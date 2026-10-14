"""Apply schema/cubrid_init.sql and schema/seed.sql to CUBRID.

``--with-mysql`` also applies schema/mysql_init.sql and seed.sql to MySQL. That is only for the
historical cross-database reproduction (``make historical-python-mysql``) and needs PyMySQL
from ``python/requirements-historical-mysql.txt``.
"""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _split_sql(content: str) -> list[str]:
    statements = []
    for chunk in content.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
    return statements


def _run_file(cursor, file_path: Path) -> None:
    for statement in _split_sql(file_path.read_text(encoding="utf-8")):
        cursor.execute(statement)


def apply_cubrid() -> None:
    cubrid_module = importlib.import_module("pycubrid")
    cubrid_connect = getattr(cubrid_module, "connect")

    conn = cubrid_connect(
        host=_env("CUBRID_HOST", "localhost"),
        port=int(_env("CUBRID_PORT", "33000")),
        database=_env("CUBRID_DB", "benchdb"),
        user="dba",
        password="",
    )
    cur = conn.cursor()
    try:
        _run_file(cur, SCHEMA_DIR / "cubrid_init.sql")
        _run_file(cur, SCHEMA_DIR / "seed.sql")
        conn.commit()
    finally:
        cur.close()
        conn.close()


def apply_mysql() -> None:
    pymysql = importlib.import_module("pymysql")

    conn = pymysql.connect(
        host=_env("MYSQL_HOST", "localhost"),
        port=int(_env("MYSQL_PORT", "3306")),
        database=_env("MYSQL_DB", "benchdb"),
        user=_env("MYSQL_USER", "root"),
        password=_env("MYSQL_PASSWORD", "bench"),
        autocommit=False,
    )
    cur = conn.cursor()
    try:
        _run_file(cur, SCHEMA_DIR / "mysql_init.sql")
        _run_file(cur, SCHEMA_DIR / "seed.sql")
        conn.commit()
    finally:
        cur.close()
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the benchmark schema and seed data.")
    parser.add_argument(
        "--with-mysql",
        action="store_true",
        help="Also seed MySQL (historical cross-database reproduction only)",
    )
    args = parser.parse_args(argv)

    apply_cubrid()
    if args.with_mysql:
        apply_mysql()
        print("Schema and seed applied to CUBRID and MySQL.")
    else:
        print("Schema and seed applied to CUBRID.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
