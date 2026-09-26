"""Contract: the coverage lanes set up a Python the project accepts.

generate-coverage builds its coverage environment on the Python the job put on
``PATH`` when nothing more specific names one. If ``actions/setup-python``
installs a version outside ``requires-python``, ``uv sync`` refuses the
interpreter and the coverage step fails, so every ``setup-python`` step in the
two coverage lanes must request a version the project accepts.
"""

from __future__ import annotations

import tomllib
import typing as typ
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
LANES: typ.Final[tuple[str, ...]] = ("ci.yml", "coverage-main.yml")
SETUP_PYTHON: typ.Final[str] = "actions/setup-python@"


def requires_python(pyproject: str) -> SpecifierSet:
    """Return the project's ``requires-python`` specifier set."""
    return SpecifierSet(tomllib.loads(pyproject)["project"]["requires-python"])


def setup_python_versions(workflow: str) -> list[str]:
    """Return every ``python-version`` a workflow's setup-python steps request."""
    document = yaml.safe_load(workflow)
    return [
        str(step.get("with", {}).get("python-version", ""))
        for job in document.get("jobs", {}).values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(SETUP_PYTHON)
    ]


def rejected_versions(accepted: SpecifierSet, requested: list[str]) -> list[str]:
    """Return the requested versions the specifier set does not accept."""
    return [version for version in requested if Version(version) not in accepted]


@pytest.mark.parametrize("lane", LANES)
def test_the_lane_sets_up_a_python_the_project_accepts(lane: str) -> None:
    """Each coverage lane installs Python, and only a version the project accepts."""
    accepted = requires_python((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / lane).read_text(encoding="utf-8")
    requested = setup_python_versions(workflow)

    assert requested, f"{lane} must set up Python for the coverage run"
    assert rejected_versions(accepted, requested) == [], (
        f"{lane} sets up {requested}, outside requires-python {accepted}"
    )


@pytest.mark.parametrize(
    ("specifier", "requested", "rejected"),
    [
        (">=3.14", ["3.14"], []),
        (">=3.14", ["3.15"], []),
        (">=3.14", ["3.13"], ["3.13"]),
        (">=3.12,<3.14", ["3.14", "3.12"], ["3.14"]),
    ],
)
def test_the_check_rejects_exactly_the_versions_outside_the_range(
    specifier: str, requested: list[str], rejected: list[str]
) -> None:
    """The comparison is by version, in both directions of the range."""
    assert rejected_versions(SpecifierSet(specifier), requested) == rejected
