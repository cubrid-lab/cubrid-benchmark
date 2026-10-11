#!/usr/bin/env python3
"""executemany() overhead investigation harness (cubrid-lab/pycubrid#822).

Stages (``--stage``):

* ``compare``   end-to-end wall time (statement phase + commit phase, both timed) of the same
                DML batch through several execution paths (see VARIANTS), for INSERT, UPDATE,
                DELETE and MERGE. Every iteration verifies the resulting table contents.
* ``phases``    pycubrid only. Splits one batch into render / packet write / socket send /
                server wait / receive / parse / residual using in-process monkeypatching
                (socket proxy + packet wrappers). Also measures the empty-request round trip.
* ``profile``   pycubrid only. cProfile of ``executemany`` (10,000 rows by default).
* ``memory``    pycubrid only. tracemalloc peak (same method as driver_baseline_bench.py)
                plus allocation snapshots at the points where the peak is reached.
* ``serverlog`` Runs each variant on a fresh connection that first executes a unique marker
                statement, so the broker SQL log (SQL_LOG=ON) can be sliced per run.

The driver is never modified; only the harness process is patched (``phases``/``memory``).
Python code must run under ``python -I`` from a venv holding only the driver under test.
"""

from __future__ import annotations

import argparse
import cProfile
import gc
import io
import json
import math
import os
import platform
import pstats
import statistics
import sys
import time
import tracemalloc
from typing import Any, Callable

SEED_ROWS = 10_000
SEED = "b822_seed"
TABLES = {"insert": "b822_ins", "update": "b822_upd", "delete": "b822_del", "merge": "b822_mrg"}
CREATE_SQL = (
    "CREATE TABLE {t} (id INT PRIMARY KEY, name VARCHAR(100) NOT NULL, "
    "email VARCHAR(200) NOT NULL, age INT NOT NULL)"
)
MERGE_BONUS = 1000
pc = time.perf_counter_ns


def seed_row(i: int) -> tuple[int, str, str, int]:
    return (i, f"user_{i}", f"user_{i}@example.com", 20 + (i % 50))


def new_update_age(i: int) -> int:
    return 100 + (i % 50)


class VerificationError(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise VerificationError(msg)


# --------------------------------------------------------------------------- statistics


def percentile(data: list[float], p: float) -> float:
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    return s[int(k)] if f == c else s[f] * (c - k) + s[c] * (k - f)


def stats_ms(ns: list[int]) -> dict[str, float]:
    ms = [t / 1e6 for t in ns]
    q1, q3 = percentile(ms, 25), percentile(ms, 75)
    return {
        "n": len(ms),
        "median_ms": percentile(ms, 50),
        "mean_ms": statistics.mean(ms),
        "stdev_ms": statistics.stdev(ms) if len(ms) > 1 else 0.0,
        "q1_ms": q1,
        "q3_ms": q3,
        "min_ms": min(ms),
        "max_ms": max(ms),
    }


# --------------------------------------------------------------------------- connections


class Conn:
    """Thin driver-neutral wrapper: ``raw`` is the DB-API connection."""

    def __init__(self, driver: str, host: str, port: int, db: str, user: str, pw: str) -> None:
        self.driver, self.host, self.port, self.db, self.user, self.pw = (
            driver,
            host,
            port,
            db,
            user,
            pw,
        )

    def open(self) -> Any:
        if self.driver == "pycubrid":
            import pycubrid

            return pycubrid.connect(
                host=self.host,
                port=self.port,
                database=self.db,
                user=self.user,
                password=self.pw,
                autocommit=False,
            )
        import CUBRIDdb

        c = CUBRIDdb.connect(f"CUBRID:{self.host}:{self.port}:{self.db}:::", self.user, self.pw)
        c.autocommit = False
        return c

    def open_native(self) -> Any:
        import pycubrid.compat.native as nat

        n = nat.connection(f"CUBRID:{self.host}:{self.port}:{self.db}:::", self.user, self.pw)
        # compat.native defaults to autocommit=True and has no public manual mode yet
        # (#467); the private driver flag is the one the driver's own tests use.
        n._driver.autocommit = False
        return n


# --------------------------------------------------------------------------- workload


def ddl(conn: Any) -> None:
    cur = conn.cursor()
    for t in (SEED, *TABLES.values()):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(CREATE_SQL.format(t=t))
    conn.commit()
    for start in range(1, SEED_ROWS + 1, 1000):
        cur.executemany(
            f"INSERT INTO {SEED} (id, name, email, age) VALUES (?, ?, ?, ?)",
            [seed_row(i) for i in range(start, start + 1000)],
        )
    conn.commit()
    for t in (TABLES["update"], TABLES["delete"]):
        cur.execute(f"INSERT INTO {t} SELECT * FROM {SEED}")
    conn.commit()
    cur.execute(f"SELECT COUNT(*), SUM(id), SUM(age) FROM {SEED}")
    row = tuple(cur.fetchone())
    check(row[0] == SEED_ROWS and int(row[1]) == sum(range(1, SEED_ROWS + 1)), f"seed {row}")
    cur.close()


class Op:
    """One DML operation: SQL, parameters, per-iteration reset and verification."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.table = TABLES[name]
        t = self.table
        if name == "insert":
            self.sql = f"INSERT INTO {t} (id, name, email, age) VALUES (?, ?, ?, ?)"
        elif name == "update":
            self.sql = f"UPDATE {t} SET age = ? WHERE id = ?"
        elif name == "delete":
            self.sql = f"DELETE FROM {t} WHERE id = ?"
        else:
            self.sql = (
                f"MERGE INTO {t} t USING (SELECT CAST(? AS INT) AS id, "
                "CAST(? AS VARCHAR(100)) AS name, CAST(? AS VARCHAR(200)) AS email, "
                "CAST(? AS INT) AS age FROM db_root) s ON (t.id = s.id) "
                "WHEN MATCHED THEN UPDATE SET t.age = s.age "
                "WHEN NOT MATCHED THEN INSERT (id, name, email, age) "
                "VALUES (s.id, s.name, s.email, s.age)"
            )

    def params(self, n: int) -> list[tuple[Any, ...]]:
        if self.name == "insert":
            return [seed_row(i) for i in range(1, n + 1)]
        if self.name == "update":
            return [(new_update_age(i), i) for i in range(1, n + 1)]
        if self.name == "delete":
            return [(i,) for i in range(1, n + 1)]
        return [
            (i, f"user_{i}", f"user_{i}@example.com", seed_row(i)[3] + MERGE_BONUS)
            for i in range(1, n + 1)
        ]

    def full_reset(self, ctl: Any) -> None:
        """Restore the whole table to its initial state (untimed, once per op/size)."""
        cur = ctl.cursor()
        t = self.table
        if self.name in ("insert", "merge"):
            cur.execute(f"DELETE FROM {t}")
        else:
            cur.execute(f"DELETE FROM {t}")
            cur.execute(f"INSERT INTO {t} SELECT * FROM {SEED}")
        ctl.commit()
        cur.close()

    def reset(self, ctl: Any, n: int) -> None:
        """Bring the table back to the pre-batch state (untimed)."""
        cur = ctl.cursor()
        t = self.table
        if self.name == "insert":
            cur.execute(f"DELETE FROM {t}")
        elif self.name == "update":
            cur.execute(f"UPDATE {t} SET age = MOD(id, 50) + 20 WHERE id <= {n}")
        elif self.name == "delete":
            cur.execute(f"DELETE FROM {t} WHERE id <= {n}")
            cur.execute(f"INSERT INTO {t} SELECT * FROM {SEED} WHERE id <= {n}")
        else:
            cur.execute(f"DELETE FROM {t}")
            cur.execute(f"INSERT INTO {t} SELECT * FROM {SEED} WHERE id <= {n // 2}")
        ctl.commit()
        cur.close()

    def verify(self, ctl: Any, n: int, label: str) -> None:
        cur = ctl.cursor()
        cur.execute(f"SELECT COUNT(*), SUM(id), SUM(age) FROM {self.table}")
        cnt, sid, sage = (int(x or 0) for x in tuple(cur.fetchone()))
        ctl.commit()
        cur.close()
        ages = [seed_row(i)[3] for i in range(1, SEED_ROWS + 1)]
        if self.name == "insert":
            exp = (n, n * (n + 1) // 2, sum(ages[:n]))
        elif self.name == "update":
            exp = (
                SEED_ROWS,
                SEED_ROWS * (SEED_ROWS + 1) // 2,
                sum(new_update_age(i) for i in range(1, n + 1)) + sum(ages[n:]),
            )
        elif self.name == "delete":
            exp = (SEED_ROWS - n, sum(range(n + 1, SEED_ROWS + 1)), sum(ages[n:]))
        else:
            exp = (n, n * (n + 1) // 2, sum(ages[:n]) + MERGE_BONUS * n)
        check(
            (cnt, sid, sage) == exp, f"{self.name} {n} {label}: got {(cnt, sid, sage)} want {exp}"
        )


# --------------------------------------------------------------------------- variants
# Each factory returns (run, close). run(rows) executes the batch (statement phase only).

VARIANTS_PY = ["executemany", "loop_execute", "batch_prerendered", "native_prepared"]
VARIANTS_DB = ["executemany", "loop_execute"]


def make_runner(
    variant: str, driver: str, conn: Any, native: Any, op: Op, n: int, rows: list[tuple[Any, ...]]
) -> Callable[[], Any]:
    """Return a zero-arg callable doing the *timed* statement phase.

    Setup that is not part of the variant's per-call cost (pre-rendering, cursor creation)
    happens here, outside the timer.
    """
    sql = op.sql
    if variant == "executemany":
        cur = conn.cursor()
        return lambda: cur.executemany(sql, rows)
    if variant == "loop_execute":
        cur = conn.cursor()

        def loop() -> None:
            ex = cur.execute
            for p in rows:
                ex(sql, p)

        return loop
    if variant == "batch_prerendered":
        cur = conn.cursor()
        sql_list = [cur._bind_parameters(sql, p) for p in rows]
        return lambda: cur.executemany_batch(sql_list, auto_commit=False)
    if variant == "native_prepared":
        ncur = native.cursor()

        def prepared() -> None:
            ncur.prepare(sql)
            bind, execute = ncur.bind_param, ncur.execute
            for p in rows:
                for k, v in enumerate(p, 1):
                    bind(k, v)
                execute()

        return prepared
    raise ValueError(variant)


def commit_fn(variant: str, conn: Any, native: Any) -> Callable[[], Any]:
    return native.commit if variant == "native_prepared" else conn.commit


# --------------------------------------------------------------------------- stage: compare


def stage_compare(a: argparse.Namespace, c: Conn) -> dict[str, Any]:
    worker, ctl = c.open(), c.open()
    native = c.open_native() if a.driver == "pycubrid" else None
    variants = a.variants or (VARIANTS_PY if a.driver == "pycubrid" else VARIANTS_DB)
    sizes = [int(x) for x in a.sizes.split(",")]
    iters = {100: (a.iters_100, 3), 1000: (a.iters_1000, 2), 10000: (a.iters_10000, 1)}
    res: dict[str, Any] = {}
    for opname in a.ops.split(","):
        op = Op(opname)
        for n in sizes:
            nit, warm = iters.get(n, (5, 1))
            if a.quick:
                nit, warm = 2, 1
            rows = op.params(n)
            op.full_reset(ctl)
            order = (
                variants[(a.trial - 1) % len(variants) :]
                + variants[: (a.trial - 1) % len(variants)]
            )
            for v in order:
                total, ex_ns, cm_ns, rc = [], [], [], []
                for i in range(warm + nit):
                    op.reset(ctl, n)
                    run = make_runner(v, a.driver, worker, native, op, n, rows)
                    commit = commit_fn(v, worker, native)
                    gc.collect()
                    t0 = pc()
                    run()
                    t1 = pc()
                    commit()
                    t2 = pc()
                    op.verify(ctl, n, v)
                    if i >= warm:
                        total.append(t2 - t0)
                        ex_ns.append(t1 - t0)
                        cm_ns.append(t2 - t1)
                        if v != "native_prepared":
                            rc.append(getattr(_last_cursor(run), "rowcount", None))
                key = f"{opname}/{n}/{v}"
                res[key] = {
                    "op": opname,
                    "rows": n,
                    "variant": v,
                    "total_ns": total,
                    "stmt_ns": ex_ns,
                    "commit_ns": cm_ns,
                    "rowcount": rc,
                    "stats": stats_ms(total),
                    "verified": True,
                }
                log(
                    f"{key}: median {res[key]['stats']['median_ms']:.1f} ms "
                    f"(commit {statistics.median(cm_ns) / 1e6:.1f})"
                )
    return res


def _last_cursor(run: Callable[[], Any]) -> Any:
    # the runner closes over its cursor in closure cell 0 (executemany/loop/batch variants)
    try:
        cell = run.__closure__[0].cell_contents  # type: ignore[index]
        return cell if hasattr(cell, "rowcount") else None
    except Exception:  # noqa: BLE001 - informational only
        return None


# --------------------------------------------------------------------------- stage: phases


class TimedSock:
    """Socket proxy: accumulates send time, server wait and receive time."""

    def __init__(self, sock: Any, acc: dict[str, int]) -> None:
        self._s, self.acc = sock, acc
        self._sent_at = 0
        self._first = False

    def sendall(self, data: Any) -> Any:
        t0 = pc()
        r = self._s.sendall(data)
        t1 = pc()
        self.acc["send"] += t1 - t0
        self.acc["bytes_out"] += len(data)
        self.acc["round_trips"] += 1
        self._sent_at, self._first = t1, True
        return r

    def recv_into(self, buf: Any, nbytes: int = 0) -> int:
        t0 = pc()
        r = self._s.recv_into(buf, nbytes)
        t1 = pc()
        if self._first:
            self.acc["wait"] += t1 - self._sent_at  # sendall end -> first reply bytes
            self._first = False
        else:
            self.acc["recv_rest"] += t1 - t0
        self.acc["bytes_in"] += r
        return r

    def __getattr__(self, name: str) -> Any:
        return getattr(self._s, name)


def install_hooks(acc: dict[str, int]) -> list[Callable[[], None]]:
    """Wrap packet write/parse and Cursor._bind_parameters; return undo callables."""
    import pycubrid.cursor as cursor_mod
    import pycubrid.protocol as proto

    undo: list[Callable[[], None]] = []

    def wrap(cls: Any, name: str, key: str) -> None:
        orig = getattr(cls, name)

        def wrapper(self: Any, *args: Any, **kw: Any) -> Any:
            t0 = pc()
            try:
                return orig(self, *args, **kw)
            finally:
                acc[key] += pc() - t0

        setattr(cls, name, wrapper)
        undo.append(lambda: setattr(cls, name, orig))

    for cls in (proto.BatchExecutePacket, proto.PrepareAndExecutePacket, proto.ExecutePacket):
        wrap(cls, "write", "pkt_write")
        wrap(cls, "parse", "pkt_parse")
    wrap(cursor_mod.Cursor, "_bind_parameters", "render_insitu")
    return undo


KEYS = [
    "render_insitu",
    "pkt_write",
    "send",
    "wait",
    "recv_rest",
    "pkt_parse",
    "bytes_out",
    "bytes_in",
    "round_trips",
]


def stage_phases(a: argparse.Namespace, c: Conn) -> dict[str, Any]:
    worker, ctl = c.open(), c.open()
    native = c.open_native()
    acc: dict[str, int] = {k: 0 for k in KEYS}
    undo = install_hooks(acc)
    worker._socket = TimedSock(worker._socket, acc)
    native._driver._socket = TimedSock(native._driver._socket, acc)
    res: dict[str, Any] = {}
    # empty-request round trip (CHECK_CAS): server-side work ~ 0, so wait ~ loopback RTT + CAS
    from pycubrid.protocol import CheckCasPacket

    rtts = []
    for i in range(300):
        for k in acc:
            acc[k] = 0
        worker._send_and_receive(CheckCasPacket())
        if i >= 20:
            rtts.append(acc["wait"] + acc["send"] + acc["recv_rest"])
    res["empty_request_roundtrip_ns"] = rtts
    log(f"empty request round trip median {statistics.median(rtts) / 1e3:.1f} us")

    op = Op("insert")
    iters = {100: 20, 1000: 10, 10000: 5}
    for n in [int(x) for x in a.sizes.split(",")]:
        rows = op.params(n)
        op.full_reset(ctl)
        nit = 2 if a.quick else iters.get(n, 5)
        for v in ["executemany", "loop_execute", "native_prepared"]:
            recs = []
            for i in range(nit + 1):
                op.reset(ctl, n)
                run = make_runner(v, "pycubrid", worker, native, op, n, rows)
                commit = commit_fn(v, worker, native)
                gc.collect()
                for k in acc:
                    acc[k] = 0
                t0 = pc()
                run()
                t1 = pc()
                snap = dict(acc)
                commit()
                t2 = pc()
                op.verify(ctl, n, "phases " + v)
                standalone = 0
                if v == "executemany":
                    cur = worker.cursor()
                    s0 = pc()
                    _ = [cur._bind_parameters(op.sql, p) for p in rows]
                    standalone = pc() - s0
                if i >= 1:
                    known = sum(
                        snap[k]
                        for k in (
                            "render_insitu",
                            "pkt_write",
                            "send",
                            "wait",
                            "recv_rest",
                            "pkt_parse",
                        )
                    )
                    recs.append(
                        {
                            "stmt_ns": t1 - t0,
                            "commit_ns": t2 - t1,
                            "residual_ns": (t1 - t0) - known,
                            "render_standalone_ns": standalone,
                            **snap,
                        }
                    )
            res[f"{n}/{v}"] = {"rows": n, "variant": v, "iterations": recs}
            med = statistics.median(r["stmt_ns"] for r in recs) / 1e6
            log(f"phases {n}/{v}: stmt median {med:.1f} ms")
    for u in undo:
        u()
    return res


# --------------------------------------------------------------------------- stage: profile


def stage_profile(a: argparse.Namespace, c: Conn) -> dict[str, Any]:
    worker, ctl = c.open(), c.open()
    n = int(a.sizes.split(",")[-1])
    op = Op("insert")
    rows = op.params(n)
    cur = worker.cursor()
    op.reset(ctl, n)
    cur.executemany(op.sql, rows)  # warm-up
    worker.commit()
    reps = 3
    prof = cProfile.Profile()
    wall = []
    for _ in range(reps):
        op.reset(ctl, n)
        gc.collect()
        t0 = pc()
        prof.enable()
        cur.executemany(op.sql, rows)
        prof.disable()
        wall.append(pc() - t0)
        worker.commit()
        op.verify(ctl, n, "profile")
    # same call without profiler for the overhead factor
    plain = []
    for _ in range(reps):
        op.reset(ctl, n)
        t0 = pc()
        cur.executemany(op.sql, rows)
        plain.append(pc() - t0)
        worker.commit()
    out = {}
    for sort in ("cumulative", "tottime"):
        s = io.StringIO()
        st = pstats.Stats(prof, stream=s).strip_dirs().sort_stats(sort)
        st.print_stats(30)
        out[sort] = s.getvalue()
    prof.dump_stats(a.output.replace(".json", ".prof"))
    return {
        "rows": n,
        "reps": reps,
        "profiled_wall_ns": wall,
        "unprofiled_wall_ns": plain,
        "pstats": out,
    }


# --------------------------------------------------------------------------- stage: memory


def stage_memory(a: argparse.Namespace, c: Conn) -> dict[str, Any]:
    import pycubrid.cursor as cursor_mod
    import pycubrid.packet as packet_mod

    worker, ctl = c.open(), c.open()
    op = Op("insert")
    res: dict[str, Any] = {}
    for n in (100, 1000, 10000):
        rows = op.params(n)
        peaks = []
        for _ in range(a.mem_reps):
            op.reset(ctl, n)
            cur = worker.cursor()
            gc.collect()
            tracemalloc.start()
            tracemalloc.reset_peak()
            cur.executemany(op.sql, rows)
            worker.commit()
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            cur.close()
            op.verify(ctl, n, "memory")
            peaks.append(peak)
        res[f"peak_{n}"] = {
            "peaks": peaks,
            "median": int(statistics.median(peaks)),
            "bytes_per_row": statistics.median(peaks) / n,
        }
        log(f"memory {n}: median peak {statistics.median(peaks):.0f} B")

    # allocation breakdown at the stages leading to the peak, 10,000 rows
    n = 10000
    rows = op.params(n)
    stages: dict[str, Any] = {}
    orig_batch = cursor_mod.Cursor._executemany_batch
    orig_fin = packet_mod.PacketWriter.finalize

    def top(snap: Any, k: int = 12) -> list[dict[str, Any]]:
        return [
            {"where": str(s.traceback[0]), "bytes": s.size, "count": s.count}
            for s in snap.statistics("lineno")[:k]
        ]

    def hook_batch(self: Any, sql_list: Any, *args: Any, **kw: Any) -> Any:
        cur_b, _ = tracemalloc.get_traced_memory()
        stages["after_render"] = {"current_bytes": cur_b, "top": top(tracemalloc.take_snapshot())}
        return orig_batch(self, sql_list, *args, **kw)

    def hook_fin(self: Any, cas_info: Any) -> Any:
        if len(self._buffer) > 100_000:
            cur_b, _ = tracemalloc.get_traced_memory()
            stages["before_finalize"] = {
                "current_bytes": cur_b,
                "top": top(tracemalloc.take_snapshot()),
            }
        return orig_fin(self, cas_info)

    cursor_mod.Cursor._executemany_batch = hook_batch
    packet_mod.PacketWriter.finalize = hook_fin
    try:
        op.reset(ctl, n)
        cur = worker.cursor()
        gc.collect()
        tracemalloc.start()
        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        cur.executemany(op.sql, rows)
        end_cur, peak = tracemalloc.get_traced_memory()
        worker.commit()
        tracemalloc.stop()
    finally:
        cursor_mod.Cursor._executemany_batch = orig_batch
        packet_mod.PacketWriter.finalize = orig_fin
    sample = _bind_sample(worker, op, rows)
    res["breakdown_10000"] = {
        "baseline_bytes": base,
        "peak_bytes": peak,
        "end_bytes": end_cur,
        "stages": stages,
        **sample,
    }
    return res


def _bind_sample(worker: Any, op: Op, rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    cur = worker.cursor()
    s = cur._bind_parameters(op.sql, rows[-1])
    return {
        "sample_sql": s,
        "sample_sql_chars": len(s),
        "sample_sql_sys_getsizeof": sys.getsizeof(s),
        "sample_encoded_getsizeof": sys.getsizeof(s.encode()),
    }


# --------------------------------------------------------------------------- stage: serverlog


def slice_sqllog(container: str, marker: str, out_path: str) -> None:
    """Append the broker SQL log lines from ``marker`` to the next disconnect.

    The broker rotates each CAS log at ~10 MB (keeping one .bak), so the slice has to be
    taken right after every run instead of dumping the logs once at the end.
    """
    import subprocess

    script = (
        "for f in $CUBRID/log/broker/sql_log/broker1_*.sql.log $CUBRID/log/broker/sql_log/"
        f"broker1_*.sql.log.bak; do awk -v m='{marker}' 'index($0,m){{p=1}} p{{print}} "
        'p&&/disconnect/{exit}\' "$f" > /tmp/slice.$$; if [ -s /tmp/slice.$$ ]; then '
        f'echo "### {marker} $f"; cat /tmp/slice.$$; fi; rm -f /tmp/slice.$$; done'
    )
    res = subprocess.run(
        ["docker", "exec", "-u", "cubrid", container, "bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    with open(out_path, "a") as fh:
        fh.write(res.stdout)


def stage_serverlog(a: argparse.Namespace, c: Conn) -> dict[str, Any]:
    ctl = c.open()
    variants = a.variants or (VARIANTS_PY if a.driver == "pycubrid" else VARIANTS_DB)
    runs = []
    for opname in a.ops.split(","):
        op = Op(opname)
        for n in [int(x) for x in a.sizes.split(",")]:
            rows = op.params(n)
            op.full_reset(ctl)
            for v in variants:
                for rep in range(a.log_reps):
                    op.reset(ctl, n)
                    worker = c.open()
                    native = c.open_native() if v == "native_prepared" else None
                    marker = f"M822_{os.getpid()}_{opname}_{n}_{v}_{rep}"
                    mc = worker.cursor() if native is None else None
                    if native is None:
                        mc.execute(f"SELECT '{marker}'")
                        mc.fetchall()
                    else:
                        import pycubrid

                        native._driver_cursor = pycubrid.cursor.Cursor(native._driver)  # type: ignore[attr-defined]
                        native._driver_cursor.execute(f"SELECT '{marker}'")
                        native._driver_cursor.fetchall()
                    run = make_runner(v, a.driver, worker, native, op, n, rows)
                    t0 = pc()
                    run()
                    t1 = pc()
                    commit_fn(v, worker, native)()
                    t2 = pc()
                    op.verify(ctl, n, "serverlog " + v)
                    runs.append(
                        {
                            "marker": marker,
                            "op": opname,
                            "rows": n,
                            "variant": v,
                            "stmt_ns": t1 - t0,
                            "commit_ns": t2 - t1,
                        }
                    )
                    worker.close()
                    if native is not None:
                        native.close()
                    if a.container and a.sqllog_out:
                        slice_sqllog(a.container, marker, a.sqllog_out)
    return {"runs": runs}


# --------------------------------------------------------------------------- main


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def environment(a: argparse.Namespace) -> dict[str, Any]:
    cpu = ""
    try:
        with open("/proc/cpuinfo") as fh:
            cpu = next(ln.split(":", 1)[1].strip() for ln in fh if ln.startswith("model name"))
    except (OSError, StopIteration):
        pass
    info: dict[str, Any] = {}
    if a.driver == "pycubrid":
        import pycubrid

        info = {"pycubrid_version": pycubrid.__version__, "module_path": pycubrid.__file__}
    else:
        import CUBRIDdb

        info = {"module_path": CUBRIDdb.__file__}
    return {
        "python": platform.python_version(),
        "executable": sys.executable,
        "kernel": platform.release(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "loadavg_at_start": os.getloadavg(),
        "driver": a.driver,
        "driver_commit": a.driver_commit,
        "host": a.host,
        "port": a.port,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **info,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--driver", choices=["pycubrid", "cubriddb"], required=True)
    ap.add_argument(
        "--stage",
        choices=["compare", "phases", "profile", "memory", "serverlog"],
        default="compare",
    )
    ap.add_argument("--driver-commit", default="unknown")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=33531)
    ap.add_argument("--db", default="testdb")
    ap.add_argument("--user", default="dba")
    ap.add_argument("--password", default="")
    ap.add_argument("--trial", type=int, default=1)
    ap.add_argument("--ops", default="insert,update,delete,merge")
    ap.add_argument("--sizes", default="100,1000,10000")
    ap.add_argument("--variants", nargs="*")
    ap.add_argument("--iters-100", type=int, default=20)
    ap.add_argument("--iters-1000", type=int, default=10)
    ap.add_argument("--iters-10000", type=int, default=5)
    ap.add_argument("--mem-reps", type=int, default=5)
    ap.add_argument("--log-reps", type=int, default=2)
    ap.add_argument("--container", help="docker container for --sqllog-out slicing")
    ap.add_argument("--sqllog-out", help="append broker SQL log slices of each serverlog run here")
    ap.add_argument("--init", action="store_true", help="(re)create and seed tables, then exit")
    ap.add_argument("--no-ddl", action="store_true", help="ignored; kept for compatibility")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--output")
    a = ap.parse_args()
    c = Conn(a.driver, a.host, a.port, a.db, a.user, a.password)
    if a.init:
        conn = c.open()
        ddl(conn)
        conn.close()
        log("tables created and seeded")
        return
    if not a.output:
        ap.error("--output is required")
    stage = {
        "compare": stage_compare,
        "phases": stage_phases,
        "profile": stage_profile,
        "memory": stage_memory,
        "serverlog": stage_serverlog,
    }[a.stage]
    out = {
        "meta": {**environment(a), "stage": a.stage, "trial": a.trial, "args": vars(a)},
        "results": stage(a, c),
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    with open(a.output, "w") as fh:
        json.dump(out, fh)
    log(f"wrote {a.output}")


if __name__ == "__main__":
    main()
