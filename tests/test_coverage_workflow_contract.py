"""Contract tests for main-owned Python coverage publication."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
COVERAGE_WORKFLOW = ROOT / ".github" / "workflows" / "coverage-main.yml"
GENERATE_COVERAGE_ACTION = (
    "leynos/shared-actions/.github/actions/generate-coverage"
    "@152d9c4784d0ae5877938a984fe6d1f04d718fd8"
)
UPLOAD_COVERAGE_ACTION = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage"
    "@152d9c4784d0ae5877938a984fe6d1f04d718fd8"
)


def _text(path: Path) -> str:
    """Read a workflow as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def _step_uses(workflow: str, step_name: str) -> str:
    """Return the pinned ``uses`` value from a named workflow step."""
    match = re.search(
        rf"- name: {re.escape(step_name)}\n(?:.*\n)*?\s+uses: ([^\n]+)",
        workflow,
    )
    assert match is not None, f"workflow step {step_name!r} is missing"
    return match.group(1).strip()


def _step_block(workflow: str, step_name: str) -> str:
    """Return the text from a named step through the next named step."""
    start = workflow.index(f"      - name: {step_name}")
    remainder = workflow[start:]
    next_step = remainder.find("\n      - name:", len(step_name))
    return remainder if next_step < 0 else remainder[:next_step]


def test_pull_request_coverage_is_local_serial_ratchet() -> None:
    """Keep pull-request coverage local and independent of CodeScene."""
    workflow = _text(CI_WORKFLOW)
    assert "CS_ACCESS_TOKEN" not in workflow
    assert "codescene" not in workflow.lower()
    assert "upload-codescene-coverage" not in workflow
    assert "cs-coverage" not in workflow
    assert "fetch-depth: 0" not in workflow

    coverage = _step_block(workflow, "Generate coverage")
    assert "if: github.event_name == 'pull_request'" in coverage
    assert _step_uses(workflow, "Generate coverage") == GENERATE_COVERAGE_ACTION
    assert "language: python" in coverage
    assert "python-source: docx_comment_extractor" in coverage
    assert "baseline-python-file: .coverage-baseline.python" in coverage
    assert "pytest-workers: ''" in coverage
    assert "with-ratchet: 'true'" in coverage


def test_main_coverage_publishes_the_ratchet_baseline() -> None:
    """Keep CodeScene publication restricted to pushes on main."""
    workflow = _text(COVERAGE_WORKFLOW)
    trigger_block = workflow[workflow.index("on:\n") : workflow.index("jobs:\n")]
    assert trigger_block == "on:\n  push:\n    branches: [main]\n\n"
    assert "pull_request" not in trigger_block
    assert "CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN || '' }}" in workflow

    coverage = _step_block(workflow, "Generate coverage")
    assert _step_uses(workflow, "Generate coverage") == GENERATE_COVERAGE_ACTION
    assert "language: python" in coverage
    assert "python-source: docx_comment_extractor" in coverage
    assert "baseline-python-file: .coverage-baseline.python" in coverage
    assert "pytest-workers: ''" in coverage
    assert "with-ratchet: 'true'" in coverage

    upload = _step_block(workflow, "Upload coverage data to CodeScene")
    assert _step_uses(workflow, "Upload coverage data to CodeScene") == (
        UPLOAD_COVERAGE_ACTION
    )
    assert "mode: upload" in upload
