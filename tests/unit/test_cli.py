"""Unit tests for command-line path validation."""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess  # noqa: S404  # Needed to prove import-time side effects.
import sys
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# A fresh interpreter reports what importing the CLI does to logging state and
# to the terminal seams. Absolute values here are the pristine defaults, so any
# handler, level, or pinned console the import adds shows up as a difference.
_IMPORT_PROBE = """
import json
import logging

root = logging.getLogger()
before = {"handlers": len(root.handlers), "level": root.level}

import docx_comment_extractor.cli as cli  # noqa: E402

print(json.dumps({
    "root_before": before,
    "root_after": {"handlers": len(root.handlers), "level": root.level},
    "cli_handlers": len(cli.LOGGER.handlers),
    "cli_level": cli.LOGGER.level,
    "module_consoles": [
        name
        for name in ("STDOUT_CONSOLE", "STDERR_CONSOLE")
        if hasattr(cli, name)
    ],
}))
"""


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


def test_validate_input_path_wraps_filesystem_failures(tmp_path: Path) -> None:
    """Input metadata failures should remain user-facing validation errors."""
    input_path = tmp_path / "input.docx"
    input_path.touch()

    def fail_to_measure(_path: Path) -> typ.NoReturn:
        message = "stat failed"
        raise OSError(message)

    with pytest.raises(cli.UserFacingError, match="Could not inspect"):
        cli._validate_input_path(input_path, file_size=fail_to_measure)


def test_validate_output_path_wraps_filesystem_failures(tmp_path: Path) -> None:
    """Output alias metadata failures should remain user-facing errors."""
    input_path = tmp_path / "input.docx"
    output_path = tmp_path / "output.md"

    def fail_to_compare(_input: Path, _output: Path) -> typ.NoReturn:
        message = "samefile failed"
        raise OSError(message)

    with pytest.raises(cli.UserFacingError, match="Could not inspect"):
        cli._validate_output_path(
            input_path,
            output_path,
            paths_refer_to_same_file=fail_to_compare,
        )


def test_duration_uses_the_injected_monotonic_clock() -> None:
    """Duration calculation should not depend on the process-wide clock."""
    assert cli._duration_ms(10.0, clock=lambda: 10.125) == 125.0, (
        "the injected clock should determine the reported elapsed duration"
    )


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
        dependencies=cli._RuntimeDependencies(
            metrics=cli.OperationMetrics(),
            output_writer=lambda path, markdown: persisted.append((path, markdown)),
            clock=lambda: 0.0,
            input_validator=cli._validate_input_path,
            output_validator=cli._validate_output_path,
            stdout=lambda _markdown: None,
            stdout_console=Console(file=StringIO()),
            stderr_console=Console(file=StringIO(), stderr=True),
        ),
    )

    assert persisted[0][0] == output_path, (
        "the injected output writer should receive the requested output path"
    )
    assert "{==" in persisted[0][1], (
        "the injected output writer should receive rendered CriticMarkup Markdown"
    )


def test_run_extraction_writes_stdout_through_the_injected_stream(
    tmp_path: Path,
) -> None:
    """Standard-output runs should use the injected stream, not the global."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    written: list[str] = []

    cli._run_extraction(
        input_path,
        None,
        dependencies=cli._RuntimeDependencies(
            metrics=cli.OperationMetrics(),
            output_writer=lambda _path, _markdown: None,
            clock=lambda: 0.0,
            input_validator=cli._validate_input_path,
            output_validator=cli._validate_output_path,
            stdout=written.append,
            stdout_console=Console(file=StringIO()),
            stderr_console=Console(file=StringIO(), stderr=True),
        ),
    )

    assert len(written) == 1, "a single stdout run should write the rendered once"
    assert "{==" in written[0], (
        "the injected stream should receive the rendered CriticMarkup Markdown"
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


def _fixed_ticks_clock(ticks: tuple[float, ...]) -> typ.Callable[[], float]:
    """Return a clock that yields ``ticks`` in order and then fails loudly."""

    def clock() -> float:
        try:
            return remaining.pop(0)
        except IndexError:
            pytest.fail("the injected clock was read more times than expected")

    remaining = list(ticks)
    return clock


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
    metrics = cli.OperationMetrics()
    monkeypatch.setattr(cli, "_build_app", lambda _dependencies: fail_app)
    monkeypatch.setattr(cli, "_print_error", presented_errors.append)

    with (
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(SystemExit, match="2"),
    ):
        cli.main([], metrics=metrics, clock=_fixed_ticks_clock((10.0, 10.25)))

    assert presented_errors == ["Could not extract the Word document."], (
        "the CLI should present extraction failures without a traceback"
    )
    assert getattr(caplog.records[0], "operation", None) == "extraction", (
        "an extraction failure should identify its operation"
    )
    assert getattr(caplog.records[0], "error", None) == "ExtractionError", (
        "an extraction failure should expose only its safe error class"
    )
    assert metrics.snapshot().operation_counts == {("extraction", "failure"): 1}, (
        "the injected metrics owner should count the terminal extraction failure"
    )
    assert metrics.snapshot().duration_totals_ms == {"extraction": 250.0}, (
        "the injected clock should determine the recorded failure duration"
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

    metrics = cli.OperationMetrics()
    monkeypatch.setattr(cli, "_build_app", lambda _dependencies: fail_app)
    monkeypatch.setattr(cli, "_print_error", lambda _message: None)

    with (
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(SystemExit, match="2"),
    ):
        cli.main([], metrics=metrics, clock=_fixed_ticks_clock((10.0, 10.25)))

    assert getattr(caplog.records[0], "operation", None) == "argument_parsing", (
        "a parsing failure should identify the argument-parsing operation"
    )
    assert getattr(caplog.records[0], "error_category", None) == "argument_parsing", (
        "a parsing failure should expose a stable error category"
    )
    assert getattr(caplog.records[0], "duration_ms", None) is not None, (
        "a parsing failure should include its command duration metric"
    )
    assert metrics.snapshot().operation_counts == {
        ("argument_parsing", "failure"): 1
    }, "the injected metrics owner should count the terminal parsing failure"
    assert metrics.snapshot().duration_totals_ms == {"argument_parsing": 250.0}, (
        "the injected clock should determine the recorded parsing failure duration"
    )


def test_main_injects_one_runtime_bundle_into_the_command_path(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Command-path events should reach the metrics and clock injected into main."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"
    metrics = cli.OperationMetrics()

    with (
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(SystemExit, match="0"),
    ):
        cli.main(
            [str(input_path), str(output_path)],
            metrics=metrics,
            clock=_fixed_ticks_clock(
                (10.0, 10.25, 10.5, 10.75, 11.0, 11.25, 11.5),
            ),
        )

    snapshot = metrics.snapshot()
    assert snapshot.operation_counts == {
        ("validation", "success"): 1,
        ("extraction", "success"): 1,
        ("output_write", "success"): 1,
    }, "every command-path boundary should record into the injected owner"
    assert snapshot.duration_totals_ms == {
        "validation": 250.0,
        "extraction": 250.0,
        "output_write": 250.0,
    }, "the injected clock should determine every recorded boundary duration"
    assert getattr(caplog.records[0], "duration_total_ms", None) == 250.0, (
        "the terminal event should be the second reading of the injected clock"
    )


def test_importing_the_cli_pins_no_streams_and_touches_no_logging_config() -> None:
    """Import must not capture terminal streams or configure logging state."""
    probe = subprocess.run(  # noqa: S603  # Fixed argv, module name is literal.
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    observed = json.loads(probe.stdout)

    assert observed["module_consoles"] == [], (
        "no module-level console should capture the import-time streams"
    )
    assert observed["cli_handlers"] == 0, (
        "importing the CLI should add no handler to the package logger"
    )
    assert observed["cli_level"] == logging.NOTSET, (
        "the package logger should keep its level unset for embedders"
    )
    assert observed["root_after"] == observed["root_before"], (
        "importing the CLI should not change root logger configuration"
    )


def test_main_writes_terminal_output_to_the_current_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal output must target the streams in place at call time."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"
    stdout_stream = StringIO()
    stderr_stream = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_stream)
    monkeypatch.setattr(sys, "stderr", stderr_stream)

    with pytest.raises(SystemExit, match="0"):
        cli.main(
            [str(input_path), "--output", str(output_path)],
            clock=lambda: 0.0,
        )

    assert "Wrote Markdown" in stderr_stream.getvalue(), (
        "the success summary should reach the stream replacing sys.stderr"
    )
    assert output_path.exists(), (
        "a run against replaced streams should still write its output file"
    )


def test_main_help_reaches_the_current_stdout_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cyclopts help must render into the stream in place at call time."""
    stdout_stream = StringIO()
    stderr_stream = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_stream)
    monkeypatch.setattr(sys, "stderr", stderr_stream)

    with pytest.raises(SystemExit, match="0"):
        cli.main(["--help"], clock=lambda: 0.0)

    assert "Usage" in stdout_stream.getvalue(), (
        "help text should reach the stream replacing sys.stdout"
    )


def test_main_errors_reach_the_current_stderr_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal failures must render into the stream in place at call time."""
    stdout_stream = StringIO()
    stderr_stream = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_stream)
    monkeypatch.setattr(sys, "stderr", stderr_stream)

    with pytest.raises(SystemExit, match="2"):
        cli.main([str(tmp_path / "missing.docx")], clock=lambda: 0.0)

    assert "does not exist" in stderr_stream.getvalue(), (
        "user-facing errors should reach the stream replacing sys.stderr"
    )


@contextlib.contextmanager
def _restored_cli_log_state() -> typ.Iterator[logging.Logger]:
    """Yield the CLI logger and restore its handlers, level, and sink marker."""
    cli_logger = cli.LOGGER
    handlers_before = list(cli_logger.handlers)
    level_before = cli_logger.level
    sink_before = getattr(cli_logger, cli._LOG_SINK_ATTRIBUTE, None)
    try:
        yield cli_logger
    finally:
        for handler in list(cli_logger.handlers):
            if handler not in handlers_before:
                cli_logger.removeHandler(handler)
        cli_logger.setLevel(level_before)
        if sink_before is None:
            with contextlib.suppress(AttributeError):
                del cli_logger.__dict__[cli._LOG_SINK_ATTRIBUTE]
        else:
            cli_logger.__dict__[cli._LOG_SINK_ATTRIBUTE] = sink_before


@contextlib.contextmanager
def _capturing_streams() -> typ.Iterator[tuple[StringIO, StringIO]]:
    """Yield replacement stdout/stderr streams and restore the originals."""
    stdout_stream, stderr_stream = StringIO(), StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_stream, stderr_stream
    try:
        yield stdout_stream, stderr_stream
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr


def test_main_installs_one_cli_log_handler_per_process(
    tmp_path: Path,
) -> None:
    """A verbose run should install exactly one idempotent CLI-scoped sink."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    with _restored_cli_log_state() as cli_logger:
        handlers_before = list(cli_logger.handlers)

        with pytest.raises(SystemExit, match="0"):
            cli.main([str(input_path), "--verbose"], clock=lambda: 0.0)
        installed = [
            handler for handler in cli_logger.handlers if handler not in handlers_before
        ]
        assert len(installed) == 1, (
            "a verbose run should install exactly one CLI log handler"
        )

        with pytest.raises(SystemExit, match="0"):
            cli.main([str(input_path), "--verbose"], clock=lambda: 0.0)

        assert cli_logger.handlers == [*handlers_before, *installed], (
            "a second verbose run should reuse the installed handler"
        )


def test_verbose_runs_emit_bounded_events_to_stderr(
    tmp_path: Path,
) -> None:
    """The opt-in sink should expose bounded fields, never payloads."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.md"
    with (
        _restored_cli_log_state(),
        _capturing_streams() as (_stdout_stream, stderr_stream),
        pytest.raises(SystemExit, match="0"),
    ):
        cli.main(
            [str(input_path), "--output", str(output_path), "--verbose"],
            clock=lambda: 0.0,
        )

    payload = stderr_stream.getvalue()
    assert "CLI operation completed" in payload, (
        "the opt-in sink should present the structured event message"
    )
    assert "operation=validation" in payload, (
        "the opt-in sink should expose the bounded operation field"
    )
    assert "Wrote Markdown" in payload, (
        "opt-in verbosity should not displace the user-facing summary"
    )
    for sensitive_value in (str(input_path), "commented text", "Sam C"):
        assert sensitive_value not in payload, (
            "opt-in events should not expose paths or document content"
        )


def test_verbosity_does_not_leak_into_the_next_invocation(
    tmp_path: Path,
) -> None:
    """A default run after a verbose one must still keep stderr empty.

    The sink must be inert again on the default path, and it must not keep
    pointing at the previous run's stream. Checking the earlier stream for
    late events is what makes this decisive: a merely detached handler would
    otherwise leave the fresh stream empty and look like a pass.
    """
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    with _restored_cli_log_state():
        with (
            _capturing_streams() as (_verbose_stdout, verbose_stderr),
            pytest.raises(SystemExit, match="0"),
        ):
            cli.main([str(input_path), "--verbose"], clock=lambda: 0.0)
        assert "operation=validation" in verbose_stderr.getvalue(), (
            "the verbose run should have opened the sink"
        )
        events_after_verbose = verbose_stderr.getvalue().count(
            "CLI operation completed"
        )

        with (
            _capturing_streams() as (default_stdout, default_stderr),
            pytest.raises(SystemExit, match="0"),
        ):
            cli.main([str(input_path)], clock=lambda: 0.0)

    assert "{==commented text==}" in default_stdout.getvalue(), (
        "the default run should still render Markdown to standard output"
    )
    assert default_stderr.getvalue() == "", (
        "a later default run must not inherit the previous run's verbosity"
    )
    assert verbose_stderr.getvalue().count("CLI operation completed") == (
        events_after_verbose
    ), "the default run must not append events to the previous run's stream"


def test_verbose_run_restores_the_logger_level_it_inherited(
    tmp_path: Path,
) -> None:
    """The opt-in must widen, then restore, whatever level it found."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    with _restored_cli_log_state() as cli_logger:
        cli_logger.setLevel(logging.ERROR)
        with (
            _capturing_streams(),
            pytest.raises(SystemExit, match="0"),
        ):
            cli.main([str(input_path), "--verbose"], clock=lambda: 0.0)
        assert cli_logger.level == logging.ERROR, (
            "the run should restore the level it found, not the package default"
        )


def test_events_stay_inert_when_the_opt_in_sink_is_absent(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The default run should emit nothing at all to standard error."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    with (
        _capturing_streams() as (stdout_stream, stderr_stream),
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(SystemExit, match="0"),
    ):
        cli.main([str(input_path)], clock=lambda: 0.0)

    assert "{==commented text==}" in stdout_stream.getvalue(), (
        "the default run should still render Markdown to standard output"
    )
    assert [getattr(record, "operation", None) for record in caplog.records] == [
        "validation",
        "extraction",
        "output_write",
    ], "the default run should still emit its structured events"
    assert stderr_stream.getvalue() == "", (
        "the default run should leave standard error empty"
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
        sensitive_value not in str(field_value)
        for record in caplog.records
        for field_value in record.__dict__.values()
        for sensitive_value in (
            str(input_path),
            "commented text",
            "Sam C",
            "Needs evidence.",
        )
    ), "structured events should not expose paths or document and comment content"


def test_stdout_writes_emit_output_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Successful standard-output writes should be observable boundaries."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    stream = StringIO()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    with caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"):
        cli.extract_comments(input_path)

    output_record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "output_write"
    )
    assert getattr(output_record, "outcome", None) == "success", (
        "a standard-output write should emit an output-write success event"
    )
    assert "{==commented text==}" in stream.getvalue(), (
        "the standard-output contract should still render the extracted Markdown"
    )


@pytest.mark.parametrize("error_type", [BrokenPipeError, OSError])
def test_stdout_write_failures_are_user_facing_and_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[OSError],
) -> None:
    """Standard-output failures should use the stable output-write boundary."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")

    class FailingStdout:
        """Raise the requested filesystem error when CLI output is written."""

        @staticmethod
        def write(_markdown: str) -> typ.NoReturn:
            """Simulate a failed standard-output stream."""
            message = "stdout failed"
            raise error_type(message)

    monkeypatch.setattr(cli.sys, "stdout", FailingStdout())

    with (
        caplog.at_level(logging.INFO, logger="docx_comment_extractor.cli"),
        pytest.raises(cli.UserFacingError, match="Could not write"),
    ):
        cli.extract_comments(input_path)

    output_record = next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "output_write"
    )
    assert getattr(output_record, "outcome", None) == "failure", (
        "a failed standard-output write should emit an output-write failure event"
    )
    assert getattr(output_record, "error_category", None) == "output_write", (
        "a failed standard-output write should have a stable error category"
    )


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
    first_metrics.record("validation", "success", 2)
    first_metrics.record("validation", "success", "not-a-duration")
    first_snapshot = first_metrics.snapshot()

    assert first_snapshot.operation_counts == {("validation", "success"): 3}, (
        "a metrics owner should retain every operation count"
    )
    assert first_snapshot.duration_totals_ms == {"validation": 3.5}, (
        "numeric durations should accumulate while non-numeric values are ignored"
    )
    assert second_metrics.snapshot().operation_counts == {}, (
        "separate metrics owners should not share operation counts"
    )

    first_metrics.reset()

    assert first_metrics.snapshot().operation_counts == {}, (
        "reset should clear the owner operation counts"
    )
    assert first_metrics.snapshot().duration_totals_ms == {}, (
        "reset should clear the owner duration totals"
    )
    assert first_snapshot.duration_totals_ms == {"validation": 3.5}, (
        "snapshots should remain detached after later metric resets"
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
    monkeypatch.setattr(cli.sys, "stderr", stream)
    # Pin the width so wrapping cannot split the untrusted text being asserted.
    monkeypatch.setenv("COLUMNS", "200")

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
    monkeypatch.setattr(cli.sys, "stderr", stream)
    # Pin the width so wrapping cannot split the untrusted text being asserted.
    monkeypatch.setenv("COLUMNS", "200")

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
    monkeypatch.setattr(cli.sys, "stderr", stream)
    # Pin the width so wrapping cannot split the untrusted text being asserted.
    monkeypatch.setenv("COLUMNS", "200")

    cli._print_error(parser_message)

    rendered = stream.getvalue()
    assert "[link=https://example.test]invalid[/link]\\x1b]8" in rendered, (
        "parser errors should render untrusted text literally"
    )
    assert "\x1b]8" not in rendered, (
        "parser errors should neutralize terminal hyperlink control sequences"
    )
