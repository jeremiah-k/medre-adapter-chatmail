"""Committed quality tools and dependency discovery share one policy."""

import json
import re
import shlex
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_pinact_only_accepts_diagnostic_exit_codes():
    trunk = yaml.safe_load((ROOT / ".trunk/trunk.yaml").read_text())
    pinact = next(
        item for item in trunk["lint"]["definitions"] if item["name"] == "pinact"
    )
    lint = next(command for command in pinact["commands"] if command["name"] == "lint")
    assert lint["success_codes"] == [0, 1, 2]
    assert lint["output"] == "sarif"
    assert "--no-api" in lint["run"]


def test_renovate_extracts_every_annotated_tool_pin():
    trunk = (ROOT / ".trunk/trunk.yaml").read_text()
    config = json.loads((ROOT / "renovate.json").read_text())
    manager = next(
        item
        for item in config["customManagers"]
        if item["managerFilePatterns"] == [r"/^\.trunk/trunk\.yaml$/"]
    )
    # The committed RE2 pattern uses ECMAScript named groups; translate only
    # the group syntax for this lightweight extraction regression.
    pattern = re.sub(r"\(\?<([a-zA-Z]+)>", r"(?P<\1>", manager["matchStrings"][0])
    annotated = [line for line in trunk.splitlines() if "# renovate:" in line]
    assert annotated
    extracted = []
    for line in annotated:
        match = re.search(pattern, line)
        assert match is not None, line
        extracted.append(match.groupdict())
    data = yaml.safe_load(trunk)
    versions = [
        data["cli"]["version"],
        *(source["ref"].removeprefix("v") for source in data["plugins"]["sources"]),
        *(entry.rsplit("@", 1)[1] for entry in data["lint"]["enabled"] if "@" in entry),
    ]
    assert sorted(item["currentValue"] for item in extracted) == sorted(versions)
    assert len({item["depName"] for item in extracted}) == len(versions)
    cli = next(item for item in extracted if item["depName"] == "trunk-cli")
    assert cli["datasource"] == "custom.trunk-cli"
    registry = config["customDatasources"]["trunk-cli"]["defaultRegistryUrlTemplate"]
    assert registry in (ROOT / ".trunk/trunk").read_text()
    assert config["customDatasources"]["trunk-cli"]["format"] == "yaml"


def test_contributing_sdk_selection_matches_ci():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    (transport,) = project["project"]["entry-points"]["medre.adapters"]
    expected = (
        f"{transport}_sdk and not local_integration and not soak "
        "and not live and not hardware and not docker"
    )
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    routine = contributing.split("```bash", 1)[1].split("```", 1)[0]
    assert "trunk upgrade" not in routine
    assert ' -m "' + expected + '"' in contributing
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
    sdk_selectors = []
    for step in workflow["jobs"]["test"]["steps"]:
        run = step.get("run", "")
        if "-m pytest" not in run or f"{transport}_sdk" not in run:
            continue
        args = shlex.split(run)
        # The first -m selects the module; the second selects pytest markers.
        sdk_selectors.append(args[args.index("-m", args.index("pytest") + 1) + 1])
    assert sdk_selectors == [expected]
