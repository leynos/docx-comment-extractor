"""Contract: every coverage call measures on one declared Python the project accepts.

generate-coverage chooses its interpreter in a fixed order: its own
``python-version`` input, then ``UV_PYTHON``, then the first entry of
``.python-version``, then the ``python3`` the job put on ``PATH``, which is the
most recent ``actions/setup-python`` step before the call in its job. If the
chosen version is outside ``requires-python``, ``uv sync`` refuses it and the
coverage step fails; and if two calls measure on different Pythons, the
pull-request ratchet compares figures that are not comparable.

So every call must declare at least one of those sources, every source it
declares must name the same version (a higher-priority value silently
overriding a lower one is how lanes drift), that version must be inside
``requires-python``, and every call in every workflow must measure on it. A
setup step guarded by ``if:`` or allowed to fail with ``continue-on-error``
may not run, so it declares nothing.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml
from coverage_python_sources import (
    LANES,
    ROOT,
    SETUP_PYTHON,
    WORKFLOWS,
    CoverageCall,
    coverage_calls,
    python_version_entry,
    rejected_versions,
    requires_python,
)
from hypothesis import given
from hypothesis import strategies as st
from packaging.specifiers import SpecifierSet

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Minimal steps for the fixture workflows the selection tests build.
SETUP: typ.Final[dict[str, object]] = {"uses": f"{SETUP_PYTHON}{'0' * 40}"}
COVERAGE: typ.Final[dict[str, object]] = {
    "uses": f"leynos/shared-actions/.github/actions/generate-coverage@{'0' * 40}"
}


def _repository_calls() -> dict[str, list[CoverageCall]]:
    """Return every workflow's coverage calls, keyed by workflow file name."""
    python_version = python_version_entry(ROOT / ".python-version")
    paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    return {
        path.name: coverage_calls(path.read_text(encoding="utf-8"), python_version)
        for path in paths
    }


def test_both_lanes_call_generate_coverage() -> None:
    """The pull-request lane and the publisher each measure coverage."""
    calls = _repository_calls()

    assert all(calls.get(lane) for lane in LANES), (
        f"{LANES} must each call generate-coverage"
    )


def test_every_call_declares_one_accepted_python() -> None:
    """Each call names a Python, every source agrees, and the project accepts it."""
    accepted = requires_python((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for workflow, calls in _repository_calls().items():
        for call in calls:
            where = f"{workflow}:{call.job}"
            assert call.declared, f"{where} measures on an undeclared Python"
            assert len(set(call.declared.values())) == 1, (
                f"{where} declares conflicting versions: {call.declared}"
            )
            assert rejected_versions(accepted, [call.effective]) == [], (
                f"{where} measures on {call.effective}, outside {accepted}"
            )


def test_every_call_measures_on_one_python() -> None:
    """The pull-request ratchet compares against a baseline measured on the same Python.

    A figure measured on one interpreter is not comparable with one measured on
    another, so every call in every workflow measures on one version.
    """
    effective = {
        call.effective for calls in _repository_calls().values() for call in calls
    }

    assert len(effective) == 1, f"coverage calls measure on {sorted(effective)}"


def _workflow(
    jobs: cabc.Mapping[str, object], env: dict[str, str] | None = None
) -> str:
    """Render a workflow with the given jobs, keeping their order."""
    document: dict[str, object] = {"jobs": dict(jobs)}
    if env:
        document["env"] = env
    return yaml.safe_dump(document, sort_keys=False)


def _setup(version: str, **extra: object) -> dict[str, object]:
    """Return a setup-python step requesting ``version``."""
    return {**SETUP, "with": {"python-version": version}, **extra}


def _steps(*steps: dict[str, object]) -> dict[str, object]:
    """Return a job with the given steps."""
    return {"steps": list(steps)}


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        ({"cov": _steps(_setup("3.14"), COVERAGE)}, ["3.14"]),
        ({"cov": _steps(COVERAGE, _setup("3.14"))}, [""]),
        ({"other": _steps(_setup("3.14")), "cov": _steps(COVERAGE)}, [""]),
        ({"lint": _steps(_setup("3.14"))}, []),
        (
            {"cov": _steps(_setup("3.13"), COVERAGE, _setup("3.14"), COVERAGE)},
            ["3.13", "3.14"],
        ),
        ({"cov": _steps(SETUP, COVERAGE)}, [""]),
        ({"cov": _steps(_setup("3.14", **{"if": "false"}), COVERAGE)}, [""]),
        (
            {"cov": _steps(_setup("3.14", **{"continue-on-error": True}), COVERAGE)},
            [""],
        ),
        (
            {
                "cov": _steps(
                    _setup("3.14"), {"uses": "./.github/actions/generate-coverage"}
                )
            },
            ["3.14"],
        ),
        (
            {
                "cov": _steps(
                    _setup("3.14"), {"uses": "$/.github/actions/generate-coverage"}
                )
            },
            ["3.14"],
        ),
    ],
    ids=[
        "before-in-job",
        "after-the-step",
        "another-job",
        "no-coverage-job",
        "latest-setup-per-call",
        "setup-without-version",
        "conditional-setup",
        "fail-green-setup",
        "relative-action",
        "same-repository-action",
    ],
)
def test_each_call_reads_the_setup_that_reliably_precedes_it(
    jobs: dict[str, object], expected: list[str]
) -> None:
    """A call's setup-python source is its job's latest unconditional setup."""
    calls = coverage_calls(_workflow(jobs))

    assert [call.sources["setup-python"] for call in calls] == expected


class SourceCase(typ.NamedTuple):
    """Where a higher-priority source is declared, and what the call records."""

    step: dict[str, object]
    job_env: dict[str, str]
    workflow_env: dict[str, str]
    python_version: str
    declared: dict[str, str]


@pytest.mark.parametrize(
    "case",
    [
        SourceCase(
            {**COVERAGE, "with": {"python-version": "3.13"}},
            {},
            {},
            "",
            {"input": "3.13", "setup-python": "3.14"},
        ),
        SourceCase(
            COVERAGE,
            {"UV_PYTHON": "3.13"},
            {},
            "",
            {"UV_PYTHON": "3.13", "setup-python": "3.14"},
        ),
        SourceCase(
            {**COVERAGE, "env": {"UV_PYTHON": "3.12"}},
            {"UV_PYTHON": "3.13"},
            {},
            "",
            {"UV_PYTHON": "3.12", "setup-python": "3.14"},
        ),
        SourceCase(
            COVERAGE,
            {},
            {"UV_PYTHON": "3.13"},
            "",
            {"UV_PYTHON": "3.13", "setup-python": "3.14"},
        ),
        SourceCase(
            COVERAGE,
            {},
            {},
            "3.12",
            {".python-version": "3.12", "setup-python": "3.14"},
        ),
    ],
    ids=[
        "input",
        "job-uv-python",
        "step-uv-python-wins",
        "workflow-uv-python",
        "python-version-file",
    ],
)
def test_higher_priority_sources_are_declared_beside_the_setup(
    case: SourceCase,
) -> None:
    """Every source the resolver reads is recorded, so a conflict is visible."""
    job = {
        **_steps(_setup("3.14"), case.step),
        **({"env": case.job_env} if case.job_env else {}),
    }
    (call,) = coverage_calls(
        _workflow({"cov": job}, case.workflow_env), case.python_version
    )

    assert call.declared == case.declared
    assert call.effective == next(iter(case.declared.values()))


_STEP = st.one_of(
    st.tuples(
        st.just("setup"), st.sampled_from(["3.12", "3.13", "3.14", ""]), st.booleans()
    ),
    st.tuples(st.just("coverage"), st.just(""), st.just(value=False)),
    st.tuples(st.just("run"), st.just(""), st.just(value=False)),
)


def _render(kind: str, version: str, *, guarded: bool) -> dict[str, object]:
    """Return a fixture step for one drawn step description."""
    if kind == "setup":
        return _setup(version, **({"if": "always()"} if guarded else {}))
    return COVERAGE if kind == "coverage" else {"run": "make lint"}


@given(st.lists(st.lists(_STEP, max_size=8), min_size=1, max_size=4))
def test_the_setup_source_matches_a_naive_reading(
    jobs: list[list[tuple[str, str, bool]]],
) -> None:
    """For any jobs, each call's setup source is its job's latest unguarded setup."""
    expected: list[str] = []
    for steps in jobs:
        on_path = ""
        for kind, version, guarded in steps:
            if kind == "setup":
                on_path = "" if guarded else version
            elif kind == "coverage":
                expected.append(on_path)
    rendered = {
        f"job{index}": _steps(*(_render(k, v, guarded=g) for k, v, g in steps))
        for index, steps in enumerate(jobs)
    }

    calls = coverage_calls(_workflow(rendered))

    assert [call.sources["setup-python"] for call in calls] == expected


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
