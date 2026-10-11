#!/usr/bin/env python3
"""Driver-agnostic baseline benchmark: pycubrid (sync + aio) and CUBRIDdb on one CUBRID server.

Why this script exists alongside ``phase_decomposed_bench.py``:
the existing harness is pycubrid-only (it imports ``pycubrid.connection``), has no
``executemany`` scaling, no ``fetchone``/``fetchmany`` modes, no async or concurrency
case, no memory measurement and no result verification. The historical CUBRIDdb numbers
came from the external benchforge tool, which no longer ships a CUBRIDdb adapter.

The timing and percentile conventions follow ``phase_decomposed_bench.py``
(``time.perf_counter_ns()``, linear-interpolated percentiles, discarded warm-up iterations).

Every measured operation is verified (row counts and values); a run that fails verification
raises and writes no result. Raw per-iteration samples are kept in the output JSON.

Usage (one process per driver; run each driver from its own virtualenv):

    python scripts/driver_baseline_bench.py --driver pycubrid --label pycubrid-main \
        --host localhost --port 33511 --db testdb --output raw/pycubrid-main_t1.json
    python scripts/driver_baseline_bench.py --driver cubriddb --label cubriddb ...
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
import os
import platform
import random
import resource
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

TABLE = "bench797_rows"
BATCH_TABLE = "bench797_batch"
SEED_ROWS = 10_000
CREATE_SQL = (
    "CREATE TABLE {t} (id INT PRIMARY KEY, name VARCHAR(100) NOT NULL, "
    "email VARCHAR(200) NOT NULL, age INT NOT NULL)"
)
INSERT_SQL = "INSERT INTO {t} (id, name, email, age) VALUES (?, ?, ?, ?)"


def expected_row(i: int) -> tuple[int, str, str, int]:
    return (i, f"user_{i}", f"user_{i}@example.com", 20 + (i % 50))


EXPECTED = [expected_row(i) for i in range(1, SEED_ROWS + 1)]


class VerificationError(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise VerificationError(msg)


# --------------------------------------------------------------------------- stats


def percentile(data: list[float], p: float) -> float:
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def stats_ms(times_ns: list[int]) -> dict[str, float]:
    ms = [t / 1e6 for t in times_ns]
    mean = statistics.mean(ms)
    sd = statistics.stdev(ms) if len(ms) > 1 else 0.0
    q1, q3 = percentile(ms, 25), percentile(ms, 75)
    return {
        "n": len(ms),
        "median_ms": round(percentile(ms, 50), 5),
        "mean_ms": round(mean, 5),
        "stdev_ms": round(sd, 5),
        "q1_ms": round(q1, 5),
        "q3_ms": round(q3, 5),
        "iqr_ms": round(q3 - q1, 5),
        "p95_ms": round(percentile(ms, 95), 5),
        "p99_ms": round(percentile(ms, 99), 5),
        "min_ms": round(min(ms), 5),
        "max_ms": round(max(ms), 5),
        "cv_pct": round(sd / mean * 100, 2) if mean else 0.0,
    }


def summarize(times_ns: list[int], units_per_iter: int, unit: str) -> dict[str, Any]:
    st = stats_ms(times_ns)
    med_s = st["median_ms"] / 1000.0
    return {
        "stats": st,
        "throughput": {
            "unit": f"{unit}/s",
            "at_median": round(units_per_iter / med_s, 2) if med_s else None,
        },
        "samples_ns": times_ns,
    }


# --------------------------------------------------------------------------- drivers


class Driver:
    name = ""

    def __init__(self, host: str, port: int, db: str, user: str, password: str) -> None:
        self.host, self.port, self.db, self.user, self.password = host, port, db, user, password

    def connect(self) -> Any:
        raise NotImplementedError

    def version_info(self) -> dict[str, Any]:
        raise NotImplementedError


class PycubridDriver(Driver):
    name = "pycubrid"

    def connect(self, fetch_size: int | None = None) -> Any:
        import pycubrid

        kw: dict[str, Any] = {"autocommit": False}
        if fetch_size is not None:
            kw["fetch_size"] = fetch_size
        conn = pycubrid.connect(
            host=self.host, port=self.port, database=self.db, user=self.user,
            password=self.password, **kw,
        )
        # connect(autocommit=False) already leaves the session in manual-commit mode and sends
        # nothing for it. The ``autocommit`` setter would add SET_DB_PARAMETER + COMMIT round
        # trips inside the timed connect region, so it is not called here.
        check(conn.autocommit is False, "pycubrid autocommit not off")
        return conn

    async def aconnect(self) -> Any:
        import pycubrid.aio

        conn = await pycubrid.aio.connect(
            host=self.host, port=self.port, database=self.db, user=self.user,
            password=self.password, autocommit=False,
        )
        check(conn.autocommit is False, "pycubrid.aio autocommit not off")
        return conn

    def version_info(self) -> dict[str, Any]:
        import pycubrid

        return {"driver": "pycubrid", "version": pycubrid.__version__,
                "module_path": pycubrid.__file__}


class CubridDbDriver(Driver):
    name = "cubriddb"

    def connect(self, fetch_size: int | None = None) -> Any:
        import CUBRIDdb

        conn = CUBRIDdb.connect(
            f"CUBRID:{self.host}:{self.port}:{self.db}:::", self.user, self.password
        )
        conn.autocommit = False
        check(conn.autocommit is False or conn.autocommit == 0, "CUBRIDdb autocommit not off")
        return conn

    def version_info(self) -> dict[str, Any]:
        import CUBRIDdb

        try:
            from importlib.metadata import version

            dist = version("cubrid-python")
        except Exception as exc:  # noqa: BLE001 - metadata is informational only
            dist = f"unknown ({exc})"
        return {"driver": "CUBRIDdb", "version": dist, "module_path": CUBRIDdb.__file__}


# --------------------------------------------------------------------------- setup


def reset_tables(conn: Any) -> None:
    cur = conn.cursor()
    for t in (TABLE, BATCH_TABLE):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(CREATE_SQL.format(t=t))
    conn.commit()
    sql = INSERT_SQL.format(t=TABLE)
    for start in range(0, SEED_ROWS, 1000):
        cur.executemany(sql, EXPECTED[start:start + 1000])
    conn.commit()
    cur.execute(f"SELECT COUNT(*), SUM(id), SUM(age) FROM {TABLE}")
    row = tuple(cur.fetchone())
    check(row[0] == SEED_ROWS, f"seed count {row}")
    check(int(row[1]) == sum(r[0] for r in EXPECTED), f"seed sum(id) {row}")
    check(int(row[2]) == sum(r[3] for r in EXPECTED), f"seed sum(age) {row}")
    cur.close()


def verify_full_table(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(f"SELECT id, name, email, age FROM {TABLE} ORDER BY id")
    rows = [tuple(r) for r in cur.fetchall()]
    cur.close()
    check(rows == EXPECTED, "table content differs from seed")


# --------------------------------------------------------------------------- sync cases


def bench_connect(drv: Driver, iters: int, warmup: int) -> dict[str, Any]:
    # verification pass (not timed): every connection can run a query
    for _ in range(5):
        c = drv.connect()
        cur = c.cursor()
        cur.execute("SELECT 1 FROM db_root")
        check(tuple(cur.fetchone()) == (1,), "SELECT 1 after connect")
        cur.close()
        c.close()
    connect_ns, close_ns, total_ns = [], [], []
    for i in range(warmup + iters):
        t0 = time.perf_counter_ns()
        c = drv.connect()
        t1 = time.perf_counter_ns()
        c.close()
        t2 = time.perf_counter_ns()
        if i >= warmup:
            connect_ns.append(t1 - t0)
            close_ns.append(t2 - t1)
            total_ns.append(t2 - t0)
    return {
        "connect_close": summarize(total_ns, 1, "conn"),
        "connect_only": summarize(connect_ns, 1, "conn"),
        "close_only": summarize(close_ns, 1, "conn"),
    }


def bench_select_pk(conn: Any, iters: int, warmup: int) -> dict[str, Any]:
    rng = random.Random(797)
    ids = [rng.randint(1, SEED_ROWS) for _ in range(warmup + iters)]
    cur = conn.cursor()
    sql = f"SELECT id, name, email, age FROM {TABLE} WHERE id = ?"
    out = []
    for i, pk in enumerate(ids):
        t0 = time.perf_counter_ns()
        cur.execute(sql, (pk,))
        row = cur.fetchone()
        t1 = time.perf_counter_ns()
        check(tuple(row) == EXPECTED[pk - 1], f"select_pk row {pk}: {row}")
        if i >= warmup:
            out.append(t1 - t0)
    cur.close()
    conn.commit()
    return {"select_pk": summarize(out, 1, "ops") | {"verified_rows": len(ids)}}


def bench_insert(conn: Any, iters: int, warmup: int) -> dict[str, Any]:
    cur = conn.cursor()
    sql = INSERT_SQL.format(t=TABLE)
    exec_ns, total_ns = [], []
    base = 1_000_000
    n = warmup + iters
    for i in range(n):
        row = (base + i, f"ins_{i}", f"ins_{i}@example.com", 30)
        t0 = time.perf_counter_ns()
        cur.execute(sql, row)
        t1 = time.perf_counter_ns()
        conn.commit()
        t2 = time.perf_counter_ns()
        check(cur.rowcount == 1, f"insert rowcount {cur.rowcount}")
        if i >= warmup:
            exec_ns.append(t1 - t0)
            total_ns.append(t2 - t0)
    cur.execute(f"SELECT COUNT(*), SUM(id) FROM {TABLE} WHERE id >= ?", (base,))
    cnt, s = tuple(cur.fetchone())
    check(cnt == n and int(s) == sum(range(base, base + n)), f"insert verify {cnt} {s}")
    cur.execute(f"DELETE FROM {TABLE} WHERE id >= ?", (base,))
    conn.commit()
    cur.close()
    return {
        "insert_commit": summarize(total_ns, 1, "ops") | {"verified_rows": n},
        "insert_execute_only": summarize(exec_ns, 1, "ops"),
    }


def bench_update(conn: Any, iters: int, warmup: int) -> dict[str, Any]:
    cur = conn.cursor()
    exec_ns, total_ns = [], []
    n = warmup + iters
    check(n <= SEED_ROWS, "too many update iterations")
    for i in range(n):
        pk = i + 1
        t0 = time.perf_counter_ns()
        cur.execute(f"UPDATE {TABLE} SET age = ? WHERE id = ?", (1000 + pk, pk))
        t1 = time.perf_counter_ns()
        conn.commit()
        t2 = time.perf_counter_ns()
        check(cur.rowcount == 1, f"update rowcount {cur.rowcount}")
        if i >= warmup:
            exec_ns.append(t1 - t0)
            total_ns.append(t2 - t0)
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE id <= ? AND age = 1000 + id", (n,))
    check(tuple(cur.fetchone())[0] == n, "update verify")
    cur.execute(f"UPDATE {TABLE} SET age = 20 + MOD(id, 50) WHERE id <= ?", (n,))
    conn.commit()
    cur.close()
    return {
        "update_commit": summarize(total_ns, 1, "ops") | {"verified_rows": n},
        "update_execute_only": summarize(exec_ns, 1, "ops"),
    }


def bench_delete(conn: Any, iters: int, warmup: int) -> dict[str, Any]:
    cur = conn.cursor()
    n = warmup + iters
    base = 2_000_000
    rows = [(base + i, f"del_{i}", f"del_{i}@example.com", 31) for i in range(n)]
    cur.executemany(INSERT_SQL.format(t=TABLE), rows)
    conn.commit()
    exec_ns, total_ns = [], []
    for i in range(n):
        t0 = time.perf_counter_ns()
        cur.execute(f"DELETE FROM {TABLE} WHERE id = ?", (base + i,))
        t1 = time.perf_counter_ns()
        conn.commit()
        t2 = time.perf_counter_ns()
        check(cur.rowcount == 1, f"delete rowcount {cur.rowcount}")
        if i >= warmup:
            exec_ns.append(t1 - t0)
            total_ns.append(t2 - t0)
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE id >= ?", (base,))
    check(tuple(cur.fetchone())[0] == 0, "delete verify")
    cur.close()
    return {
        "delete_commit": summarize(total_ns, 1, "ops") | {"verified_rows": n},
        "delete_execute_only": summarize(exec_ns, 1, "ops"),
    }


def _batch_rows(size: int) -> list[tuple[int, str, str, int]]:
    return [expected_row(i) for i in range(1, size + 1)]


def bench_executemany(conn: Any, size: int, iters: int, warmup: int) -> dict[str, Any]:
    cur = conn.cursor()
    rows = _batch_rows(size)
    sql = INSERT_SQL.format(t=BATCH_TABLE)
    out = []
    exp_sum = sum(r[0] for r in rows)
    for i in range(warmup + iters):
        t0 = time.perf_counter_ns()
        cur.executemany(sql, rows)
        conn.commit()
        t1 = time.perf_counter_ns()
        cur.execute(f"SELECT COUNT(*), SUM(id), SUM(age) FROM {BATCH_TABLE}")
        cnt, s, a = tuple(cur.fetchone())
        check(cnt == size and int(s) == exp_sum and int(a) == sum(r[3] for r in rows),
              f"executemany {size} verify {cnt}")
        cur.execute(f"DELETE FROM {BATCH_TABLE}")
        conn.commit()
        if i >= warmup:
            out.append(t1 - t0)
    cur.close()
    return {f"executemany_{size}": summarize(out, size, "rows") | {
        "verified_rows_per_iter": size}}


def _fetch_once(conn: Any, mode: str, many: int = 100) -> list[tuple[Any, ...]]:
    cur = conn.cursor()
    cur.execute(f"SELECT id, name, email, age FROM {TABLE} ORDER BY id")
    rows: list[Any] = []
    if mode == "fetchall":
        rows = cur.fetchall()
    elif mode == "fetchmany":
        while True:
            chunk = cur.fetchmany(many)
            if not chunk:
                break
            rows.extend(chunk)
    elif mode == "fetchone":
        while True:
            r = cur.fetchone()
            if r is None:
                break
            rows.append(r)
    cur.close()
    return rows


def bench_fetch(conn: Any, mode: str, iters: int, warmup: int, tag: str = "") -> dict[str, Any]:
    out = []
    for i in range(warmup + iters):
        t0 = time.perf_counter_ns()
        rows = _fetch_once(conn, mode)
        t1 = time.perf_counter_ns()
        check(len(rows) == SEED_ROWS and [tuple(r) for r in rows] == EXPECTED,
              f"{mode} verify ({len(rows)} rows)")
        del rows
        if i >= warmup:
            out.append(t1 - t0)
    conn.commit()
    return {f"{mode}_{SEED_ROWS}{tag}": summarize(out, SEED_ROWS, "rows") | {
        "verified_rows_per_iter": SEED_ROWS}}


def traced_peak(fn: Callable[[], Any]) -> int:
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        res = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del res
    return peak


def memory_pass(conn: Any, drv: Driver, reps: int) -> dict[str, Any]:
    mem: dict[str, Any] = {}

    def em(size: int) -> Callable[[], Any]:
        rows = _batch_rows(size)

        def run() -> None:
            cur = conn.cursor()
            cur.executemany(INSERT_SQL.format(t=BATCH_TABLE), rows)
            conn.commit()
            cur.close()
        return run

    for size in (100, 1000, 10000):
        peaks = []
        for _ in range(reps):
            peaks.append(traced_peak(em(size)))
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {BATCH_TABLE}")
            check(tuple(cur.fetchone())[0] == size, "mem executemany verify")
            cur.execute(f"DELETE FROM {BATCH_TABLE}")
            conn.commit()
            cur.close()
        mem[f"executemany_{size}"] = {"tracemalloc_peak_bytes": peaks,
                                      "median_bytes": int(statistics.median(peaks))}
    for mode in ("fetchone", "fetchmany", "fetchall"):
        peaks = []
        for _ in range(reps):
            holder: dict[str, Any] = {}

            if mode == "fetchall":
                def run() -> None:
                    holder["n"] = len(_fetch_once(conn, "fetchall"))
                peaks.append(traced_peak(run))
            else:
                # streaming modes discard rows as they arrive, so the peak is the
                # driver's own buffering rather than the caller's result list
                peaks.append(traced_peak(lambda m=mode: _stream_discard(conn, m, holder)))
            check(holder["n"] == SEED_ROWS, f"mem {mode} verify")
        mem[f"{mode}_{SEED_ROWS}"] = {"tracemalloc_peak_bytes": peaks,
                                      "median_bytes": int(statistics.median(peaks)),
                                      "note": "streaming modes discard rows as they arrive"
                                      if mode != "fetchall" else "fetchall keeps all rows"}
    conn.commit()
    return mem


def _stream_discard(conn: Any, mode: str, holder: dict[str, Any]) -> None:
    cur = conn.cursor()
    cur.execute(f"SELECT id, name, email, age FROM {TABLE} ORDER BY id")
    n = 0
    s = 0
    if mode == "fetchone":
        while (r := cur.fetchone()) is not None:
            n += 1
            s += r[0]
    else:
        while chunk := cur.fetchmany(100):
            n += len(chunk)
            s += sum(r[0] for r in chunk)
    cur.close()
    check(s == SEED_ROWS * (SEED_ROWS + 1) // 2, "stream sum")
    holder["n"] = n


# --------------------------------------------------------------------------- concurrency

PK_SQL = f"SELECT id, name, email, age FROM {TABLE} WHERE id = ?"


def _ids_for(worker: int, n: int) -> list[int]:
    rng = random.Random(10_000 + worker)
    return [rng.randint(1, SEED_ROWS) for _ in range(n)]


def bench_threads(drv: Driver, conc: int, per_worker: int, rounds: int,
                  warmup_rounds: int) -> dict[str, Any]:
    conns = [drv.connect() for _ in range(conc)]
    wall: list[int] = []
    lat: list[int] = []
    try:
        for rnd in range(warmup_rounds + rounds):
            barrier = threading.Barrier(conc + 1)
            lats: list[list[int]] = [[] for _ in range(conc)]
            errors: list[BaseException] = []

            def work(w: int) -> None:
                try:
                    cur = conns[w].cursor()
                    ids = _ids_for(w * 1000 + rnd, per_worker)
                    barrier.wait()
                    for pk in ids:
                        t0 = time.perf_counter_ns()
                        cur.execute(PK_SQL, (pk,))
                        row = cur.fetchone()
                        lats[w].append(time.perf_counter_ns() - t0)
                        check(tuple(row) == EXPECTED[pk - 1], "thread row")
                    cur.close()
                    conns[w].commit()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    errors.append(exc)
                    try:
                        barrier.abort()
                    except Exception:  # noqa: BLE001
                        pass

            ths = [threading.Thread(target=work, args=(w,)) for w in range(conc)]
            for t in ths:
                t.start()
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass  # a worker failed; join every thread, then raise its error below
            t0 = time.perf_counter_ns()
            for t in ths:
                t.join()
            t1 = time.perf_counter_ns()
            if errors:
                # workers released by barrier.abort() report BrokenBarrierError; raise the cause
                raise next((e for e in errors
                            if not isinstance(e, threading.BrokenBarrierError)), errors[0])
            check(not barrier.broken, "thread barrier broken without a worker error")
            check(sum(len(x) for x in lats) == conc * per_worker, "thread count")
            if rnd >= warmup_rounds:
                wall.append(t1 - t0)
                for x in lats:
                    lat.extend(x)
    finally:
        for c in conns:
            c.close()
    total = conc * per_worker
    return {
        "mode": "sync-threads", "concurrency": conc, "queries_per_round": total,
        "round_wall": summarize(wall, total, "queries"),
        "query_latency": {"stats": stats_ms(lat)},
        "verified_rows": total * rounds,
    }


async def _bench_async(drv: PycubridDriver, conc: int, per_worker: int, rounds: int,
                       warmup_rounds: int) -> dict[str, Any]:
    conns = [await drv.aconnect() for _ in range(conc)]
    wall: list[int] = []
    lat: list[int] = []
    try:
        for rnd in range(warmup_rounds + rounds):
            lats: list[list[int]] = [[] for _ in range(conc)]
            start = asyncio.Event()

            async def work(w: int) -> None:
                cur = conns[w].cursor()
                ids = _ids_for(w * 1000 + rnd, per_worker)
                await start.wait()
                for pk in ids:
                    t0 = time.perf_counter_ns()
                    await cur.execute(PK_SQL, (pk,))
                    row = await cur.fetchone()
                    lats[w].append(time.perf_counter_ns() - t0)
                    check(tuple(row) == EXPECTED[pk - 1], "async row")
                await cur.close()
                await conns[w].commit()

            tasks = [asyncio.create_task(work(w)) for w in range(conc)]
            await asyncio.sleep(0)
            t0 = time.perf_counter_ns()
            start.set()
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                # stop the other workers before their connections are closed below
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            t1 = time.perf_counter_ns()
            check(sum(len(x) for x in lats) == conc * per_worker, "async count")
            if rnd >= warmup_rounds:
                wall.append(t1 - t0)
                for x in lats:
                    lat.extend(x)
    finally:
        for c in conns:
            await c.close()
    total = conc * per_worker
    return {
        "mode": "async-tasks", "concurrency": conc, "queries_per_round": total,
        "round_wall": summarize(wall, total, "queries"),
        "query_latency": {"stats": stats_ms(lat)},
        "verified_rows": total * rounds,
    }


# --------------------------------------------------------------------------- env


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as exc:  # noqa: BLE001 - informational
        return f"unavailable ({exc})"


def environment() -> dict[str, Any]:
    cpu = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "python_executable": sys.executable,
        "os": platform.platform(),
        "kernel": platform.release(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "loadavg_at_start": os.getloadavg(),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# --------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--driver", choices=["pycubrid", "cubriddb"], required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=33000)
    ap.add_argument("--db", default="testdb")
    ap.add_argument("--user", default="dba")
    ap.add_argument("--password", default="")
    ap.add_argument("--trial", type=int, default=1)
    ap.add_argument("--driver-commit", default="", help="source commit of the driver build")
    ap.add_argument("--quick", action="store_true", help="tiny iteration counts (smoke test)")
    ap.add_argument("--cases", choices=["all", "connect"], default="all",
                    help="'connect' runs only the connect/close cases (re-measurement)")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    q = args.quick
    it = (lambda full, quick: quick if q else full)  # noqa: E731
    drv: Driver = (PycubridDriver if args.driver == "pycubrid" else CubridDbDriver)(
        args.host, args.port, args.db, args.user, args.password)

    t_start = time.time()
    res: dict[str, Any] = {}
    log = lambda m: print(f"[{args.label} t{args.trial}] {m}", flush=True)  # noqa: E731

    log("connect/close")
    res.update(bench_connect(drv, it(200, 5), it(20, 2)))
    conc_res: list[dict[str, Any]] = []
    mem: dict[str, Any] = {}
    if args.cases == "all":
        conn = drv.connect()
        try:
            log("seed")
            reset_tables(conn)
            log("select_pk")
            res.update(bench_select_pk(conn, it(1000, 10), it(100, 2)))
            log("insert")
            res.update(bench_insert(conn, it(300, 5), it(30, 2)))
            log("update")
            res.update(bench_update(conn, it(300, 5), it(30, 2)))
            log("delete")
            res.update(bench_delete(conn, it(300, 5), it(30, 2)))
            verify_full_table(conn)
            for size, n in ((100, 50), (1000, 20), (10000, 10)):
                log(f"executemany {size}")
                res.update(bench_executemany(conn, size, it(n, 2), it(3, 1)))
            for mode in ("fetchone", "fetchmany", "fetchall"):
                log(f"{mode}")
                res.update(bench_fetch(conn, mode, it(30, 2), it(3, 1)))
            verify_full_table(conn)
        finally:
            conn.close()

        if args.driver == "pycubrid":
            for fs in (1000, 10000):
                log(f"fetch_size={fs}")
                c = drv.connect(fetch_size=fs)
                try:
                    for mode in ("fetchmany", "fetchall"):
                        res.update(bench_fetch(c, mode, it(30, 2), it(3, 1), tag=f"_fs{fs}"))
                finally:
                    c.close()

        for conc in (1, 8, 32):
            log(f"threads c={conc}")
            conc_res.append(bench_threads(drv, conc, it(200, 5), it(5, 1), 1))
            if args.driver == "pycubrid":
                log(f"async c={conc}")
                conc_res.append(asyncio.run(_bench_async(drv, conc, it(200, 5), it(5, 1), 1)))

        log("memory")
        conn = drv.connect()
        try:
            mem = memory_pass(conn, drv, it(3, 1))
            verify_full_table(conn)
            cur = conn.cursor()
            for t in (TABLE, BATCH_TABLE):
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            conn.commit()
            cur.close()
        finally:
            conn.close()

    out = {
        "metadata": {
            "tool": "scripts/driver_baseline_bench.py",
            "label": args.label,
            "trial": args.trial,
            "driver": drv.version_info() | {"commit": args.driver_commit},
            "server": {"host": args.host, "port": args.port, "db": args.db},
            "transaction_mode": "autocommit=False on every connection (pycubrid: "
                                "connect(autocommit=False), no setter call; CUBRIDdb: "
                                "client-side setter); explicit commit per write op "
                                "(single-row DML timed as execute+commit)",
            "cases": args.cases,
            "seed_rows": SEED_ROWS,
            "timer": "time.perf_counter_ns()",
            "quick": q,
            "environment": environment(),
            "ru_maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "elapsed_s": round(time.time() - t_start, 1),
            "verification": "all measured iterations verified (row counts and values)",
        },
        "results": res,
        "concurrency": conc_res,
        "memory": mem,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out) + "\n")
    log(f"done in {out['metadata']['elapsed_s']}s -> {args.output}")


if __name__ == "__main__":
    main()
