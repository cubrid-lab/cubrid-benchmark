"""List the CAS packet classes one pycubrid cursor.execute() sends (for #822 reproduction notes)."""

import sys

import pycubrid

host, port = sys.argv[1], int(sys.argv[2])
conn = pycubrid.connect(
    host=host, port=port, database="testdb", user="dba", password="", autocommit=False
)
cur = conn.cursor()
seen = []
orig = conn._send_and_receive_locked


def spy(packet, *a, **k):
    seen.append(type(packet).__name__)
    return orig(packet, *a, **k)


conn._send_and_receive_locked = spy
for sql, params in (
    ("INSERT INTO b822_ins (id, name, email, age) VALUES (?, ?, ?, ?)", (999999, "a", "b", 2)),
    ("UPDATE b822_upd SET age = ? WHERE id = ?", (5, 1)),
    ("DELETE FROM b822_del WHERE id = ?", (1,)),
):
    del seen[:]
    for i in range(3):
        cur.execute(sql, params)
        conn.rollback()
    print(sql.split()[0], "x3 ->", seen)
conn.rollback()
