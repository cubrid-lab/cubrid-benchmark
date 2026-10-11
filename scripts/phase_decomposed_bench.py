#!/usr/bin/env python3
"""Phase-decomposed micro-benchmark for pycubrid.

Measures individual operation phases (execute, fetch, commit) with
time.perf_counter_ns() precision, matching the methodology used in the
baseline run (2026-03-27_before-optimization).

This produces directly comparable numbers to the baseline:
- Connect time
- INSERT: execute + commit
- SELECT PK: execute + fetch (1 row)
- SELECT 10K: execute + fetch (10,000 rows)
- UPDATE: execute + commit
- DELETE: execute + commit

Output: JSON file with per-phase statistics.
"""

import argparse
import json
import math
import platform
import statistics
import time
from pathlib import Path

import pycubrid
from pycubrid.connection import Connection


def connect(host: str, port: int, db: str, user: str, password: str) -> Connection:
    return Connection(host=host, port=port, database=db, user=user, password=password)


def setup_table(conn: Connection, row_count: int = 10_000) -> None:
    """Create and populate bench_users table."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS bench_users")
    cur.execute("""
        CREATE TABLE bench_users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(200) NOT NULL,
            age INT NOT NULL
        )
    """)
    conn.commit()

    for i in range(1, row_count + 1):
        cur.execute(
            "INSERT INTO bench_users (id, name, email, age) VALUES (?, ?, ?, ?)",
            (i, f"user_{i}", f"user_{i}@example.com", 20 + (i % 50)),
        )
    conn.commit()
    cur.close()


def teardown_table(conn: Connection) -> None:
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS bench_users")
    conn.commit()
    cur.close()


def percentile(data: list[float], p: float) -> float:
    """Calculate percentile using linear interpolation."""
    sorted_data = sorted(data)
    n = len(sorted_data)
    k = (n - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


def compute_stats(times_ns: list[int]) -> dict:
    """Compute statistics from nanosecond measurements."""
    times_ms = [t / 1_000_000 for t in times_ns]
    mean = statistics.mean(times_ms)
    stdev = statistics.stdev(times_ms) if len(times_ms) > 1 else 0.0
    cv = (stdev / mean * 100) if mean > 0 else 0.0
    return {
        "mean_ms": round(mean, 4),
        "stdev_ms": round(stdev, 4),
        "p50_ms": round(percentile(times_ms, 50), 4),
        "p95_ms": round(percentile(times_ms, 95), 4),
        "p99_ms": round(percentile(times_ms, 99), 4),
        "cv_pct": round(cv, 2),
        "min_ms": round(min(times_ms), 4),
        "max_ms": round(max(times_ms), 4),
        "count": len(times_ms),
    }


def bench_connect(
    host: str, port: int, db: str, user: str, password: str,
    iterations: int, warmup: int,
) -> dict:
    """Measure connection time."""
    times: list[int] = []
    for i in range(warmup + iterations):
        t0 = time.perf_counter_ns()
        c = connect(host, port, db, user, password)
        t1 = time.perf_counter_ns()
        c.close()
        if i >= warmup:
            times.append(t1 - t0)
    return {"connect": compute_stats(times)}


def bench_insert(conn: Connection, iterations: int, warmup: int) -> dict:
    """Measure INSERT execute + commit phases."""
    execute_times: list[int] = []
    commit_times: list[int] = []

    for i in range(warmup + iterations):
        cur = conn.cursor()

        t0 = time.perf_counter_ns()
        cur.execute(
            "INSERT INTO bench_users (id, name, email, age) VALUES (?, ?, ?, ?)",
            (100_000 + i, f"bench_{i}", f"bench_{i}@test.com", 25),
        )
        t1 = time.perf_counter_ns()

        conn.commit()
        t2 = time.perf_counter_ns()

        cur.close()

        if i >= warmup:
            execute_times.append(t1 - t0)
            commit_times.append(t2 - t1)

        cur2 = conn.cursor()
        cur2.execute("DELETE FROM bench_users WHERE id = ?", (100_000 + i,))
        conn.commit()
        cur2.close()

    return {
        "insert_execute": compute_stats(execute_times),
        "insert_commit": compute_stats(commit_times),
        "insert_total": compute_stats(
            [e + c for e, c in zip(execute_times, commit_times)]
        ),
    }


def bench_select_pk(conn: Connection, iterations: int, warmup: int) -> dict:
    """Measure SELECT by PK (1 row): execute + fetch."""
    execute_times: list[int] = []
    fetch_times: list[int] = []

    for i in range(warmup + iterations):
        cur = conn.cursor()

        t0 = time.perf_counter_ns()
        cur.execute("SELECT * FROM bench_users WHERE id = ?", (1,))
        t1 = time.perf_counter_ns()

        cur.fetchone()
        t2 = time.perf_counter_ns()

        cur.close()

        if i >= warmup:
            execute_times.append(t1 - t0)
            fetch_times.append(t2 - t1)

    return {
        "select_pk_execute": compute_stats(execute_times),
        "select_pk_fetch": compute_stats(fetch_times),
        "select_pk_total": compute_stats(
            [e + f for e, f in zip(execute_times, fetch_times)]
        ),
    }


def bench_select_full(conn: Connection, iterations: int, warmup: int) -> dict:
    """Measure SELECT full scan (10K rows): execute + fetchall."""
    execute_times: list[int] = []
    fetch_times: list[int] = []

    for i in range(warmup + iterations):
        cur = conn.cursor()

        t0 = time.perf_counter_ns()
        cur.execute("SELECT * FROM bench_users")
        t1 = time.perf_counter_ns()

        cur.fetchall()
        t2 = time.perf_counter_ns()

        cur.close()

        if i >= warmup:
            execute_times.append(t1 - t0)
            fetch_times.append(t2 - t1)

    return {
        "select_full_execute": compute_stats(execute_times),
        "select_full_fetch": compute_stats(fetch_times),
        "select_full_total": compute_stats(
            [e + f for e, f in zip(execute_times, fetch_times)]
        ),
    }


def bench_update(conn: Connection, iterations: int, warmup: int) -> dict:
    """Measure UPDATE single row: execute + commit."""
    execute_times: list[int] = []
    commit_times: list[int] = []

    for i in range(warmup + iterations):
        cur = conn.cursor()

        t0 = time.perf_counter_ns()
        cur.execute(
            "UPDATE bench_users SET age = ? WHERE id = ?",
            (30 + (i % 20), 1),
        )
        t1 = time.perf_counter_ns()

        conn.commit()
        t2 = time.perf_counter_ns()

        cur.close()

        if i >= warmup:
            execute_times.append(t1 - t0)
            commit_times.append(t2 - t1)

    return {
        "update_execute": compute_stats(execute_times),
        "update_commit": compute_stats(commit_times),
        "update_total": compute_stats(
            [e + c for e, c in zip(execute_times, commit_times)]
        ),
    }


def bench_delete(conn: Connection, iterations: int, warmup: int) -> dict:
    """Measure DELETE single row: execute + commit."""
    execute_times: list[int] = []
    commit_times: list[int] = []

    for i in range(warmup + iterations):
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bench_users (id, name, email, age) VALUES (?, ?, ?, ?)",
            (200_000 + i, f"del_{i}", f"del_{i}@test.com", 30),
        )
        conn.commit()
        cur.close()

        cur = conn.cursor()
        t0 = time.perf_counter_ns()
        cur.execute("DELETE FROM bench_users WHERE id = ?", (200_000 + i,))
        t1 = time.perf_counter_ns()

        conn.commit()
        t2 = time.perf_counter_ns()

        cur.close()

        if i >= warmup:
            execute_times.append(t1 - t0)
            commit_times.append(t2 - t1)

    return {
        "delete_execute": compute_stats(execute_times),
        "delete_commit": compute_stats(commit_times),
        "delete_total": compute_stats(
            [e + c for e, c in zip(execute_times, commit_times)]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase-decomposed pycubrid micro-benchmark")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=33000)
    parser.add_argument("--db", default="benchdb")
    parser.add_argument("--user", default="dba")
    parser.add_argument("--password", default="")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--row-count", type=int, default=10_000)
    parser.add_argument("--output", type=str, default="phase_decomposed.json")
    args = parser.parse_args()

    print(f"Phase-decomposed benchmark: {args.iterations} iterations, "
          f"{args.warmup} warmup, {args.row_count} rows")

    print("  [1/6] Connect...")
    connect_results = bench_connect(
        args.host, args.port, args.db, args.user, args.password,
        args.iterations, args.warmup,
    )

    conn = connect(args.host, args.port, args.db, args.user, args.password)
    conn.autocommit = False

    print(f"  Setting up bench_users ({args.row_count} rows)...")
    setup_table(conn, args.row_count)

    print("  [2/6] INSERT...")
    insert_results = bench_insert(conn, args.iterations, args.warmup)

    print("  [3/6] SELECT PK...")
    select_pk_results = bench_select_pk(conn, args.iterations, args.warmup)

    print("  [4/6] SELECT Full Scan...")
    select_full_results = bench_select_full(conn, args.iterations, args.warmup)

    print("  [5/6] UPDATE...")
    update_results = bench_update(conn, args.iterations, args.warmup)

    print("  [6/6] DELETE...")
    delete_results = bench_delete(conn, args.iterations, args.warmup)

    teardown_table(conn)
    conn.close()

    results = {
        "metadata": {
            "tool": "phase_decomposed_bench.py",
            "pycubrid_version": pycubrid.__version__,
            "python_version": f"{platform.python_implementation()} {platform.python_version()}",
            "iterations": args.iterations,
            "warmup": args.warmup,
            "row_count": args.row_count,
            "timer": "time.perf_counter_ns()",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "results": {
            **connect_results,
            **insert_results,
            **select_pk_results,
            **select_full_results,
            **update_results,
            **delete_results,
        },
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nResults written to {output_path}")

    print("\n--- Summary ---")
    for key, stats in results["results"].items():
        print(f"  {key:30s}  mean={stats['mean_ms']:>8.3f}ms  "
              f"p50={stats['p50_ms']:>8.3f}ms  CV={stats['cv_pct']:.1f}%")


if __name__ == "__main__":
    main()
