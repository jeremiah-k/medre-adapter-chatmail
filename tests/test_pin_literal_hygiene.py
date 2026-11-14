"""Runtime pin values live only in pyproject and the license disclosure."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

_VERSION_LITERAL = re.compile(r"\d+(?:\.\d+){1,3}(?:[a-zA-Z0-9.+_-]*)?")

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_FILE_NAMES = {"pyproject.toml", "DEPENDENCY_LICENSES.md", "uv.lock"}
SCAN_DIRECTORIES = ("src", "tests", "scripts", "docs", ".github")
SCAN_ROOT_FILES = ("README.md", "renovate.json")


def _declared_runtime_pins() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    pins: dict[str, str] = {}
    for requirement in project["dependencies"]:
        if requirement.startswith("medre") or "==" not in requirement:
            continue
        name, _, value = requirement.partition("==")
        pins[name.split("[", 1)[0]] = value
    assert pins, "runtime SDK pins must be declared"
    return pins


def _scanned_files() -> list[Path]:
    files = [ROOT / name for name in SCAN_ROOT_FILES]
    for directory in SCAN_DIRECTORIES:
        base = ROOT / directory
        if not base.is_dir():
            continue
        files.extend(path for path in base.rglob("*") if path.is_file())
    return files


def test_runtime_pin_values_stay_out_of_secondary_files():
    """A version-only dependency bump must not require edits beyond the pin and disclosure."""
    patterns = {
        name: re.compile(
            rf"{re.escape(name)}(?:\[[^\]]*\])?(?:==|@|\s+){re.escape(value)}"
        )
        for name, value in _declared_runtime_pins().items()
    }
    offenders: list[str] = []
    for path in _scanned_files():
        relative = path.relative_to(ROOT)
        if path.name in ALLOWED_FILE_NAMES:
            continue
        if "changes" in relative.parts or "__pycache__" in relative.parts:
            continue
        if any(part.endswith(".egg-info") for part in relative.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(
            f"{relative} mentions {name}"
            for name, pattern in patterns.items()
            if pattern.search(text)
        )
    assert not offenders, offenders


def _version_literal_comparisons(source: str) -> list[int]:
    """Find equality checks between installed-version queries and literal pins."""
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not any(
            isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops
        ):
            continue
        operands = (node.left, *node.comparators)
        version_query = any(
            isinstance(value, ast.Call)
            and (
                isinstance(value.func, ast.Name)
                and value.func.id == "version"
                or isinstance(value.func, ast.Attribute)
                and value.func.attr == "version"
            )
            for value in operands
        )
        literal_pin = any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and _VERSION_LITERAL.fullmatch(value.value)
            for value in operands
        )
        if version_query and literal_pin:
            hits.append(node.lineno)
    return hits


def test_version_literal_guard_detects_assertion_forms():
    for source in (
        'assert version("simplex-chat") == "7.0.2"',
        'assert "7.0.2" == version("simplex-chat")',
        'assert metadata.version("simplex-chat") != "7.0.2"',
    ):
        assert _version_literal_comparisons(source) == [1]
    assert not _version_literal_comparisons(
        'assert version("simplex-chat") == expected_version'
    )


def test_installed_version_assertions_follow_declared_pins():
    offenders = []
    for path in _scanned_files():
        relative = path.relative_to(ROOT)
        if path.suffix != ".py" or "__pycache__" in relative.parts:
            continue
        if any(part.endswith(".egg-info") for part in relative.parts):
            continue
        offenders.extend(
            f"{relative}:{line}"
            for line in _version_literal_comparisons(path.read_text(encoding="utf-8"))
        )
    assert not offenders, offenders
