"""CUBRID-only pytest fixtures used to record this experiment's runs.

``python/conftest.py`` still opens a MySQL connection for every Python benchmark session
(its retirement is tracked separately), so the runs here load only this plugin:

    cd python && PYTHONPATH=../experiments/tier1-python-workload \
        pytest --noconftest -p cubrid_only_plugin bench_pycubrid.py --benchmark-json=<file>

The connection settings match ``python/conftest.py::cubrid_conn``.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pycubrid
import pytest


@pytest.fixture(scope="session")
def cubrid_conn() -> Generator[object, None, None]:
    conn = pycubrid.connect(
        host=os.getenv("CUBRID_HOST", "localhost"),
        port=int(os.getenv("CUBRID_PORT", "33000")),
        database=os.getenv("CUBRID_DB", "benchdb"),
        user="dba",
        password="",
    )
    try:
        yield conn
    finally:
        conn.close()
