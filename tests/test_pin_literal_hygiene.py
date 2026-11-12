"""Runtime pin values live only in pyproject and the license disclosure."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

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
