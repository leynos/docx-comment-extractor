"""Unit tests for command-line path validation."""

from __future__ import annotations

import logging
import typing as typ

import pytest

from docx_comment_extractor import cli
from docx_comment_extractor.extractor import ExtractionError
from tests.support_documents import build_fixture

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("alias_kind", ["same", "symlink", "hard-link"])
def test_extract_comments_rejects_output_alias_before_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
) -> None:
    """Input aliases should fail before document extraction can run."""
    input_path = build_fixture("simple-comment", tmp_path / "input.docx")
    output_path = tmp_path / "output.docx"
    if alias_kind == "same":
        output_path = input_path
    elif alias_kind == "symlink":
        output_path.symlink_to(input_path)
    else:
        output_path.hardlink_to(input_path)

    def fail_if_extracted(_path: Path) -> typ.NoReturn:
        pytest.fail("output alias validation must run before extraction")

    monkeypatch.setattr(cli, "extract_document", fail_if_extracted)

    with pytest.raises(
        cli.UserFacingError,
        match=r"Output path must not overwrite the input document\.",
    ):
        cli.extract_comments(input_path, output_path)


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
