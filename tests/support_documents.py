"""Test helpers for constructing `.docx` fixtures with comments."""

from __future__ import annotations

import typing as typ
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from docx.oxml import OxmlElement

if typ.TYPE_CHECKING:
    from pathlib import Path

    from docx.comments import Comment as WordComment


class CommentElement(typ.Protocol):
    """The subset of the private comment element needed by the fixtures."""

    def set(self, key: str, value: str) -> None:
        """Assign an XML attribute value."""


COMMENT_DATE_ATTR = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}date"
FIXED_COMMENT_TIMESTAMP = "2026-04-09T20:35:31Z"
NAIVE_COMMENT_TIMESTAMP = "2026-04-09T20:35:31"
AWARE_COMMENT_TIMESTAMP = "2026-04-09T22:35:31+02:00"


def build_simple_comment_docx(path: Path) -> Path:
    """Create a document with a heading and one commented run.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    document.add_heading("Sample heading", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("Before ")
    commented_run = paragraph.add_run("commented text")
    paragraph.add_run(" after.")
    comment = document.add_comment(
        commented_run,
        text="Needs evidence.",
        author="Sam C",
    )
    _set_fixed_timestamp(comment)
    document.save(str(path))
    return path


def build_multi_run_comment_docx(path: Path) -> Path:
    """Create a document whose comment spans multiple runs.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Please review ")
    first_run = paragraph.add_run("this ")
    middle_run = paragraph.add_run("multi-run ")
    last_run = paragraph.add_run("span")
    paragraph.add_run(" now.")
    comment = document.add_comment(
        [first_run, middle_run, last_run],
        text="Tighten this wording.",
        author="Alex Reviewer",
    )
    _set_fixed_timestamp(comment)
    document.save(str(path))
    return path


def build_cross_paragraph_comment_docx(path: Path) -> Path:
    """Create a document whose comment spans two paragraphs.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    first_paragraph = document.add_paragraph()
    first_paragraph.add_run("The first paragraph opens ")
    first_anchor = first_paragraph.add_run("a long comment")
    second_paragraph = document.add_paragraph()
    second_anchor = second_paragraph.add_run("that closes here")
    second_paragraph.add_run(" with more context.")
    comment = document.add_comment(
        [first_anchor, second_anchor],
        text="This crosses a paragraph boundary.",
        author="Jordan",
    )
    _set_fixed_timestamp(comment)
    comment.add_paragraph("Second note paragraph.")
    document.save(str(path))
    return path


def build_table_docx(path: Path) -> Path:
    """Create a document with an unsupported table block.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    document.add_paragraph("Before table.")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Ignored"
    document.add_paragraph("After table.")
    document.save(str(path))
    return path


def build_criticmarkup_literal_docx(path: Path) -> Path:
    """Create a document containing literal CriticMarkup delimiters.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    paragraph = document.add_paragraph()
    run = paragraph.add_run("Literal {==marker==} text")
    comment = document.add_comment(
        run,
        text="Avoid {>>raw<<} delimiters.",
        author="Escaper",
    )
    _set_fixed_timestamp(comment)
    document.save(str(path))
    return path


def build_comment_normalization_docx(path: Path) -> Path:
    """Create a comment with blank authorship and a naive timestamp.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    run = document.add_paragraph().add_run("Commented text")
    comment = document.add_comment(run, text="Normalize metadata.", author="   ")
    _set_timestamp(comment, NAIVE_COMMENT_TIMESTAMP)
    document.save(str(path))
    return path


def build_inline_controls_docx(path: Path) -> Path:
    """Create a document with tabs and line breaks around a comment range.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    paragraph = document.add_paragraph()
    outside_before = paragraph.add_run("Outside")
    outside_before.add_tab()
    outside_before.add_break()
    first_commented_run = paragraph.add_run("Inside")
    first_commented_run.add_tab()
    first_commented_run.add_break()
    last_commented_run = paragraph.add_run("again")
    # The fixture requires an OOXML-only carriage-return element.
    last_commented_run._r.append(OxmlElement("w:cr"))
    outside_after = paragraph.add_run("After")
    outside_after.add_tab()
    outside_after.add_break()
    comment = document.add_comment(
        [first_commented_run, last_commented_run],
        text="Preserve controls.",
        author="Control Reviewer",
    )
    _set_fixed_timestamp(comment)
    document.save(str(path))
    return path


def build_timezone_aware_comment_docx(path: Path) -> Path:
    """Create a comment with a timezone-aware timestamp.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    run = document.add_paragraph().add_run("Commented text")
    comment = document.add_comment(run, text="Normalize metadata.", author="Sam C")
    _set_timestamp(comment, AWARE_COMMENT_TIMESTAMP)
    document.save(str(path))
    return path


def build_excessive_package_members_docx(path: Path) -> Path:
    """Create a compact ZIP package with several archive members.

    Parameters
    ----------
    path
        Destination path for the synthetic package.

    Returns
    -------
    Path
        ``path`` after the synthetic package has been saved.

    """
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for index in range(3):
            archive.writestr(f"member-{index}.xml", "x")
    return path


def build_oversized_uncompressed_docx(path: Path) -> Path:
    """Create a small ZIP package whose member exceeds the uncompressed budget.

    The member is highly compressible, so the package stays tiny on disk even
    though its declared uncompressed size is large. That is exactly the case
    ``_validate_package_limits`` must catch from ZIP metadata alone, before any
    member is read into memory.

    Parameters
    ----------
    path
        Destination path for the synthetic package.

    Returns
    -------
    Path
        ``path`` after the synthetic package has been saved.

    """
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("repetitive.xml", "0" * 4096)
    return path


def build_malformed_xml_docx(path: Path) -> Path:
    """Create a well-formed ZIP package whose main document part is not XML.

    The package is structurally a valid OOXML container, so it passes the size,
    member-count, and archive checks and is opened by the loader. Its
    ``document.xml`` then fails to parse, which is the failure the CLI must
    report as a clean status-2 error rather than an unhandled traceback.

    Truncating the part rather than replacing it keeps the ZIP directory and the
    ``[Content_Types].xml`` declaration intact, so parsing really is the first
    thing that fails.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    document.add_paragraph("Alpha")
    document.save(str(path))

    with ZipFile(path, "r") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}

    damaged = members["word/document.xml"]
    # Cut the part mid-element so the XML is unterminated rather than empty.
    members["word/document.xml"] = damaged[: len(damaged) // 2]

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def build_carriage_return_docx(path: Path) -> Path:
    """Create a document whose text carries a real carriage return.

    A literal CR in XML character data is normalized to a line feed by the XML
    parser, so the only way a carriage return reaches the renderer is as a
    numeric character reference. That is not a synthetic shape: an editor or a
    converter that writes ``&#13;`` produces exactly this, and CommonMark treats
    the resulting CR as a line ending, which is where an opener after it becomes
    an HTML block.

    The reference is injected into the serialized part rather than through
    ``python-docx``, which turns a literal CR into a ``<w:br/>`` element and so
    cannot express the shape at all.

    The opener is a comment rather than a block tag name, because the renderer
    wraps the text in ``{==``/``==}`` and a tag name needs whitespace or a tag
    terminator after it. ``<!--`` is live on its own, so it reaches the guard as
    a real block opener rather than as an inert near-miss.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    paragraph = document.add_paragraph()
    run = paragraph.add_run("placeholder<!--")
    comment = document.add_comment(run, text="Carriage-return opener.", author="CR")
    _set_fixed_timestamp(comment)
    document.save(str(path))

    with ZipFile(path, "r") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}

    part = members["word/document.xml"]
    replaced = part.replace(b"placeholder", b"safe&#13;")
    if replaced == part:
        message = "The carriage-return fixture should still carry its placeholder."
        raise AssertionError(message)
    members["word/document.xml"] = replaced

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def build_missing_style_attribute_docx(path: Path) -> Path:
    """Create a document whose paragraph style element lacks its required value.

    ``w:pStyle`` requires a ``w:val`` attribute. A paragraph carrying the
    element without one is serialized as well-formed XML, so the package parses
    and the extractor reaches the point of reading the style, where the
    malformed attribute has to be handled rather than raised.

    Parameters
    ----------
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the fixture document has been saved.

    """
    document = Document()
    paragraph = document.add_paragraph("Styled without a value")
    properties = paragraph._p.get_or_add_pPr()
    style = OxmlElement("w:pStyle")
    # Deliberately omit w:val, which the schema requires.
    properties.insert(0, style)
    document.save(str(path))
    return path


def build_fixture(name: str, path: Path) -> Path:
    """Build a named fixture document at ``path``.

    Parameters
    ----------
    name
        Registered fixture scenario name.
    path
        Destination path for the fixture document.

    Returns
    -------
    Path
        ``path`` after the selected fixture document has been saved.

    """
    builders = {
        "simple-comment": build_simple_comment_docx,
        "multi-run-comment": build_multi_run_comment_docx,
        "cross-paragraph-comment": build_cross_paragraph_comment_docx,
        "table-document": build_table_docx,
        "criticmarkup-literal": build_criticmarkup_literal_docx,
        "comment-normalization": build_comment_normalization_docx,
        "inline-controls": build_inline_controls_docx,
        "timezone-aware-comment": build_timezone_aware_comment_docx,
        "excessive-package-members": build_excessive_package_members_docx,
        "oversized-uncompressed": build_oversized_uncompressed_docx,
        "carriage-return": build_carriage_return_docx,
    }
    return builders[name](path)


def _set_fixed_timestamp(comment: WordComment) -> None:
    """Force a deterministic comment timestamp for snapshot stability."""
    _set_timestamp(comment, FIXED_COMMENT_TIMESTAMP)


def _set_timestamp(comment: WordComment, value: str) -> None:
    """Set the private OOXML timestamp used by a synthetic fixture."""
    element = typ.cast(
        "CommentElement",
        object.__getattribute__(comment, "_element"),
    )
    element.set(COMMENT_DATE_ATTR, value)
