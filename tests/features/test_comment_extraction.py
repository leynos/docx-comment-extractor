"""Behavioural tests for the command-line interface."""

from __future__ import annotations

import subprocess  # noqa: S404  # Required for behavioural CLI process coverage.
import sys
import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from tests.support_documents import build_fixture

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

scenarios("comment_extraction.feature")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CommandResult(typ.NamedTuple):
    """Capture a CLI invocation result."""

    returncode: int
    stdout: str
    stderr: str


@given(
    parsers.parse('a "{fixture_name}" fixture document'), target_fixture="document_path"
)
def given_fixture_document(tmp_path: Path, fixture_name: str) -> Path:
    """Create a synthetic input document for a scenario."""
    return build_fixture(fixture_name, tmp_path / f"{fixture_name}.docx")


@given("a missing fixture document path", target_fixture="document_path")
def given_missing_document_path(tmp_path: Path) -> Path:
    """Provide a missing input path."""
    return tmp_path / "missing.docx"


@given("an invalid-extension document path", target_fixture="document_path")
def given_invalid_extension_document_path(tmp_path: Path) -> Path:
    """Provide an existing input with an unsupported extension."""
    document_path = tmp_path / "invalid.txt"
    document_path.write_text("not a Word document", encoding="utf-8")
    return document_path


@given("a directory document path", target_fixture="document_path")
def given_directory_document_path(tmp_path: Path) -> Path:
    """Provide a `.docx` path that exists but is not a file."""
    document_path = tmp_path / "directory.docx"
    document_path.mkdir()
    return document_path


@when("I run the extractor CLI on the document", target_fixture="command_result")
def when_run_cli(document_path: Path) -> CommandResult:
    """Run the CLI and capture its output."""
    process = subprocess.run(  # noqa: S603  # Fixed argv with pytest-owned paths.
        [sys.executable, "-m", "docx_comment_extractor.cli", str(document_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return CommandResult(
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


@when("I run the extractor CLI with an output file", target_fixture="command_result")
def when_run_cli_with_output(document_path: Path, tmp_path: Path) -> CommandResult:
    """Run the CLI and write its output to a file."""
    output_path = tmp_path / "output.md"
    process = subprocess.run(  # noqa: S603  # Fixed argv with pytest-owned paths.
        [
            sys.executable,
            "-m",
            "docx_comment_extractor.cli",
            str(document_path),
            "--output",
            str(output_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return CommandResult(
        returncode=process.returncode,
        stdout=str(output_path),
        stderr=process.stderr,
    )


@then("the command exits successfully")
def then_command_succeeds(command_result: CommandResult) -> None:
    """Verify a successful exit code."""
    assert command_result.returncode == 0, (
        "the CLI success scenario should exit with status 0"
    )


@then("the command exits with an error")
def then_command_fails(command_result: CommandResult) -> None:
    """Verify a failing exit code."""
    assert command_result.returncode != 0, "the CLI error scenario should exit non-zero"


@then(parsers.parse('standard output matches the "{snapshot_name}" snapshot'))
def then_stdout_matches_snapshot(
    command_result: CommandResult,
    snapshot: SnapshotAssertion,
    snapshot_name: str,
) -> None:
    """Compare stdout against the approved snapshot."""
    assert command_result.stdout == snapshot(name=snapshot_name), (
        "standard output should match the approved CLI snapshot"
    )


@then(parsers.parse('the output file matches the "{snapshot_name}" snapshot'))
def then_output_file_matches_snapshot(
    command_result: CommandResult,
    snapshot: SnapshotAssertion,
    snapshot_name: str,
) -> None:
    """Compare the written output file against the approved snapshot."""
    output_path = Path(command_result.stdout)
    assert output_path.read_text(encoding="utf-8") == snapshot(name=snapshot_name), (
        "the output file should match the approved CLI snapshot"
    )


@then("standard error is empty")
def then_stderr_is_empty(command_result: CommandResult) -> None:
    """Ensure no extra terminal output leaked on success-to-stdout runs."""
    assert command_result.stderr == "", (
        "successful stdout mode should keep stderr empty"
    )


@then(parsers.parse('standard error contains "{text}"'))
def then_stderr_contains(command_result: CommandResult, text: str) -> None:
    """Verify stderr output contains expected text."""
    assert text in command_result.stderr, (
        "stderr should contain the expected user-facing text"
    )
