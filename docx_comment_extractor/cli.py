"""Command-line interface for `docx-comment-extractor`."""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import tempfile
import threading
import time
import typing as typ
from collections import Counter
from contextlib import suppress
from pathlib import Path

from cyclopts import App
from cyclopts.exceptions import CycloptsError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .extractor import MAX_INPUT_BYTES, ExtractionError, extract_document
from .renderer import render_document

if typ.TYPE_CHECKING:
    import collections.abc as cabc

STDOUT_CONSOLE = Console(file=sys.stdout)
STDERR_CONSOLE = Console(file=sys.stderr, stderr=True)
APP = App(help="Extract Word comments into inline CriticMarkup Markdown.")
LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class MetricsSnapshot:
    """An immutable copy of the metrics collected by one CLI invocation."""

    operation_counts: dict[tuple[str, str], int]
    duration_totals_ms: dict[str, float]


class OperationMetrics:
    """Own bounded metrics for one CLI invocation or injected test scope."""

    def __init__(self) -> None:
        """Create an empty, lock-protected metrics owner."""
        self._lock = threading.Lock()
        self._operation_counts: Counter[tuple[str, str]] = Counter()
        self._duration_totals_ms: Counter[str] = Counter()

    def record(
        self,
        operation: str,
        outcome: str,
        duration_ms: object,
    ) -> dict[str, int | float]:
        """Record an event and return its bounded metric fields."""
        with self._lock:
            self._operation_counts[operation, outcome] += 1
            fields: dict[str, int | float] = {
                "operation_count": self._operation_counts[operation, outcome]
            }
            match duration_ms:
                case int() | float():
                    self._duration_totals_ms[operation] += duration_ms
                    fields["duration_total_ms"] = self._duration_totals_ms[operation]
        return fields

    def reset(self) -> None:
        """Clear all counters so the owner can be reused deterministically."""
        with self._lock:
            self._operation_counts.clear()
            self._duration_totals_ms.clear()

    def snapshot(self) -> MetricsSnapshot:
        """Return a thread-safe copy of this owner's current metric values."""
        with self._lock:
            return MetricsSnapshot(
                operation_counts=dict(self._operation_counts),
                duration_totals_ms=dict(self._duration_totals_ms),
            )


@dataclasses.dataclass(frozen=True)
class _RenderedDocument:
    """Keep rendering results separate from persistence and terminal reporting."""

    markdown: str
    comment_count: int
    warnings: cabc.Sequence[object]


@dataclasses.dataclass(frozen=True)
class _RuntimeDependencies:
    """Own the narrow runtime seams used by CLI orchestration."""

    metrics: OperationMetrics
    output_writer: typ.Callable[[Path, str], None]
    clock: typ.Callable[[], float]
    input_validator: typ.Callable[[Path], Path]
    output_validator: typ.Callable[[Path, Path], None]


class UserFacingError(Exception):
    """An expected CLI error that should be presented cleanly."""

    def __init__(self, message: str, *, operation: str = "validation") -> None:
        """Store the safe operation name alongside the display message."""
        super().__init__(message)
        self.operation = operation

    @classmethod
    def invalid_extension(cls, path: Path) -> UserFacingError:
        """Build an error for unsupported input extensions."""
        return cls(f"Input path '{path}' must use the .docx extension.")

    @classmethod
    def missing_file(cls, path: Path) -> UserFacingError:
        """Build an error for a missing input path."""
        return cls(f"Input path '{path}' does not exist.")

    @classmethod
    def not_a_file(cls, path: Path) -> UserFacingError:
        """Build an error for non-file inputs."""
        return cls(f"Input path '{path}' is not a file.")

    @classmethod
    def input_too_large(cls) -> UserFacingError:
        """Build an error for Word packages that exceed the input limit."""
        return cls("Input document is too large; maximum size is 20 MiB.")

    @classmethod
    def output_alias(cls) -> UserFacingError:
        """Build an error for output paths that alias the input document."""
        return cls("Output path must not overwrite the input document.")

    @classmethod
    def output_write(cls) -> UserFacingError:
        """Build an error for output destinations that cannot be written."""
        return cls(
            "Could not write the Markdown output file.", operation="output_write"
        )


@APP.default
def extract_comments(input_docx: Path, output: Path | None = None) -> None:
    """Extract inline CriticMarkup Markdown from ``input_docx``.

    Parameters
    ----------
    input_docx
        Path to the Word ``.docx`` document to extract.
    output
        Optional destination for the rendered Markdown. When omitted, output
        is written to standard output.

    Returns
    -------
    None
        The command returns after writing the rendered Markdown and reports a
        success summary to standard error when ``output`` is provided.

    Raises
    ------
    UserFacingError
        If an input or output path fails command validation. ``main`` presents
        the error and exits with status 2.

    """
    _run_extraction(
        input_docx,
        output,
        dependencies=_RuntimeDependencies(
            metrics=OperationMetrics(),
            output_writer=_write_output_atomically,
            clock=time.perf_counter,
            input_validator=_validate_input_path,
            output_validator=_validate_output_path,
        ),
    )


def _run_extraction(
    input_docx: Path,
    output: Path | None,
    *,
    dependencies: _RuntimeDependencies,
) -> None:
    """Orchestrate extraction with injectable metrics and file persistence."""
    rendered = _prepare_rendered_document(
        input_docx,
        output,
        dependencies=dependencies,
    )
    _write_rendered_document(
        output,
        rendered,
        dependencies=dependencies,
    )
    _report_warnings(rendered.warnings, dependencies=dependencies)


def _prepare_rendered_document(
    input_docx: Path,
    output: Path | None,
    *,
    dependencies: _RuntimeDependencies,
) -> _RenderedDocument:
    """Validate, extract, and render a document before any persistence."""
    validation_started_at = dependencies.clock()
    validated_input = dependencies.input_validator(input_docx)
    if output is not None:
        dependencies.output_validator(validated_input, output)
    _log_event(
        dependencies.metrics,
        "validation",
        "success",
        {
            "duration_ms": _duration_ms(
                validation_started_at,
                clock=dependencies.clock,
            )
        },
    )
    extraction_started_at = dependencies.clock()
    result = extract_document(validated_input)
    _log_event(
        dependencies.metrics,
        "extraction",
        "success",
        {
            "comment_count": len(result.document.comments),
            "duration_ms": _duration_ms(
                extraction_started_at,
                clock=dependencies.clock,
            ),
            "warning_count": len(result.warnings),
        },
    )
    return _RenderedDocument(
        markdown=f"{render_document(result.document)}\n",
        comment_count=len(result.document.comments),
        warnings=result.warnings,
    )


def _write_rendered_document(
    output: Path | None,
    rendered: _RenderedDocument,
    *,
    dependencies: _RuntimeDependencies,
) -> None:
    """Write rendered Markdown to standard output or an injected file writer."""
    if output is None:
        sys.stdout.write(rendered.markdown)
    else:
        output_write_started_at = dependencies.clock()
        dependencies.output_writer(output, rendered.markdown)
        _log_event(
            dependencies.metrics,
            "output_write",
            "success",
            {
                "duration_ms": _duration_ms(
                    output_write_started_at,
                    clock=dependencies.clock,
                )
            },
        )
        _print_success(output, rendered.comment_count, len(rendered.warnings))


def _report_warnings(
    warnings: cabc.Sequence[object],
    *,
    dependencies: _RuntimeDependencies,
) -> None:
    """Report non-fatal warnings after output has completed."""
    if warnings:
        warning_summary_started_at = dependencies.clock()
        _log_event(
            dependencies.metrics,
            "warning_summary",
            "reported",
            {
                "duration_ms": _duration_ms(
                    warning_summary_started_at,
                    clock=dependencies.clock,
                ),
                "warning_count": len(warnings),
            },
        )
        _print_warning_summary(warnings)


def main(
    tokens: cabc.Iterable[str] | None = None,
    *,
    metrics: OperationMetrics | None = None,
    clock: typ.Callable[[], float] = time.perf_counter,
) -> None:
    """Run the command-line application.

    Parameters
    ----------
    tokens
        Optional command-line argument iterable. ``None`` makes Cyclopts read
        arguments from the process command line.
    metrics
        Optional per-invocation metrics owner for terminal failure events.
    clock
        Monotonic clock used for terminal failure duration metrics.

    Returns
    -------
    None
        The command returns normally after successful Cyclopts dispatch.

    Raises
    ------
    SystemExit
        With status 2 after a handled validation, extraction, or argument
        parsing failure.

    """
    command_started_at = clock()
    active_metrics = metrics or OperationMetrics()
    try:
        APP(
            tokens=tokens,
            console=STDOUT_CONSOLE,
            error_console=STDERR_CONSOLE,
            exit_on_error=False,
            print_error=False,
        )
    except UserFacingError as error:
        _log_event(
            active_metrics,
            error.operation,
            "failure",
            {
                "duration_ms": _duration_ms(command_started_at, clock=clock),
                "error": type(error).__name__,
                "error_category": "user_facing",
            },
        )
        _print_error(str(error))
        raise SystemExit(2) from error
    except ExtractionError as error:
        _log_event(
            active_metrics,
            "extraction",
            "failure",
            {
                "duration_ms": _duration_ms(command_started_at, clock=clock),
                "error": type(error).__name__,
                "error_category": "extraction",
            },
        )
        _print_error(str(error))
        raise SystemExit(2) from error
    except CycloptsError as error:
        _log_event(
            active_metrics,
            "argument_parsing",
            "failure",
            {
                "duration_ms": _duration_ms(command_started_at, clock=clock),
                "error": type(error).__name__,
                "error_category": "argument_parsing",
            },
        )
        _print_error(str(error))
        raise SystemExit(2) from error


def _file_size(path: Path) -> int:
    """Return the byte size used by input validation."""
    return path.stat().st_size


def _validate_input_path(
    path: Path,
    *,
    file_size: typ.Callable[[Path], int] | None = None,
) -> Path:
    """Validate and return a readable Word input path."""
    active_file_size = file_size or _file_size
    try:
        if path.suffix.lower() != ".docx":
            raise UserFacingError.invalid_extension(path)
        if not path.exists():
            raise UserFacingError.missing_file(path)
        if not path.is_file():
            raise UserFacingError.not_a_file(path)
        if active_file_size(path) > MAX_INPUT_BYTES:
            raise UserFacingError.input_too_large()
    except OSError as error:
        message = "Could not inspect the input document path."
        raise UserFacingError(message) from error
    return path


def _validate_output_path(
    input_docx: Path,
    output: Path,
    *,
    paths_refer_to_same_file: typ.Callable[[Path, Path], bool] | None = None,
) -> None:
    """Reject output paths that alias the input document."""
    active_paths_refer_to_same_file = (
        paths_refer_to_same_file or _paths_refer_to_same_file
    )
    try:
        if active_paths_refer_to_same_file(input_docx, output):
            raise UserFacingError.output_alias()
    except OSError as error:
        message = "Could not inspect the output document path."
        raise UserFacingError(message) from error


def _paths_refer_to_same_file(input_docx: Path, output: Path) -> bool:
    """Return whether two paths resolve to the same filesystem object."""
    if output.resolve() == input_docx.resolve():
        return True
    return output.exists() and output.samefile(input_docx)


def _write_output_atomically(output: Path, markdown: str) -> None:
    """Write Markdown through a same-directory temporary file and replacement."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=output.parent,
            encoding="utf-8",
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(markdown)
        os.replace(  # noqa: PTH105  # Required atomic-replacement primitive.
            temporary_path,
            output,
        )
    except OSError as error:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        raise UserFacingError.output_write() from error


def _print_error(message: str) -> None:
    """Render a user-facing error on standard error."""
    STDERR_CONSOLE.print(
        Panel.fit(_safe_terminal_text(message), title="Error", border_style="red")
    )


def _print_success(output: Path, comment_count: int, warning_count: int) -> None:
    """Render a successful output-file summary on standard error."""
    message = (
        f"Wrote Markdown to {output} "
        f"({comment_count} comments, {warning_count} warnings)."
    )
    STDERR_CONSOLE.print(_safe_terminal_text(message), style="green")


def _print_warning_summary(warnings: cabc.Sequence[object]) -> None:
    """Render the count of non-fatal extraction warnings."""
    count = len(warnings)
    label = "warning" if count == 1 else "warnings"
    STDERR_CONSOLE.print(
        Panel.fit(
            f"Completed with {count} {label}.",
            title="Warnings",
            border_style="yellow",
        )
    )


def _log_event(
    metrics: OperationMetrics,
    operation: str,
    outcome: str,
    details: cabc.Mapping[str, object] | None = None,
) -> None:
    """Emit a bounded event with counters and duration metrics."""
    fields: dict[str, object] = {"operation": operation, "outcome": outcome}
    if details is not None:
        fields.update(details)
    fields.update(metrics.record(operation, outcome, fields.get("duration_ms")))
    LOGGER.info("CLI operation completed", extra=fields)


def _duration_ms(started_at: float, *, clock: typ.Callable[[], float]) -> float:
    """Return elapsed monotonic time in milliseconds."""
    return (clock() - started_at) * 1000


def _safe_terminal_text(message: str) -> Text:
    """Return literal text with terminal control characters made visible."""
    sanitized = "".join(
        character if character.isprintable() else f"\\x{ord(character):02x}"
        for character in message
    )
    return Text(sanitized)


if __name__ == "__main__":  # pragma: no cover
    main()
