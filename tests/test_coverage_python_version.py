"""Contract: each coverage call measures on a Python the project accepts.

generate-coverage builds its coverage environment on the Python the job put on
``PATH`` when nothing more specific names one. If ``actions/setup-python``
installs a version outside ``requires-python``, ``uv sync`` refuses the
interpreter and the coverage step fails; and if the two lanes measure on
different Pythons, the pull-request ratchet compares figures that are not
comparable. So every generate-coverage call must follow, in its own job, a
``setup-python`` step naming a version the project accepts, and every call in
both lanes must measure on one version. The Python a call measures on is the
one the most recent setup before it put on ``PATH``; a setup in another job,
or after the call, does not count.
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


def coverage_pythons(workflow: str) -> list[tuple[str, str]]:
    """Return the Python on ``PATH`` at every generate-coverage step.

    Each job's steps are read in order. A ``setup-python`` step puts its
    ``python-version`` on ``PATH`` for the steps after it, replacing any
    earlier one, so each coverage call measures on the most recent setup before
    it in the same job. A setup in another job, or after the call, does not
    count.

    Parameters
    ----------
    workflow : str
        The text of one workflow file.

    Returns
    -------
    list of tuple of (str, str)
        One ``(job, version)`` pair per coverage call, in workflow order. The
        version is empty when no setup precedes the call in its job, or the
        setup names no ``python-version``.

    """
    document = yaml.safe_load(workflow)
    runs: list[tuple[str, str]] = []
    for name, job in document.get("jobs", {}).items():
        on_path = ""
        for step in job.get("steps", []):
            uses = str(step.get("uses", ""))
            if uses.startswith(SETUP_PYTHON):
                on_path = str((step.get("with") or {}).get("python-version") or "")
            elif GENERATE_COVERAGE in uses:
                runs.append((name, on_path))
    return runs


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
def test_each_coverage_call_measures_on_a_python_the_project_accepts(
    lane: str,
) -> None:
    """Every coverage call measures on a named Python within ``requires-python``."""
    accepted = requires_python((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / lane).read_text(encoding="utf-8")
    runs = coverage_pythons(workflow)

    assert runs, f"{lane} must run generate-coverage"
    unnamed = [job for job, version in runs if not version]
    assert unnamed == [], (
        f"{lane}: these jobs reach generate-coverage without a setup-python "
        f"step naming a python-version: {unnamed}"
    )
    requested = [version for _job, version in runs]
    assert rejected_versions(accepted, requested) == [], (
        f"{lane} measures on {requested}, outside requires-python {accepted}"
    )


def test_both_lanes_measure_on_one_python() -> None:
    """The pull-request ratchet compares against a baseline measured on the same Python.

    A figure measured on one interpreter is not comparable with one measured on
    another, so every coverage call in both lanes measures on one version.
    """
    requested = {
        version
        for lane in LANES
        for _job, version in coverage_pythons(
            (ROOT / ".github" / "workflows" / lane).read_text(encoding="utf-8")
        )
    }

    assert len(requested) == 1, f"coverage lanes measure on {sorted(requested)}"


def _workflow(jobs: dict[str, list[dict[str, object]]]) -> str:
    """Render a workflow with the given jobs' steps."""
    # Keep the jobs in the order given: a setup in an earlier job must not
    # leak into a later one, and sorting would put "cov" before "other".
    return yaml.safe_dump(
        {"jobs": {name: {"steps": steps} for name, steps in jobs.items()}},
        sort_keys=False,
    )


def _setup(version: str) -> dict[str, object]:
    """Return a setup-python step requesting ``version``."""
    return {**SETUP, "with": {"python-version": version}}


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        ({"cov": [_setup("3.14"), COVERAGE]}, [("cov", "3.14")]),
        ({"cov": [COVERAGE, _setup("3.14")]}, [("cov", "")]),
        ({"other": [_setup("3.14")], "cov": [COVERAGE]}, [("cov", "")]),
        ({"lint": [_setup("3.14")]}, []),
        (
            {"cov": [_setup("3.13"), COVERAGE, _setup("3.14"), COVERAGE]},
            [("cov", "3.13"), ("cov", "3.14")],
        ),
        ({"cov": [SETUP, COVERAGE]}, [("cov", "")]),
    ],
    ids=[
        "before-in-job",
        "after-the-step",
        "another-job",
        "no-coverage-job",
        "latest-setup-per-call",
        "setup-without-version",
    ],
)
def test_each_call_reads_the_latest_setup_before_it(
    jobs: dict[str, list[dict[str, object]]], expected: list[tuple[str, str]]
) -> None:
    """A call measures on its job's most recent named setup, or on nothing."""
    assert coverage_pythons(_workflow(jobs)) == expected


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
