"""Live proof of the pre-fix Tier 1 key drift on CUBRID (cubrid-lab/cubrid-benchmark#26).

Replays the old workload shape on a small table: an ``AUTO_INCREMENT`` primary key, a
``DELETE`` of all rows, a re-insert, then reads and writes that address the hard-coded keys
``1..N``. It prints the generated keys and the rows the SELECT/UPDATE/DELETE actually reached.

Usage: python auto_increment_proof.py --host localhost --port 33000 --db benchdb [--rows 5]
"""

from __future__ import annotations

import argparse
import json

import pycubrid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=33000)
    parser.add_argument("--db", default="benchdb")
    parser.add_argument("--rows", type=int, default=5)
    args = parser.parse_args()

    conn = pycubrid.connect(
        host=args.host, port=args.port, database=args.db, user="dba", password=""
    )
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS bench26_auto_increment_proof")
    cur.execute(
        "CREATE TABLE bench26_auto_increment_proof ("
        "id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), amount INT)"
    )
    conn.commit()

    rounds = []
    for round_no in (1, 2):
        cur.execute("DELETE FROM bench26_auto_increment_proof")
        conn.commit()
        for i in range(1, args.rows + 1):
            cur.execute(
                "INSERT INTO bench26_auto_increment_proof (name, amount) VALUES (?, ?)",
                ("row_{:05d}".format(i), i),
            )
        conn.commit()
        cur.execute("SELECT id FROM bench26_auto_increment_proof ORDER BY id")
        ids = [int(row[0]) for row in cur.fetchall()]
        selected = 0
        for i in range(1, args.rows + 1):
            cur.execute("SELECT id FROM bench26_auto_increment_proof WHERE id = ?", (i,))
            selected += cur.fetchone() is not None
        updated = 0
        for i in range(1, args.rows + 1):
            cur.execute(
                "UPDATE bench26_auto_increment_proof SET amount = ? WHERE id = ?", (i + 100000, i)
            )
            updated += cur.rowcount
        deleted = 0
        for i in range(1, args.rows + 1):
            cur.execute("DELETE FROM bench26_auto_increment_proof WHERE id = ?", (i,))
            deleted += cur.rowcount
        conn.rollback()
        rounds.append(
            {
                "round": round_no,
                "generated_ids": ids,
                "addressed_keys": [1, args.rows],
                "select_hits": selected,
                "update_rowcount": updated,
                "delete_rowcount": deleted,
            }
        )

    cur.execute("DROP TABLE IF EXISTS bench26_auto_increment_proof")
    conn.commit()
    cur.execute("SELECT version()")
    version = cur.fetchone()[0]
    conn.close()
    print(
        json.dumps(
            {"cubrid_version": version, "pycubrid": pycubrid.__version__, "rounds": rounds},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
