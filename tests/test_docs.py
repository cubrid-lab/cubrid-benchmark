"""Current-facing documentation: links resolve, commands exist, scope statements stay accurate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CURRENT_DOCS = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "RUNBOOK.md",
    "REPRODUCIBILITY.md",
    "ROADMAP.md",
    "BENCHMARK_PLAN.md",
    "PRD.md",
    "docs/METHODOLOGY.md",
    "docs/WORKLOADS.md",
]
LINK = re.compile(r"\]\(([^)\s]+)\)")
MAKE_TARGET = re.compile(r"(?<![\w-])make ((?:[a-z0-9][\w-]*)(?: [a-z0-9][\w-]*)*)")


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _make_targets() -> set[str]:
    targets = set()
    for line in _text("Makefile").splitlines():
        match = re.match(r"^([a-z0-9][\w-]*):(?!=)", line)
        if match:
            targets.add(match.group(1))
    return targets


@pytest.mark.parametrize("doc", CURRENT_DOCS)
def test_relative_links_resolve(doc: str) -> None:
    base = (ROOT / doc).parent
    for target in LINK.findall(_text(doc)):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path = target.split("#", 1)[0]
        assert (base / path).exists(), f"{doc}: broken link {target}"


@pytest.mark.parametrize("doc", CURRENT_DOCS)
def test_no_links_to_the_old_organization(doc: str) -> None:
    assert "github.com/cubrid-labs" not in _text(doc), doc
    assert "orgs/cubrid-labs" not in _text(doc), doc


@pytest.mark.parametrize(
    "doc", ["README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/METHODOLOGY.md"]
)
def test_documented_make_targets_exist(doc: str) -> None:
    targets = _make_targets()
    # Only inspect code blocks and inline code, where commands are written.
    code = "\n".join(re.findall(r"```(?:bash)?\n(.*?)```", _text(doc), re.S))
    code += "\n" + "\n".join(re.findall(r"`([^`\n]*)`", _text(doc)))
    for match in MAKE_TARGET.finditer(code):
        for word in match.group(1).split():
            if "=" in word or word.isupper():
                break
            assert word in targets, f"{doc}: make {word} does not exist"


def test_no_vanished_commands() -> None:
    for doc in ("README.md", "AGENTS.md", "CONTRIBUTING.md"):
        text = _text(doc)
        for vanished in ("python -m benchmark", "npm run benchmark", "go run main.go"):
            assert vanished not in text, f"{doc}: {vanished}"
        assert "make tier2-go" not in text and "make tier2-ts" not in text, doc


def test_readme_lists_every_experiment_once() -> None:
    readme = _text("README.md")
    for experiment in sorted((ROOT / "experiments").iterdir()):
        if not experiment.is_dir() or experiment.name.startswith("_"):
            continue
        link = f"(experiments/{experiment.name}/README.md)"
        assert readme.count(link) == 1, experiment.name


def test_cross_database_results_are_framed_as_historical() -> None:
    readme = _text("README.md")
    assert "## Historical Experiments" in readme
    assert "pure-Python" in readme
    agents = _text("AGENTS.md")
    assert "primary optimization target" not in agents
    assert "historical" in agents.split("### Current focus", 1)[1]
    assert "C extensions" not in _text("docs/METHODOLOGY.md")
    assert "retired" in _text("BENCHMARK_PLAN.md").split("## Table of Contents", 1)[0]


def test_draft_issue_target_uses_the_current_organization() -> None:
    assert "TARGET_REPO ?= cubrid-lab/pycubrid" in _text("Makefile")
    assert 'DEFAULT_TARGET_REPO = "cubrid-lab/pycubrid"' in _text("scripts/gap_to_issue.py")
