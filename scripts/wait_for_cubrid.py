"""Wait until a CUBRID broker accepts a pycubrid connection (no MySQL involved).

Usage: python scripts/wait_for_cubrid.py [--host localhost] [--port 33000] [--db benchdb]
                                         [--timeout 180]
Exit status 0 once ``SELECT 1`` succeeds, 1 on timeout.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default=os.getenv("CUBRID_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CUBRID_PORT", "33000")))
    parser.add_argument("--db", default=os.getenv("CUBRID_DB", "benchdb"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv)

    import pycubrid

    deadline = time.monotonic() + args.timeout
    last_error = "no attempt"
    while time.monotonic() < deadline:
        try:
            conn = pycubrid.connect(
                host=args.host, port=args.port, database=args.db, user="dba", password=""
            )
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - keep retrying until the deadline
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(args.interval)
            continue
        print(f"CUBRID ready at {args.host}:{args.port}/{args.db}")
        return 0
    sys.stderr.write(
        f"CUBRID not ready at {args.host}:{args.port}/{args.db} after {args.timeout:.0f}s "
        f"({last_error})\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
