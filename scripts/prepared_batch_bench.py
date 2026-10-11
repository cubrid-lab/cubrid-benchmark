#!/usr/bin/env python3
"""Prepared-statement batch paths for pycubrid executemany() (cubrid-lab/pycubrid#836).

Compares three ways of sending the same DML batch through pycubrid:

* ``executemany``     the shipped path: N client-rendered literal SQL strings in one
                      EXECUTE_BATCH (function code 20) request.
* ``native_prepared`` ``pycubrid.compat.native``: one PREPARE (FC2), then one EXECUTE (FC3)
                      with bound values per row (N round trips).
* ``execute_array``   prototype: one PREPARE (FC2), one EXECUTE_ARRAY (FC21) carrying all
                      rows' bound values, one CLOSE_REQ (FC6). The request and reply are
                      built by this harness (``ExecuteArrayPacket``); the driver is not modified.

Stages (``--stage``):

* ``compare``   same schema, data, iteration counts, timing and verification as
                ``executemany_overhead_bench.py --stage compare`` (imported from it).
* ``semantics`` transaction, error, rowcount, lastrowid and type-coverage probes for each
                path, autocommit on and off. Writes observations only; nothing is timed.

Run under ``python -I`` from a venv that holds only pycubrid.
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import gc
import json
import os
import sys
import traceback
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import executemany_overhead_bench as base  # noqa: E402
import pycubrid  # noqa: E402
from pycubrid.constants import CASFunctionCode, CUBRIDDataType  # noqa: E402
from pycubrid.packet import PacketReader, PacketWriter  # noqa: E402
from pycubrid.protocol import (  # noqa: E402
    BatchExecutePacket,
    CloseQueryPacket,
    GetLastInsertIdPacket,
    PreparePacket,
    _CasPacket,
    _encode_prepared_collection,
    _encode_prepared_scalar,
    _PreparedLob,
    _raise_error,
)

pc = base.pc

# --------------------------------------------------------------------------- FC21 prototype


class ExecuteArrayPacket(_CasPacket):
    """EXECUTE_ARRAY (FC21) request and reply.

    Request (JDBC ``UStatement.writeExecuteBatchRequest``, CAS ``fn_execute_array``):
    int query handle, int query timeout (protocol V4+), byte autocommit, then for every
    row and every marker one (byte type, value) argument pair, the same pair ExecutePacket
    writes for one bound value.

    Reply (``ux_execute_array``): int result code, int executed row count, then per row
    either ``int count >= 0`` followed by an 8-byte OID, or ``int -1`` (DBMS error
    indicator, V3+), int error code, int message length, message. Shard id (V5+) last.
    """

    def __init__(self, handle: int, rows: list[tuple[Any, ...]], auto_commit: bool, pv: int):
        self.handle, self.rows, self.auto_commit, self.pv = handle, rows, auto_commit, pv
        self.outcomes: list[tuple[str, int, str]] = []  # ("ok", count, "") / ("error", code, msg)

    def write(self, cas_info: bytes) -> bytes:
        w = PacketWriter(encoding=self.encoding)
        w._write_byte(CASFunctionCode.EXECUTE_ARRAY)
        w.add_int(self.handle)
        if self.pv > 3:
            w.add_int(0)  # query timeout (ms)
        w.add_byte(1 if self.auto_commit else 0)
        for row in self.rows:
            for b in row:
                w.add_byte(b.type_code)
                w.add_bytes(b.payload)
        return w.finalize(cas_info)

    def parse(self, data: bytes | bytearray) -> None:
        r = PacketReader(data, encoding=self.encoding)
        r._skip_bytes(4)
        code = r._parse_int()
        if code < 0:
            _raise_error(r, len(data) - 8)
        n = r._parse_int()
        self.outcomes = []
        for _ in range(n):
            res = r._parse_int()
            if res < 0:
                err = r._parse_int()
                msg = r._parse_lenient_text(r._parse_int())
                self.outcomes.append(("error", err, msg))
            else:
                r._parse_int()
                r._parse_short()
                r._parse_short()
                self.outcomes.append(("ok", res, ""))
        if self.pv > 4:
            r._parse_int()


class OrderedBatchPacket(BatchExecutePacket):
    """FC20 with the per-statement outcomes kept in order (the driver splits them)."""

    def parse(self, data: bytes | bytearray) -> None:
        r = PacketReader(data, encoding=self.encoding)
        r._skip_bytes(4)
        code = r._parse_int()
        if code < 0:
            _raise_error(r, len(data) - 8)
        self.outcomes: list[tuple[str, int, str]] = []
        for _ in range(r._parse_int()):
            r._parse_byte()
            res = r._parse_int()
            if res < 0:
                err = r._parse_int()
                msg = r._parse_lenient_text(r._parse_int())
                self.outcomes.append(("error", err, msg))
            else:
                r._parse_int()
                r._parse_short()
                r._parse_short()
                self.outcomes.append(("ok", res, ""))
        if self.protocol_version > 4:
            r._parse_int()


def encode_rows(conn: Any, rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    enc = conn._encoding
    return [tuple(_encode_prepared_scalar(v, enc) for v in row) for row in rows]


def execute_array(
    conn: Any, sql: str, bound_rows: list[tuple[Any, ...]], *, close: bool = True
) -> ExecuteArrayPacket:
    """FC2 + FC21 (+ FC6) on the ordinary pycubrid connection."""
    prep = PreparePacket(sql, auto_commit=conn.autocommit)
    conn._send_and_receive(prep)
    pkt = ExecuteArrayPacket(prep.query_handle, bound_rows, conn.autocommit, conn._protocol_version)
    try:
        conn._send_and_receive(pkt)
    finally:
        if close:
            try:
                conn._send_and_receive(CloseQueryPacket(prep.query_handle))
            except pycubrid.Error:
                pass  # handle already released by an autocommit boundary
    return pkt


# --------------------------------------------------------------------------- stage: compare

VARIANTS = ["executemany", "native_prepared", "execute_array"]


def make_runner(variant: str, conn: Any, native: Any, op: base.Op, rows: list[tuple[Any, ...]]):
    if variant == "execute_array":

        def run() -> None:
            pkt = execute_array(conn, op.sql, encode_rows(conn, rows))
            bad = [o for o in pkt.outcomes if o[0] != "ok"]
            base.check(len(pkt.outcomes) == len(rows) and not bad, f"FC21 outcomes {bad[:3]}")

        return run
    return base.make_runner(variant, "pycubrid", conn, native, op, len(rows), rows)


def stage_compare(a: argparse.Namespace, c: base.Conn) -> dict[str, Any]:
    worker, ctl = c.open(), c.open()
    native = c.open_native()
    variants = a.variants or VARIANTS
    sizes = [int(x) for x in a.sizes.split(",")]
    iters = {100: (a.iters_100, 3), 1000: (a.iters_1000, 2), 10000: (a.iters_10000, 1)}
    res: dict[str, Any] = {}
    for opname in a.ops.split(","):
        op = base.Op(opname)
        for n in sizes:
            nit, warm = iters.get(n, (5, 1))
            if a.quick:
                nit, warm = 2, 1
            rows = op.params(n)
            op.full_reset(ctl)
            k = (a.trial - 1) % len(variants)
            for v in variants[k:] + variants[:k]:
                total, ex_ns, cm_ns = [], [], []
                for i in range(warm + nit):
                    op.reset(ctl, n)
                    run = make_runner(v, worker, native, op, rows)
                    commit = base.commit_fn(v, worker, native)
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
                key = f"{opname}/{n}/{v}"
                res[key] = {
                    "op": opname,
                    "rows": n,
                    "variant": v,
                    "total_ns": total,
                    "stmt_ns": ex_ns,
                    "commit_ns": cm_ns,
                    "stats": base.stats_ms(total),
                    "verified": True,
                }
                base.log(f"{key}: median {res[key]['stats']['median_ms']:.1f} ms")
    return res


# --------------------------------------------------------------------------- stage: semantics

T = "s836"
TA = "s836_ai"
TL = "s836_lob"
TS = "s836_set"


def _exc(e: BaseException) -> dict[str, Any]:
    return {
        "class": type(e).__name__,
        "code": getattr(e, "code", None),
        "message": str(e)[:300],
    }


def _count(c: Any, table: str = T) -> list[Any]:
    cur = c.cursor()
    cur.execute(f"SELECT id FROM {table} ORDER BY id")
    ids = [r[0] for r in cur.fetchall()]
    cur.close()
    if not c.autocommit:
        c.commit()
    return ids


def _visible_same_txn(c: Any, table: str = T) -> list[Any]:
    cur = c.cursor()
    cur.execute(f"SELECT id FROM {table} ORDER BY id")
    ids = [r[0] for r in cur.fetchall()]
    cur.close()
    return ids


class Path:
    """Runs one parameterised DML batch through one execution path and reports outcomes."""

    def __init__(self, name: str, c: base.Conn, autocommit: bool) -> None:
        self.name, self.autocommit = name, autocommit
        if name == "native_prepared":
            self.n = c.open_native()
            self.n.set_autocommit(autocommit)
            self.conn = None
        else:
            self.conn = pycubrid.connect(
                host=c.host,
                port=c.port,
                database=c.db,
                user=c.user,
                password=c.pw,
                autocommit=autocommit,
            )

    def run(self, sql: str, rows: list[tuple[Any, ...]]) -> dict[str, Any]:
        out: dict[str, Any] = {"raised": None, "rowcount": None, "per_row": None}
        if self.name == "executemany":
            cur = self.conn.cursor()
            try:
                cur.executemany(sql, rows)
            except Exception as e:  # noqa: BLE001
                out["raised"] = _exc(e)
            out["rowcount"] = cur.rowcount
            out["lastrowid"] = cur.lastrowid
            # Same request again on a scratch probe is not possible (side effects);
            # the ordered outcome list comes from run_raw_fc20() in a separate scenario.
            return out
        if self.name == "executemany_fc20_raw":
            cur = self.conn.cursor()
            pkt = OrderedBatchPacket(
                [cur._bind_parameters(sql, r) for r in rows],
                auto_commit=self.conn.autocommit,
                protocol_version=self.conn._protocol_version,
            )
            try:
                self.conn._send_and_receive(pkt)
            except Exception as e:  # noqa: BLE001
                out["raised"] = _exc(e)
            out["per_row"] = getattr(pkt, "outcomes", None)
            return out
        if self.name == "execute_array":
            try:
                bound = [tuple(self.bind(v) for v in r) for r in rows]
            except Exception as e:  # noqa: BLE001
                out["raised"] = {"stage": "client encode", **_exc(e)}
                return out
            try:
                pkt = execute_array(self.conn, sql, bound)
                out["per_row"] = pkt.outcomes
                out["rowcount_sum_ok"] = sum(o[1] for o in pkt.outcomes if o[0] == "ok")
                lid = GetLastInsertIdPacket()
                self.conn._send_and_receive(lid)
                out["last_insert_id_after"] = lid.last_insert_id
            except Exception as e:  # noqa: BLE001
                out["raised"] = _exc(e)
            return out
        # native_prepared: prepare once, execute per row, stop at the first error
        ncur = self.n.cursor()
        counts: list[Any] = []
        try:
            ncur.prepare(sql)
            for r in rows:
                for k, v in enumerate(r, 1):
                    if isinstance(v, tuple) and v and v[0] == "__set__":
                        s = self.n.set()
                        s.imports(tuple(str(x) for x in v[1]), CUBRIDDataType.INT)
                        ncur.bind_set(k, s)
                    else:
                        ncur.bind_param(k, v)
                try:
                    counts.append(("ok", ncur.execute(), ""))
                except Exception as e:  # noqa: BLE001
                    counts.append(("error", getattr(e, "code", None), str(e)[:200]))
                    out["raised"] = _exc(e)
                    break
        except Exception as e:  # noqa: BLE001
            out["raised"] = {"stage": "bind or prepare", **_exc(e)}
        out["per_row"] = counts
        return out

    def bind(self, v: Any) -> Any:
        enc = self.conn._encoding
        if isinstance(v, tuple) and v and v[0] == "__set__":
            return _encode_prepared_collection(v[1], CUBRIDDataType.SET, CUBRIDDataType.INT, enc)
        if isinstance(v, tuple) and v and v[0] == "__clob__":
            lob = self.conn.create_lob(CUBRIDDataType.CLOB)
            lob.write(v[1].encode())
            return _PreparedLob(
                CUBRIDDataType.CLOB, lob.lob_handle, self.conn, self.conn._physical_generation
            )
        return _encode_prepared_scalar(v, enc)

    def commit(self) -> None:
        (self.n if self.conn is None else self.conn).commit()

    def rollback(self) -> None:
        (self.n if self.conn is None else self.conn).rollback()

    def visible(self, table: str = T) -> list[Any]:
        if self.conn is None:
            # compat.native has no general cursor; read through a helper connection would
            # not see uncommitted rows, so use the driver connection of the same session.
            cur = pycubrid.cursor.Cursor(self.n._driver)
            cur.execute(f"SELECT id FROM {table} ORDER BY id")
            ids = [r[0] for r in cur.fetchall()]
            cur.close()
            return ids
        return _visible_same_txn(self.conn, table)

    def close(self) -> None:
        (self.n if self.conn is None else self.conn).close()


def _fresh(ctl: Any) -> None:
    cur = ctl.cursor()
    for t, ddl in (
        (T, f"CREATE TABLE {T} (id INT PRIMARY KEY, name VARCHAR(50), n INT)"),
        (TA, f"CREATE TABLE {TA} (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(50))"),
        (TL, f"CREATE TABLE {TL} (id INT PRIMARY KEY, doc CLOB)"),
        (TS, f"CREATE TABLE {TS} (id INT PRIMARY KEY, tags SET(INT))"),
    ):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(ddl)
    ctl.commit()
    cur.close()


def _seed(ctl: Any, ids: list[int]) -> None:
    cur = ctl.cursor()
    cur.execute(f"DELETE FROM {T}")
    for i in ids:
        cur.execute(f"INSERT INTO {T} (id, name, n) VALUES (?, ?, ?)", (i, f"s{i}", i % 2))
    ctl.commit()
    cur.close()


PATHS = ["executemany", "executemany_fc20_raw", "native_prepared", "execute_array"]
INS = f"INSERT INTO {T} (id, name, n) VALUES (?, ?, ?)"


def stage_semantics(a: argparse.Namespace, c: base.Conn) -> dict[str, Any]:
    ctl = c.open()
    _fresh(ctl)
    res: dict[str, Any] = {}
    for path in PATHS:
        for ac in (False, True):
            key = f"{path}/autocommit={'on' if ac else 'off'}"
            r: dict[str, Any] = {}
            try:
                r = _scenarios(path, ac, c, ctl)
            except Exception:  # noqa: BLE001
                r = {"harness_error": traceback.format_exc()}
            res[key] = r
            base.log(f"semantics {key} done")
    ctl.close()
    return res


def _scenarios(path: str, ac: bool, c: base.Conn, ctl: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # 1. duplicate key in row 3 of 5 (rows 1, 2, 2, 4, 5)
    for scen, rows in (
        ("duplicate_key_row3", [(1, "a", 1), (2, "b", 0), (2, "dup", 0), (4, "d", 0), (5, "e", 1)]),
        ("conversion_error_row3", [(1, "a", 1), (2, "b", 0), (3, "c", "abc"), (4, "d", 0)]),
    ):
        for end in ("rollback", "commit"):
            _seed(ctl, [])
            p = Path(path, c, ac)
            rec = p.run(INS, rows)
            rec["visible_in_same_session_before_end"] = p.visible()
            if not ac:
                (p.rollback if end == "rollback" else p.commit)()
            rec[f"committed_after_{end}"] = _count(ctl)
            p.close()
            out[f"{scen}/{end}" if not ac else scen] = rec
            if ac:
                break
    # 2. rowcount: rows 1..5 with n = id % 2; per-row counts 3, 0, 2
    _seed(ctl, [1, 2, 3, 4, 5])
    p = Path(path, c, ac)
    out["update_rowcount_3_0_2"] = p.run(
        f"UPDATE {T} SET name = ? WHERE n = ?", [("x", 1), ("y", 9), ("z", 0)]
    )
    if not ac:
        p.rollback()
    out["delete_rowcount_1_0_1"] = p.run(f"DELETE FROM {T} WHERE id = ?", [(1,), (99,), (2,)])
    if not ac:
        p.rollback()
    p.close()
    # 3. NULLs
    _seed(ctl, [])
    p = Path(path, c, ac)
    rec = p.run(INS, [(1, None, None), (2, "b", None), (3, None, 3)])
    if not ac:
        p.commit()
    cur = ctl.cursor()
    cur.execute(f"SELECT id, name, n FROM {T} ORDER BY id")
    rec["stored"] = [list(x) for x in cur.fetchall()]
    ctl.commit()
    out["nulls"] = rec
    p.close()
    # 4. other scalar types (literal path renders them; the prepared encoders may not)
    types = {
        "bool": True,
        "bigint": 2**40,
        "float": 1.5,
        "decimal": decimal.Decimal("1.25"),
        "date": dt.date(2026, 1, 2),
        "datetime": dt.datetime(2026, 1, 2, 3, 4, 5),
        "bytes": b"\x01\x02",
    }
    tr: dict[str, Any] = {}
    for tname, val in types.items():
        _seed(ctl, [])
        p = Path(path, c, ac)
        sql = f"INSERT INTO {T} (id, name) VALUES (?, ?)"
        rec = p.run(sql, [(1, val)])
        if not ac:
            p.commit()
        rec["committed"] = _count(ctl)
        p.close()
        tr[tname] = rec
    out["scalar_types_into_varchar"] = tr
    # 5. collection SET(INT)
    p = Path(path, c, ac)
    cur = ctl.cursor()
    cur.execute(f"DELETE FROM {TS}")
    ctl.commit()
    if path in ("execute_array", "native_prepared"):
        rows = [(1, ("__set__", (1, 2))), (2, ("__set__", (3,)))]
    else:
        rows = [(1, pycubrid.Set([1, 2])), (2, pycubrid.Set([3]))]
    rec = p.run(f"INSERT INTO {TS} (id, tags) VALUES (?, ?)", rows)
    if not ac:
        p.commit()
    rd = pycubrid.connect(
        host=c.host,
        port=c.port,
        database=c.db,
        user=c.user,
        password=c.pw,
        decode_collections=True,
    )
    rcur = rd.cursor()
    rcur.execute(f"SELECT id, tags FROM {TS} ORDER BY id")
    rec["stored"] = [[x[0], sorted(x[1])] for x in rcur.fetchall()]
    rd.close()
    out["set_int"] = rec
    p.close()
    # 6. CLOB, one fresh LOB handle per row (FC21 only; the literal path rejects Lob)
    p = Path(path, c, ac)
    cur.execute(f"DELETE FROM {TL}")
    ctl.commit()
    if path == "execute_array":
        rows = [(1, ("__clob__", "first")), (2, ("__clob__", "second"))]
    elif path == "native_prepared":
        rows = []  # bind_lob() needs a compat.native lob; covered by pycubrid's own tests
    else:
        rows = [(1, "first"), (2, "second")]  # str literal into a CLOB column
    if rows:
        rec = p.run(f"INSERT INTO {TL} (id, doc) VALUES (?, ?)", rows)
        if not ac:
            p.commit()
        cur.execute(f"SELECT id, CLOB_TO_CHAR(doc) FROM {TL} ORDER BY id")
        rec["stored"] = [list(x) for x in cur.fetchall()]
        ctl.commit()
        out["clob"] = rec
    p.close()
    # 7. AUTO_INCREMENT and lastrowid
    p = Path(path, c, ac)
    cur.execute(f"DELETE FROM {TA}")
    ctl.commit()
    rec = p.run(f"INSERT INTO {TA} (name) VALUES (?)", [("a",), ("b",), ("c",)])
    if not ac:
        p.commit()
    cur.execute(f"SELECT MAX(id) FROM {TA}")
    rec["max_id"] = cur.fetchone()[0]
    ctl.commit()
    out["auto_increment"] = rec
    p.close()
    cur.close()
    return out


# --------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["compare", "semantics"], default="compare")
    ap.add_argument("--driver-commit", default="unknown")
    ap.add_argument("--cubrid-version", default="unknown")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=33814)
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
    ap.add_argument("--init", action="store_true", help="(re)create and seed tables, then exit")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--output")
    a = ap.parse_args()
    a.driver = "pycubrid"
    c = base.Conn("pycubrid", a.host, a.port, a.db, a.user, a.password)
    if a.init:
        conn = c.open()
        base.ddl(conn)
        conn.close()
        base.log("tables created and seeded")
        return
    if not a.output:
        ap.error("--output is required")
    stage = {"compare": stage_compare, "semantics": stage_semantics}[a.stage]
    meta = {**base.environment(a), "stage": a.stage, "trial": a.trial, "args": vars(a)}
    probe = c.open()
    meta["server_version"] = probe.get_server_version()
    meta["protocol_version"] = probe._protocol_version
    probe.close()
    out = {"meta": meta, "results": stage(a, c)}
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    with open(a.output, "w") as fh:
        json.dump(out, fh, default=str)
    base.log(f"wrote {a.output}")


if __name__ == "__main__":
    main()
