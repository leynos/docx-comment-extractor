"""Contract: each coverage run sets up a Python the project accepts.

generate-coverage builds its coverage environment on the Python the job put on
``PATH`` when nothing more specific names one. If ``actions/setup-python``
installs a version outside ``requires-python``, ``uv sync`` refuses the
interpreter and the coverage step fails. So every job that runs
generate-coverage must set Python up before that step, in the same job, and
only with versions the project accepts. A setup step in another job, or after
the coverage step, puts nothing on the coverage step's ``PATH``.
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
GENERATE_COVERAGE: typ.Final[str] = "/.github/actions/generate-coverage@"

#: Minimal steps for the fixture workflows the selection tests build.
SETUP: typ.Final[dict[str, object]] = {"uses": f"{SETUP_PYTHON}{'0' * 40}"}
COVERAGE: typ.Final[dict[str, object]] = {
    "uses": f"leynos/shared-actions{GENERATE_COVERAGE}{'0' * 40}"
}


def requires_python(pyproject: str) -> SpecifierSet:
    """Return the project's ``requires-python`` specifier set.

    Parameters
    ----------
    pyproject : str
        The text of ``pyproject.toml``.

    Returns
    -------
    SpecifierSet
        The versions the project declares it accepts.

    """
    return SpecifierSet(tomllib.loads(pyproject)["project"]["requires-python"])


def coverage_setups(workflow: str) -> dict[str, list[str]]:
    """Map each coverage job to the Python versions set up before its coverage step.

    Parameters
    ----------
    workflow : str
        The text of one workflow file.

    Returns
    -------
    dict of str to list of str
        For every job with a generate-coverage step, the ``python-version`` of
        each ``setup-python`` step that precedes it in that job.

    """
    document = yaml.safe_load(workflow)
    setups: dict[str, list[str]] = {}
    for name, job in document.get("jobs", {}).items():
        steps = [str(step.get("uses", "")) for step in job.get("steps", [])]
        coverage = [
            index for index, uses in enumerate(steps) if GENERATE_COVERAGE in uses
        ]
        if not coverage:
            continue
        setups[name] = [
            str(step.get("with", {}).get("python-version", ""))
            for step in job["steps"][: coverage[0]]
            if str(step.get("uses", "")).startswith(SETUP_PYTHON)
        ]
    return setups


def rejected_versions(accepted: SpecifierSet, requested: list[str]) -> list[str]:
    """Return the requested versions the specifier set does not accept.

    Parameters
    ----------
    accepted : SpecifierSet
        The project's ``requires-python``.
    requested : list of str
        Versions requested by ``setup-python`` steps.

    Returns
    -------
    list of str
        The requested versions outside ``accepted``, in their original order.

    """
    return [version for version in requested if Version(version) not in accepted]


@pytest.mark.parametrize("lane", LANES)
def test_each_coverage_job_sets_up_a_python_the_project_accepts(lane: str) -> None:
    """Every coverage job sets Python up first, and only within ``requires-python``."""
    accepted = requires_python((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / lane).read_text(encoding="utf-8")
    setups = coverage_setups(workflow)

    assert setups, f"{lane} must run generate-coverage"
    for job, requested in setups.items():
        assert requested, f"{lane}:{job} must set up Python before generate-coverage"
        assert rejected_versions(accepted, requested) == [], (
            f"{lane}:{job} sets up {requested}, outside requires-python {accepted}"
        )


def test_both_lanes_measure_on_one_python() -> None:
    """The pull-request ratchet compares against a baseline measured on the same Python.

    A figure measured on one interpreter is not comparable with one measured on
    another, so every coverage job in both lanes sets up one version.
    """
    requested = {
        version
        for lane in LANES
        for versions in coverage_setups(
            (ROOT / ".github" / "workflows" / lane).read_text(encoding="utf-8")
        ).values()
        for version in versions
    }

    assert len(requested) == 1, f"coverage lanes set up {sorted(requested)}"


def _workflow(jobs: dict[str, list[dict[str, object]]]) -> str:
    """Render a workflow with the given jobs' steps."""
    return yaml.safe_dump({
        "jobs": {name: {"steps": steps} for name, steps in jobs.items()}
    })


def _setup(version: str) -> dict[str, object]:
    """Return a setup-python step requesting ``version``."""
    return {**SETUP, "with": {"python-version": version}}


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        ({"cov": [_setup("3.14"), COVERAGE]}, {"cov": ["3.14"]}),
        ({"cov": [COVERAGE, _setup("3.14")]}, {"cov": []}),
        ({"other": [_setup("3.14")], "cov": [COVERAGE]}, {"cov": []}),
        ({"lint": [_setup("3.14")]}, {}),
    ],
    ids=["before-in-job", "after-the-step", "another-job", "no-coverage-job"],
)
def test_only_setups_before_the_coverage_step_count(
    jobs: dict[str, list[dict[str, object]]], expected: dict[str, list[str]]
) -> None:
    """A setup step counts only when it runs before coverage in the same job."""
    assert coverage_setups(_workflow(jobs)) == expected


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
