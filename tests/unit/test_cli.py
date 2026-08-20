"""Unit tests for command-line path validation."""

from __future__ import annotations

import logging
import types
import typing as typ
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from docx_comment_extractor import cli
from docx_comment_extractor.extractor import ExtractionError
from tests.support_documents import build_fixture


@pytest.mark.parametrize("alias_kind", ["same", "symlink", "hard-link"])
def test_extract_comments_rejects_output_alias_before_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
) -> None:
    """Input aliases should fail before document extraction can run."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = _build_output_alias(input_path, tmp_path, alias_kind)

    def fail_if_extracted(_path: Path) -> typ.NoReturn:
        pytest.fail("output alias validation must run before extraction")

    monkeypatch.setattr(cli, "extract_document", fail_if_extracted)

    with pytest.raises(
        cli.UserFacingError,
        match=r"Output path must not overwrite the input document\.",
    ):
        cli.extract_comments(input_path, output_path)


def test_extract_comments_preserves_output_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replacement must leave an existing output file unchanged."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"
    original_markdown = "existing output\n"
    output_path.write_text(original_markdown, encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> typ.NoReturn:
        """Simulate a filesystem failure during atomic replacement."""
        message = "replace failed"
        raise OSError(message)

    monkeypatch.setattr(
        cli,
        "os",
        types.SimpleNamespace(replace=fail_replace),
        raising=False,
    )

    with pytest.raises(cli.UserFacingError, match="Could not write"):
        cli.extract_comments(input_path, output_path)

    assert output_path.read_text(encoding="utf-8") == original_markdown, (
        "a failed atomic replacement should preserve the existing output"
    )
    assert not list(tmp_path.glob(f".{output_path.name}.*")), (
        "a failed atomic replacement should remove its temporary output"
    )


def test_write_output_removes_temp_when_writing_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A temporary file should be removed when its content write fails."""
    output_path = tmp_path / "output.md"
    temporary_path = tmp_path / ".output.md.write-failure.tmp"

    class FailingTemporaryFile:
        """Create a named file then fail while writing its content."""

        name = str(temporary_path)

        def __enter__(self) -> FailingTemporaryFile:
            temporary_path.touch()
            return self

        def __exit__(
            self,
            _exception_type: object,
            _exception: object,
            _traceback: object,
        ) -> None:
            return None

        @staticmethod
        def write(_markdown: str) -> typ.NoReturn:
            """Simulate a temporary-file write failure."""
            message = "write failed"
            raise OSError(message)

    monkeypatch.setattr(
        cli.tempfile,
        "NamedTemporaryFile",
        lambda *_args, **_kwargs: FailingTemporaryFile(),
    )

    with pytest.raises(cli.UserFacingError, match="Could not write"):
        cli._write_output_atomically(output_path, "Markdown")

    assert not temporary_path.exists(), (
        "a failed temporary-file write should remove the created temporary file"
    )


def test_validate_input_path_rejects_oversized_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should reject package files beyond its documented input limit."""
    input_path = tmp_path / "oversized.docx"
    input_path.write_bytes(b"xx")
    monkeypatch.setattr(cli, "MAX_INPUT_BYTES", 1, raising=False)

    with pytest.raises(cli.UserFacingError, match="too large"):
        cli._validate_input_path(input_path)


def test_run_extraction_uses_the_injected_output_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extraction orchestration should persist Markdown through its writer seam."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"
    persisted: list[tuple[Path, str]] = []
    monkeypatch.setattr(cli, "_print_success", lambda *_args: None)

    cli._run_extraction(
        input_path,
        output_path,
        metrics=cli.OperationMetrics(),
        output_writer=lambda path, markdown: persisted.append((path, markdown)),
    )

    assert persisted[0][0] == output_path, (
        "the injected output writer should receive the requested output path"
    )
    assert "{==" in persisted[0][1], (
        "the injected output writer should receive rendered CriticMarkup Markdown"
    )


def _build_output_alias(input_path: Path, tmp_path: Path, alias_kind: str) -> Path:
    """Create the requested input alias for output-path validation tests."""
    output_path = tmp_path / "output.docx"
    match alias_kind:
        case "same":
            return input_path
        case "symlink":
            output_path.symlink_to(input_path)
        case "hard-link":
            output_path.hardlink_to(input_path)
        case _:
            message = f"Unsupported output alias kind: {alias_kind}"
            raise ValueError(message)
    return output_path


def test_main_presents_extraction_errors_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Extraction failures should become a user-facing status-2 error."""

    def fail_app(**_kwargs: object) -> typ.NoReturn:
        """Simulate a package failure at the command boundary."""
        message = "Could not extract the Word document."
        raise ExtractionError(message)

    presented_errors: list[str] = []
    monkeypatch.setattr(cli, "APP", fail_app)
    monkeypatch.setattr(cli, "_print_error", presented_errors.append)

    with (
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(SystemExit, match="2"),
    ):
        cli.main([])

    assert presented_errors == ["Could not extract the Word document."], (
        "the CLI should present extraction failures without a traceback"
    )
    assert getattr(caplog.records[0], "operation", None) == "extraction", (
        "an extraction failure should identify its operation"
    )
    assert getattr(caplog.records[0], "error", None) == "ExtractionError", (
        "an extraction failure should expose only its safe error class"
    )


def test_main_records_cyclopts_failures_with_a_stable_category(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Argument parser failures should have a bounded observability category."""

    def fail_app(**_kwargs: object) -> typ.NoReturn:
        """Simulate a command-line parsing failure."""
        message = "invalid arguments"
        raise cli.CycloptsError(message)

    monkeypatch.setattr(cli, "APP", fail_app)
    monkeypatch.setattr(cli, "_print_error", lambda _message: None)

    with (
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(SystemExit, match="2"),
    ):
        cli.main([])

    assert getattr(caplog.records[0], "operation", None) == "argument_parsing", (
        "a parsing failure should identify the argument-parsing operation"
    )
    assert getattr(caplog.records[0], "error_category", None) == "argument_parsing", (
        "a parsing failure should expose a stable error category"
    )
    assert getattr(caplog.records[0], "duration_ms", None) is not None, (
        "a parsing failure should include its command duration metric"
    )


def test_extract_comments_emits_bounded_structured_events(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operational logs should expose fields without document payloads."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"

    with caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"):
        cli.extract_comments(input_path, output_path)

    operations = [getattr(record, "operation", None) for record in caplog.records]
    assert operations == ["validation", "extraction", "output_write"], (
        "the CLI should log each operational boundary once"
    )
    assert all(
        getattr(record, "outcome", None) == "success" for record in caplog.records
    ), "successful boundary events should expose a success outcome"
    assert all(
        str(input_path) not in record.getMessage() for record in caplog.records
    ), "structured events should not include raw input paths"


def test_extract_comments_records_warning_metrics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warnings should include bounded counts and duration metrics."""
    input_path = build_fixture("table-document", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"

    with caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"):
        cli.extract_comments(input_path, output_path)

    warning_record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "warning_summary"
    )
    assert getattr(warning_record, "warning_count", None) == 1, (
        "the warning summary metric should count the unsupported table warning"
    )
    assert getattr(warning_record, "operation_count", None) is not None, (
        "the warning summary should include an operation-outcome counter"
    )
    assert getattr(warning_record, "duration_ms", None) is not None, (
        "the warning summary should include a duration metric"
    )


def test_operation_metrics_are_isolated_and_resettable() -> None:
    """Each metrics owner should keep state local and support explicit reset."""
    first_metrics = cli.OperationMetrics()
    second_metrics = cli.OperationMetrics()

    first_metrics.record("validation", "success", 1.5)

    assert first_metrics.snapshot().operation_counts == {
        ("validation", "success"): 1
    }, "a metrics owner should retain its own operation count"
    assert second_metrics.snapshot().operation_counts == {}, (
        "separate metrics owners should not share operation counts"
    )

    first_metrics.reset()

    assert first_metrics.snapshot().operation_counts == {}, (
        "reset should clear the owner operation counts"
    )


def test_operation_metrics_records_concurrent_events() -> None:
    """Concurrent event records should produce an exact count and duration total."""
    metrics = cli.OperationMetrics()

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _index: metrics.record("validation", "success", 1.0), range(64)
            )
        )

    snapshot = metrics.snapshot()

    assert snapshot.operation_counts == {("validation", "success"): 64}, (
        "concurrent records should retain every operation outcome"
    )
    assert snapshot.duration_totals_ms == {"validation": 64.0}, (
        "concurrent records should retain the complete duration total"
    )


@pytest.mark.parametrize(
    "path_text",
    [
        "[link=https://example.test]input[/link]",
        "input\x1b]8;;https://example.test\x1b\\",
    ],
)
def test_error_output_renders_untrusted_input_paths_as_safe_text(
    path_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error output must display path text without Rich markup or control codes."""
    stream = StringIO()
    monkeypatch.setattr(cli, "STDERR_CONSOLE", Console(file=stream, width=200))

    cli._print_error(str(cli.UserFacingError.invalid_extension(Path(path_text))))

    rendered = stream.getvalue()
    assert str(Path(path_text)).replace("\x1b", "\\x1b") in rendered, (
        "error output should show untrusted input paths literally"
    )
    assert "\x1b]8" not in rendered, (
        "error output should neutralize terminal hyperlink control sequences"
    )


@pytest.mark.parametrize(
    "path_text",
    [
        "[link=https://example.test]output[/link]",
        "output\x1b]8;;https://example.test\x1b\\",
    ],
)
def test_success_output_renders_untrusted_output_paths_as_safe_text(
    path_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success output must display output paths without Rich markup or controls."""
    stream = StringIO()
    monkeypatch.setattr(cli, "STDERR_CONSOLE", Console(file=stream, width=200))

    cli._print_success(Path(path_text), comment_count=1, warning_count=0)

    rendered = stream.getvalue()
    assert str(Path(path_text)).replace("\x1b", "\\x1b") in rendered, (
        "success output should show untrusted output paths literally"
    )
    assert "\x1b]8" not in rendered, (
        "success output should neutralize terminal hyperlink control sequences"
    )


def test_parser_error_output_renders_untrusted_text_as_safe_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parser error output must not interpret markup or terminal control codes."""
    stream = StringIO()
    parser_message = (
        "[link=https://example.test]invalid[/link]\x1b]8;;https://example.test\x1b\\"
    )
    monkeypatch.setattr(cli, "STDERR_CONSOLE", Console(file=stream, width=200))

    cli._print_error(parser_message)

    rendered = stream.getvalue()
    assert "[link=https://example.test]invalid[/link]\\x1b]8" in rendered, (
        "parser errors should render untrusted text literally"
    )
    assert "\x1b]8" not in rendered, (
        "parser errors should neutralize terminal hyperlink control sequences"
    )
