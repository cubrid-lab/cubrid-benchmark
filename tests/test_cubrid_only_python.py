"""The maintained Python benchmark path must never touch MySQL or import PyMySQL."""

from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = ROOT / "python"

# pycubrid stand-in backed by SQLite: the maintained workloads use explicit keys and "?"
# placeholders, so they run unchanged. Every connect() is recorded.
FAKE_PYCUBRID = """
import sqlite3

CONNECTS = []


def connect(**kwargs):
    CONNECTS.append(kwargs)
    return sqlite3.connect(":memory:")
"""

# Importing PyMySQL from a CUBRID-only session is the failure this test guards against.
FAKE_PYMYSQL = """
raise RuntimeError("PyMySQL was imported by a CUBRID-only benchmark session")
"""

HISTORICAL_MYSQL_MODULES = {
    "bench_tier0_mysql.py",
    "bench_pymysql.py",
    "bench_pymysql_extended.py",
    "bench_sqlalchemy_mysql.py",
}


def _run_session(tmp_path: Path, *modules: str) -> subprocess.CompletedProcess[str]:
    shutil.copy(PYTHON_DIR / "conftest.py", tmp_path / "conftest.py")
    for module in modules:
        shutil.copy(PYTHON_DIR / module, tmp_path / module)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "pycubrid.py").write_text(FAKE_PYCUBRID, encoding="utf-8")
    (stubs / "pymysql.py").write_text(FAKE_PYMYSQL, encoding="utf-8")
    env = {"PYTHONPATH": str(stubs), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--benchmark-disable"]
        + list(modules),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def test_cubrid_session_never_imports_pymysql(tmp_path: Path) -> None:
    result = _run_session(tmp_path, "bench_pycubrid.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 passed" in result.stdout
    assert "PyMySQL was imported" not in result.stdout + result.stderr


def test_active_requirements_exclude_pymysql() -> None:
    active = (PYTHON_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
    historical = (PYTHON_DIR / "requirements-historical-mysql.txt").read_text(encoding="utf-8")

    assert "pymysql" not in active
    assert "PyMySQL" in historical and "-r requirements.txt" in historical


def _make_recipe(target: str) -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(target)}:.*?(?=^\S|\Z)", text, re.M | re.S)
    assert match, target
    return match.group(0)


def test_default_python_targets_are_cubrid_only() -> None:
    for target in (
        "tier0",
        "tier1-python",
        "tier1-python-extended",
        "tier2-python",
        "bench-verify",
    ):
        recipe = _make_recipe(target)
        for module in HISTORICAL_MYSQL_MODULES:
            assert module not in recipe, (target, module)
    all_targets = _make_recipe("all")
    assert "historical-python-mysql" not in all_targets


def test_historical_sources_stay_reproducible() -> None:
    recipe = _make_recipe("historical-python-mysql")
    for module in HISTORICAL_MYSQL_MODULES:
        assert (PYTHON_DIR / module).is_file(), module
        assert module in recipe, module


def test_workflow_python_lanes_do_not_run_mysql_modules() -> None:
    text = (ROOT / ".github" / "workflows" / "bench.yml").read_text(encoding="utf-8")

    for module in HISTORICAL_MYSQL_MODULES:
        assert module not in text, module
    assert "historical-python-mysql" not in text


def test_default_seed_never_imports_pymysql(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    apply_schema = importlib.import_module("apply_schema")
    calls: list[str] = []
    monkeypatch.setattr(apply_schema, "apply_cubrid", lambda: calls.append("cubrid"))
    monkeypatch.setattr(apply_schema, "apply_mysql", lambda: calls.append("mysql"))

    assert apply_schema.main([]) == 0
    assert calls == ["cubrid"]
    assert apply_schema.main(["--with-mysql"]) == 0
    assert calls == ["cubrid", "cubrid", "mysql"]


def test_historical_target_seeds_mysql_explicitly() -> None:
    assert "apply_schema.py --with-mysql" in _make_recipe("historical-python-mysql")
    assert "--with-mysql" not in _make_recipe("seed")
