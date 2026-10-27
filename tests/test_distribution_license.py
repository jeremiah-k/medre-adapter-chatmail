"""Own-source licensing and dependency disclosures retain separate authority."""

from pathlib import Path
import tomllib

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


def test_dependency_license_disclosure_tracks_runtime_pins():
    """A dependency update must keep its cited distribution requirement current."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    disclosure = (ROOT / "DEPENDENCY_LICENSES.md").read_text(encoding="utf-8")
    for requirement in project["dependencies"]:
        if not requirement.startswith("medre>="):
            assert requirement in disclosure, requirement
