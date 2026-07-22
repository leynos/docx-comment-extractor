"""Command-line interface for `docx-comment-extractor`."""

from __future__ import annotations

import sys
import typing as typ
from pathlib import Path  # noqa: TC003  # Cyclopts resolves annotations at runtime.

from cyclopts import App
from cyclopts.exceptions import CycloptsError
from rich.console import Console
from rich.panel import Panel

from .extractor import extract_document
from .renderer import render_document

if typ.TYPE_CHECKING:
    import collections.abc as cabc

STDOUT_CONSOLE = Console(file=sys.stdout)
STDERR_CONSOLE = Console(file=sys.stderr, stderr=True)
APP = App(help="Extract Word comments into inline CriticMarkup Markdown.")


class UserFacingError(Exception):
    """An expected CLI error that should be presented cleanly."""

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
    def output_alias(cls) -> UserFacingError:
        """Build an error for output paths that alias the input document."""
        return cls("Output path must not overwrite the input document.")


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
    validated_input = _validate_input_path(input_docx)
    if output is not None:
        _validate_output_path(validated_input, output)
    result = extract_document(validated_input)
    markdown = f"{render_document(result.document)}\n"

    if output is None:
        sys.stdout.write(markdown)
    else:
        output.write_text(markdown, encoding="utf-8")
        _print_success(output, len(result.document.comments), len(result.warnings))

    if result.warnings:
        _print_warning_summary(result.warnings)


def main(tokens: cabc.Iterable[str] | None = None) -> None:
    """Run the command-line application."""
    try:
        APP(
            tokens=tokens,
            console=STDOUT_CONSOLE,
            error_console=STDERR_CONSOLE,
            exit_on_error=False,
            print_error=False,
        )
    except UserFacingError as error:
        _print_error(str(error))
        raise SystemExit(2) from error
    except CycloptsError as error:
        _print_error(str(error))
        raise SystemExit(2) from error


def _validate_input_path(path: Path) -> Path:
    if path.suffix.lower() != ".docx":
        raise UserFacingError.invalid_extension(path)
    if not path.exists():
        raise UserFacingError.missing_file(path)
    if not path.is_file():
        raise UserFacingError.not_a_file(path)
    return path


def _validate_output_path(input_docx: Path, output: Path) -> None:
    if _paths_refer_to_same_file(input_docx, output):
        raise UserFacingError.output_alias()


def _paths_refer_to_same_file(input_docx: Path, output: Path) -> bool:
    if output.resolve() == input_docx.resolve():
        return True
    return output.exists() and output.samefile(input_docx)


def _print_error(message: str) -> None:
    STDERR_CONSOLE.print(Panel.fit(message, title="Error", border_style="red"))


def _print_success(output: Path, comment_count: int, warning_count: int) -> None:
    message = (
        f"Wrote Markdown to {output} "
        f"({comment_count} comments, {warning_count} warnings)."
    )
    STDERR_CONSOLE.print(message, style="green")


def _print_warning_summary(warnings: cabc.Sequence[object]) -> None:
    count = len(warnings)
    label = "warning" if count == 1 else "warnings"
    STDERR_CONSOLE.print(
        Panel.fit(
            f"Completed with {count} {label}.",
            title="Warnings",
            border_style="yellow",
        )
    )


if __name__ == "__main__":  # pragma: no cover
    main()
