"""Unit tests for command-line path validation."""

from __future__ import annotations

import typing as typ

import pytest

from docx_comment_extractor import cli
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
