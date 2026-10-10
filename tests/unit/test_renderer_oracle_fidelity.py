"""Calibrate the raw-HTML oracle against a real CommonMark parser.

The renderer properties assert that no live construct survives escaping. An
oracle for that claim is only as good as its agreement with an actual consumer:
a pattern that is too loose rejects safe output, and one that is too narrow
accepts output a reader would parse as markup. This test pins the oracle to
``markdown-it-py`` rather than to my reading of the specification.

``markdown-it-py`` is deliberately not declared as a project dependency. It is
present in the development environment, and the suite skips this calibration
when it is absent, so the production dependency set never grows for a test-only
need.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.support_renderer_oracles import LIVE_RAW_HTML

markdown_it = pytest.importorskip(
    "markdown_it", reason="calibration parser not installed"
)

if typ.TYPE_CHECKING:
    from markdown_it import MarkdownIt

# Each case pairs a snippet with the classification a CommonMark reader gives
# it. The corpus covers every raw-HTML shape the specification recognizes, the
# near-misses an over-eager pattern wrongly accepts, and the residues a real
# escape leaves behind.
CALIBRATION_CASES: tuple[tuple[str, bool], ...] = (
    # Recognized raw HTML.
    ("<script>alert(1)</script>", True),
    ("<b>", True),
    ("</div>", True),
    ("<img src=x>", True),
    ('<a href="x">', True),
    ("<a href='x'>", True),
    ('<a href="x" target="_blank">', True),
    ("<br/>", True),
    ("<br />", True),
    ("<b >", True),
    ("<b/>x", True),
    ("<a href=''>", True),
    ("<a b=c>", True),
    ("<x-custom>", True),
    ("<!-- comment -->", True),
    ("<?php ?>", True),
    ("<![CDATA[x]]>", True),
    ("<!DOCTYPE html>", True),
    # CommonMark treats an unclosed inline comment as raw HTML to end of input.
    ("<!--", True),
    # Near-misses that must not match.
    ("<a~>", False),
    ("<scr", False),
    ("a<scr", False),
    ("<3>", False),
    ("< b>", False),
    ("<a href=>", False),
    ("<x:y>", False),
    # Residues the renderer's own escaping leaves behind.
    ("&lt;script&gt;", False),
    ("<script&gt;", False),
    ('<a href="x"&gt;', False),
    ('&lt;!--<a href="x"&gt;~\\\\>', False),
    ("&lt;!--<script&gt;alert(1)&lt;/script&gt;~\\\\>", False),
    ("{==\\{====}{>>@a0@<<}", False),
    ("&lt;", False),
    ("x&gt;", False),
)


def _live_html_tokens(markdown: MarkdownIt, text: str) -> list[str]:
    """Collect every raw-HTML token, descending into inline children.

    ``md.parse`` returns block tokens, and inline HTML lives in the children of
    ``inline`` tokens. A top-level scan alone misses most of the corpus, so the
    walk has to recurse.
    """
    found: list[str] = []
    stack = list(markdown.parse(text))
    while stack:
        token = stack.pop()
        if token.type in {"html_inline", "html_block"}:
            found.append(token.content)
        children = token.children
        if children:
            stack.extend(children)
    return found


@pytest.mark.parametrize(("text", "is_live"), CALIBRATION_CASES)
def test_oracle_agrees_with_commonmark(text: str, *, is_live: bool) -> None:
    """The oracle's verdict should match a real CommonMark parser's."""
    from markdown_it import MarkdownIt

    markdown = MarkdownIt("commonmark")
    parser_verdict = bool(_live_html_tokens(markdown, text))
    oracle_verdict = bool(LIVE_RAW_HTML.search(text))
    assert oracle_verdict == parser_verdict == is_live, (
        f"{text!r}: oracle={oracle_verdict} parser={parser_verdict} expected={is_live}"
    )


def test_oracle_rejects_every_escaping_residue() -> None:
    """Escaped output should contain no raw HTML a parser would recognize.

    This is the end-to-end form of the calibration: it takes real escaping
    output rather than hand-written snippets, so it also covers whatever the
    escape table does to a tag that straddles the raw-HTML pattern.
    """
    from markdown_it import MarkdownIt

    from docx_comment_extractor.renderer import escape_criticmarkup_text

    markdown = MarkdownIt("commonmark")
    samples = (
        "<script>alert(1)</script>",
        '<a href="x">',
        "<!--",
        "</div>",
        "<b>",
        "<img src=x>",
        r'<!--<a href="x">~\>',
        r"<!--<script>alert(1)</script>~\>",
    )
    for sample in samples:
        escaped = escape_criticmarkup_text(sample)
        assert not _live_html_tokens(markdown, escaped), (
            f"{sample!r} escaped to live markup {escaped!r}"
        )
