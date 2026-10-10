"""Consumer-model oracles and mutation harness for the renderer properties.

These helpers live outside the property module so a second test can validate
them against deliberately mutated renderers. An oracle checked only by the test
it supports proves nothing: it has to be shown to reject the defects it claims
to catch, and it has to be shown to accept a correct renderer, or the
properties would fail for reasons that have nothing to do with the renderer.

The oracles reason the way a downstream consumer does rather than the way the
renderer is written. CommonMark escapes a character only when the backslash run
preceding it is odd, and a CriticMarkup reader consumes a whole range at a time,
so delimiter-like text inside one is content. Both facts are easy to get wrong
in a check that reads the raw output as a flat string.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    import pytest

# The pattern the renderer itself matches, restated rather than imported so
# editing the module cannot silently redefine what the fidelity test counts.
RAW_HTML_TAG = re.compile(r"<[A-Za-z!/][^>]*>")

# A consumer-faithful raw-HTML pattern, calibrated against a CommonMark parser
# (``markdown-it-py``) over the corpus in
# ``tests/unit/test_renderer_oracle_fidelity.py``.
# The renderer's own pattern is looser than any HTML reader: it protects a tag by
# entity-escaping only the outer brackets and keeping the content verbatim, so
# ``<script`` inside a protected span survives as-is and a later delimiter escape
# can supply a ``>``. Searching for that residue reports a live tag where no
# reader sees one, because the reassembled tag name contains an ``&``.
#
# An attribute value may not contain a bare ``<``, ``>``, or ``&`` -- those end
# the tag -- so an entity-escaped ``>`` cannot terminate an attribute or an
# unterminated tag opener. ``<!--`` alone is live because CommonMark treats an
# unclosed inline comment as raw HTML to the end of the input.
_LIVE_TAG = (
    r"</?[A-Za-z][A-Za-z0-9-]*"
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`&]+))?)*"
    r"\s*/?>"
)
# Openers CommonMark promotes to an HTML block at the start of a line. A comment
# opener and a processing instruction are live even without a '>' because the
# block runs to the next blank line; that is how a bare ``<!--`` swallows later
# markup. Mid-line the same prefixes are inert text.
# The block names CommonMark promotes at line start. The name must be followed
# by whitespace, a tag terminator, or the end of the line; anything else (an
# entity, for instance) means the reader sees no block opener.
_HTML_BLOCK_NAMES = (
    r"address|article|aside|base|basefont|blockquote|body|caption|center|col"
    r"|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure"
    r"|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li"
    r"|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search"
    r"|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
    r"|pre|script|style|textarea"
)
_LIVE_LINE_START = (
    # A block tag name is matched case-insensitively, as the specification
    # requires; ``<SCRIPT`` opens a block just as ``<script`` does.
    rf"(?i:</?(?:{_HTML_BLOCK_NAMES})(?=\s|/?>|$))"
    r"|<!--"
    r"|<\?"
    # A declaration needs an uppercase letter or the literal CDATA opener; the
    # escaped form an earlier rule may produce is inert for a real parser.
    r"|<![A-Z]"
    r"|<!\[CDATA\["
)
# An HTML block may be indented by up to three spaces, and it may begin at the
# start of any line rather than only at the start of the text. Both bounds were
# missed by an earlier form of this pattern, which accepted an indented or
# uppercase opener as inert when a real reader sees a block.
LIVE_RAW_HTML = re.compile(
    # A line-start opener, with or without a closing '>'.
    rf"(?m:^ {{0,3}}(?:{_LIVE_LINE_START}))"
    # A complete tag anywhere, which also ends any block the first rule found.
    rf"|{_LIVE_TAG}"
    rf"|<\?[\s\S]*?\?>"
    rf"|<!\[CDATA\[[\s\S]*?\]\]>"
)
LIVE_AMPERSAND = re.compile(r"&(?!amp;|lt;|gt;)")

# The opener prefixes CommonMark promotes to an HTML block at the start of a
# line. The renderer neutralizes one of these when no complete tag covers it,
# because an unclosed opener would otherwise swallow the renderer's own markers
# along with every later block. This restates the rule rather than importing the
# renderer's pattern, so dropping an opener from the implementation fails the
# accounting property instead of redefining what the property counts.
#
# Every line is a candidate, with up to three spaces of indentation, matching
# the start condition a CommonMark reader applies.
LIVE_BLOCK_OPENER = re.compile(rf"(?m:^ {{0,3}}(?=(?:{_LIVE_LINE_START}))<)")


def guarded_opener_count(text: str) -> int:
    """Count the openers escaping must neutralize beyond its matched tags.

    A complete tag anywhere is the raw-HTML pattern's job, so the guard only
    acts where no tag covers the bracket. Masking the matched tag spans before
    counting keeps the two mechanisms from reporting the same bracket twice, and
    a tag that spans a line break can hide an opener behind it.

    The count is per line rather than a single leading position, because a
    fragment may carry a Word line break and every line it starts is a
    candidate.
    """
    masked = list(text)
    for match in RAW_HTML_TAG.finditer(text):
        masked[match.start() : match.end()] = " " * (match.end() - match.start())
    return len(LIVE_BLOCK_OPENER.findall("".join(masked)))


# An anchored CriticMarkup reader scans left to right and consumes a complete
# range as a unit.
ANCHORED_RANGE = re.compile(
    r"\{==(?P<text>.*?)==\}\{>>(?P<reference>.*?)<<\}", re.DOTALL
)

RANGE_MARKERS = ("{==", "==}", "{>>", "<<}")


def preceding_backslash_run(text: str, index: int) -> int:
    """Return the length of the backslash run immediately before ``index``.

    CommonMark treats a character as escaped only when an odd-length run
    precedes it; an even run leaves the character active even though backslashes
    are present. Counting the run, rather than asking whether the character *is*
    preceded by a backslash, is what makes the checks below match a real
    consumer.
    """
    run = 0
    position = index - 1
    while position >= 0 and text[position] == "\\":
        run += 1
        position -= 1
    return run


def active_delimiter_count(text: str, delimiter: str) -> int:
    """Count occurrences of ``delimiter`` a consumer would treat as live.

    The renderer guards every delimiter by doubling the source's backslashes and
    then adding one, so a guarded delimiter always has an odd-length run before
    it. Any occurrence with an even run is one the prose itself assembled.
    """
    total = 0
    index = 0
    while (found := text.find(delimiter, index)) != -1:
        if preceding_backslash_run(text, found) % 2 == 0:
            total += 1
        index = found + 1
    return total


def has_active_bracket(text: str) -> bool:
    """Report whether a consumer would act on a Markdown bracket in ``text``."""
    return any(
        character in "[]" and preceding_backslash_run(text, index) % 2 == 0
        for index, character in enumerate(text)
    )


def consumed_spans(rendered: str) -> list[tuple[str, str]]:
    """Split rendered output into the chunks a consumer actually reads.

    An anchored CriticMarkup consumer reads left to right and consumes each
    complete range as a unit, so delimiter-like text inside one is content
    rather than markup. Reporting the pieces separately is what keeps a check
    honest: a substring search over the whole output would count a prose ``{``
    immediately before a close marker as if it were a live opener.

    A range contributes its interior text, not its delimiters. The delimiters
    are the renderer's own markup, and a later marker's ``>`` would otherwise
    complete an inert ``<!`` in the prose into a tag that exists only because
    the check searched across markup.

    The interior is the highlighted text alone; the reference is not part of it.
    The reference is reported separately by ``consumed_references``, so a count
    of ranges is not confused by a body, and a body is still checked rather than
    discarded.
    """
    spans: list[tuple[str, str]] = []
    cursor = 0
    for match in ANCHORED_RANGE.finditer(rendered):
        if match.start() > cursor:
            spans.append(("prose", rendered[cursor : match.start()]))
        spans.append(("range", match.group("text")))
        cursor = match.end()
    if cursor < len(rendered):
        spans.append(("prose", rendered[cursor:]))
    return spans


def consumed_references(rendered: str) -> list[str]:
    """Return the comment reference text of every consumed range.

    A body reaches the output inside ``{>> ... <<}`` and is escaped by the same
    function the renderer applies to document text, so it can carry a live
    construct just as a fragment can. Checking only the highlight interior left
    the body unexamined; this is what makes it visible to the oracle.
    """
    return [match.group("reference") for match in ANCHORED_RANGE.finditer(rendered)]


def first_live_construct(
    chunk: str,
    *,
    kind: str,
    check_delimiters: bool,
) -> str | None:
    """Return the first live construct in one chunk, or ``None`` if it is clean.

    ``kind`` names the chunk in the verdict, so a failure says whether the
    construct was in the prose, a highlight interior, or a comment reference.
    ``check_delimiters`` carries the one standard that differs between them: a
    range interior may hold delimiter text, because that is its content, while
    prose and a reference may not. Raw HTML and Markdown brackets are unsafe in
    all three.
    """
    match = LIVE_RAW_HTML.search(chunk)
    if match is not None:
        return f"live raw HTML tag in {kind}: {match.group()!r}"
    if has_active_bracket(chunk):
        return f"active Markdown bracket in {kind}: {chunk!r}"
    if not check_delimiters:
        return None
    for delimiter in RANGE_MARKERS:
        if active_delimiter_count(chunk, delimiter):
            return f"live {delimiter!r} in {kind}: {chunk!r}"
    return None


def composition_violation(rendered: str) -> str | None:
    """Return the first live construct in ``rendered``, or ``None`` if clean.

    This is the composition oracle reduced to a verdict so that the oracle
    itself can be tested. Prose must carry no live markup at all. A consumed
    range may hold delimiter text, because that is its content, but its raw HTML
    and Markdown brackets must still be inert or the highlight would not nest.

    A comment reference is held to the prose standard rather than the range
    standard. Delimiters in the interior are the highlighted document's own
    text, which a CriticMarkup reader keeps as content, but a delimiter in the
    reference would close the range early and spill the rest of the body into
    the document.
    """
    for kind, chunk in consumed_spans(rendered):
        violation = first_live_construct(
            chunk, kind=kind, check_delimiters=kind == "prose"
        )
        if violation is not None:
            return violation
    for reference in consumed_references(rendered):
        violation = first_live_construct(
            reference, kind="reference", check_delimiters=True
        )
        if violation is not None:
            return violation
    return None


def lump_fragments(fragments: typ.Iterable[typ.Any]) -> typ.Iterator[tuple]:
    """Yield one group per fragment, reverting the contiguous-grouping fix.

    This reproduces the pre-repair renderer, which escaped every fragment on its
    own and so let adjacent fragments reassemble a construct neither contained.
    """
    for fragment in fragments:
        yield (fragment,)


def group_everything(fragments: typ.Iterable[typ.Any]) -> typ.Iterator[tuple]:
    """Yield a single group covering every fragment, boundaries ignored."""
    whole = tuple(fragments)
    if whole:
        yield whole


def apply_per_fragment_escaping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the renderer escape each fragment separately, as it once did."""
    from docx_comment_extractor import renderer

    monkeypatch.setattr(renderer, "_contiguous_fragment_groups", lump_fragments)


def apply_boundary_insensitive_escaping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the renderer ignore comment boundaries while grouping fragments."""
    from docx_comment_extractor import renderer

    monkeypatch.setattr(renderer, "_contiguous_fragment_groups", group_everything)


def apply_missing_backslash_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the backslash-doubling rule, as before the parity fix."""
    from docx_comment_extractor import renderer

    monkeypatch.setattr(
        renderer,
        "MARKDOWN_TEXT_ESCAPE_SEQUENCES",
        tuple(
            pair for pair in renderer.MARKDOWN_TEXT_ESCAPE_SEQUENCES if pair[0] != "\\"
        ),
    )


# The guard as it stood before the start-condition repair: anchored at string
# position zero and case-sensitive, so an indented, uppercase, or
# post-line-break opener slipped past it. Reproduced here so the oracle's
# sensitivity to that defect is committed rather than reported.
def leading_only_html_block_opener(text: str) -> str:
    """Entity-escape an opener only when it starts the whole string."""
    if RAW_HTML_TAG.match(text) is None and _LEADING_ONLY_OPENER.match(text):
        return "&lt;" + text[1:]
    return text


_LEADING_ONLY_OPENER = re.compile(
    r"^(?:"
    r"<!--"
    r"|<\?"
    r"|<!(?:[A-Z]|\\\[|\[CDATA\[)"
    r"|</?(?:address|article|aside|base|basefont|blockquote|body|caption|center"
    r"|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure"
    r"|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend"
    r"|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param"
    r"|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
    r"|pre|script|style|textarea)"
    r"(?=\s|/?>|$)"
    r")"
)


def apply_leading_only_html_block_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the pre-repair guard, which only guarded string position zero."""
    from docx_comment_extractor import renderer

    monkeypatch.setattr(
        renderer, "_neutralize_html_block_openers", leading_only_html_block_opener
    )
