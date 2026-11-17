"""Own-source licensing and dependency disclosures retain separate authority."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_metadata_matches_distributed_text():
    """Dependency terms do not silently replace the adapter's source grant."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project["license"] == "MIT"
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "The above copyright notice and this permission notice" in license_text


def test_dependency_license_disclosure_references_the_declared_pin():
    """The disclosure names the SDK and defers its version to pyproject.toml."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    disclosure = (ROOT / "DEPENDENCY_LICENSES.md").read_text(encoding="utf-8")
    assert "pyproject.toml" in disclosure
    for raw in project["dependencies"]:
        dep = Requirement(raw)
        if canonicalize_name(dep.name) == "medre":
            continue
        assert dep.name in disclosure, dep
        pin = next(iter(dep.specifier))
        assert pin.operator == "=="
        # The disclosure never restates the version: a bump edits pyproject only.
        assert str(dep) not in disclosure, dep
        assert f"{dep.name}=={pin.version}" not in disclosure, dep


def test_renovate_does_not_manage_the_license_disclosure():
    """Renovate tracks pins in pyproject only; the disclosure is not a manifest."""
    import json

    config = json.loads((ROOT / "renovate.json").read_text())
    managers = [
        manager
        for manager in config.get("customManagers", [])
        if any(
            "DEPENDENCY_LICENSES" in pattern
            for pattern in manager.get("managerFilePatterns", [])
        )
    ]
    assert managers == []
