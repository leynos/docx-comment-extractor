# Comment extraction design

## Status

Implemented in the initial inline CriticMarkup release.

## Overview

The extractor uses `python-docx` as the document entry point and then walks the
underlying Office Open XML (OOXML) paragraph content to reconstruct comment
anchor ranges. Rendered Markdown uses CriticMarkup highlights followed
immediately by CriticMarkup comments, for example:

```text
{==commented text==}{>>Author, 2026-04-09T20:35:31Z: note<<}
```

## Architecture

The implementation is split into four modules:

- `docx_comment_extractor.models` defines immutable document, block, fragment,
  comment, and warning data structures.
- `docx_comment_extractor.extractor` opens the Word document, normalizes
  comment metadata, and reconstructs comment ranges from `commentRangeStart` and
  `commentRangeEnd` markers. Its injectable document loader translates known
  package and filesystem failures into `ExtractionError`.
- `docx_comment_extractor.renderer` serializes the internal model to Markdown
  and CriticMarkup.
- `docx_comment_extractor.cli` exposes the `cyclopts` command-line interface
  and uses `rich` for warnings, success messages, and clean user-facing
  failures. It also emits bounded structured events through the Python standard
  library logger without configuring application logging policy.

## Parsing strategy

`python-docx` exposes comment bodies and metadata via `Document.comments`, but
it does not provide a high-level comment-anchor abstraction. The extractor
therefore uses a hybrid approach:

1. Load the document through an injectable adapter whose default calls
   `docx.Document(...)`.
2. Iterate public top-level paragraph and table objects in source order with
   `Document.iter_inner_content()`.
3. Within each paragraph, use the isolated private-OOXML adapter to detect
   `commentRangeStart` and `commentRangeEnd` markers and attach those
   boundaries to text fragments.
4. Ignore the zero-text `commentReference` runs that Word inserts after comment
   ends.

Access to `Paragraph._element` is confined to `_iter_paragraph_xml_children`.
This adapter is the single compatibility point for future `python-docx`
private-interface changes.

The supplied sample document contains 247 comments and 46 cross-paragraph
comment ranges. A quick structure check also showed no nested or overlapping
comment ranges, so the first release can model comment boundaries as ordered
start and end markers rather than a general overlap graph.

## Rendering rules

- Word heading styles named `Heading 1` through `Heading 6`, with or without a
  space before the number, are rendered as hash-prefixed Markdown headings.
- Comment metadata is normalized to
  `Author, YYYY-MM-DDTHH:MM:SSZ: comment text` when author and timestamp are
  available.
- Multi-paragraph comment bodies are flattened with ` / `.
- Cross-paragraph comment spans stay open across the rendered blank line
  between paragraphs so that the highlight range remains contiguous.
- Literal CriticMarkup delimiters in source text or comment bodies are escaped
  with backslashes before rendering.
- Raw HTML and Markdown link syntax in source text or comment bodies are
  neutralized before rendering so untrusted content remains literal text. The
  raw-HTML rule applies anywhere inside a line, so it also covers the forms a
  parser accepts mid-line but not at a line start: a complete tag, a
  declaration, a CDATA section, and a processing instruction such as
  `<?php ?>`. A processing instruction needed its own first-character branch;
  without it the rule matched tag names, declarations, and CDATA but passed a
  mid-line `<?` through unescaped.
- Escaping also covers text that *begins* a line with an HTML-block opener and
  never closes it, such as a bare `<!--` or `<script`. CommonMark begins an
  HTML block at the start of a line for these prefixes and runs the block to
  the next blank line, so an unclosed opener would otherwise swallow the
  renderer's own CriticMarkup markers along with every later block. The guard
  matches the start condition a reader applies: the beginning of any line, not
  merely the beginning of the rendered text, with up to three leading spaces,
  and with the block tag names matched case-insensitively.

  A line also ends at a carriage return, not only at a line feed. A Word `w:br`
  or `w:cr` reaches the renderer as a newline, but a numeric character
  reference such as `&#13;` survives XML parsing as a real CR, so both endings
  are reachable and both are guarded.

  A blockquote or list marker before the opener is folded away as well. An
  opener inside a container is promoted to an HTML block *within* that
  container, exactly as indentation at the top level promotes it, so a guard
  that considered only indentation left the opener live inside a list item.

  The container need not appear on the opener's own line. CommonMark retains
  container state across a line break and across a blank line, and a
  continuation line's indentation is measured from the container's content
  column rather than from the document margin, so `10. item\n    <script` is a
  list item holding an HTML block on its continuation line — verified against a
  parser, which returns `list_item_open`, `paragraph_open`, then `html_block` —
  while a guard inspecting that line alone sees four spaces and nothing more. A
  marker may also lead the continuation line itself, as in
  `10. item\n    > <!--`. Because `render_document` joins blocks with a blank
  line and the reader retains the container across it, a container opened in
  one block can govern the next, so the guard cannot treat a buffer in
  isolation.

  The container prefix and the rest of the opener are preserved, and the same
  prefixes mid-line stay untouched, because escaping them would be a visible
  change to text no reader treats as markup.

  The guard folds away any run of indentation and container markers, rather
  than the bounded prefix a parser computes. That is a deliberate
  over-approximation with two causes. A list marker's separator is bounded by
  four *columns*, and a tab advances to the next four-column stop rather than
  filling one, so a character-count pattern matches a tab-heavy prefix such as
  `-\t\t` that a reader sees as indented code. More fundamentally, a
  continuation line's content column depends on a container opened on an
  earlier line and retained across the blank line between blocks; the guard
  escapes one buffer at a time and cannot recompute that column, so it must
  accept any marker run rather than a specified number of columns.

  The claims made for the pattern are correspondingly bounded. That the guard
  leaves no parser-live opener behind was checked against `markdown-it-py` over
  the corpora described below: a structured corpus of container and
  continuation shapes, where the count of parser-live outputs fell from 1932 to
  zero under the guard, and a random corpus of 30000 strings, where the count
  fell from 2803 to zero and a bracket-counting cross-check over 4000 further
  strings reported no mismatch. A finite corpus does not establish the property
  beyond the shapes it draws, so this is evidence for those shapes rather than
  a universal soundness claim. Exactness is not claimed at all: because the
  folded prefix is unbounded, the guard escapes text a parser reads as indented
  code, so it is not exact even on input that contains no tab.

  The cost is visible rather than invisible, and the earlier claim that a code
  block is unaffected was wrong. A parser reads four-space-indented `<script`
  as indented code; the guard still escapes the bracket, and CommonMark decodes
  an entity in prose but not inside a code span or code block. The change is
  therefore visible to the reader of the rendered page: where the text used to
  display as the literal `<script`, it now displays as the literal
  `&lt;script`. Those two are the *displayed* forms; the serialized HTML that
  produces them is `<pre><code>&lt;script` and `<pre><code>&amp;lt;script`
  respectively. Over a corpus of 2919 container-and-opener rows, 1204 differed
  at the text level and 943 of those differed in the rendered HTML a reader
  would see: 847 involving a code block and 96 prose only. That is the price of
  the only per-buffer rule that reaches the continuation shapes. It errs
  towards escaping: a spurious entity is visible and bounded, while a missed
  opener is a swallowed document.

## Unsupported content

The first release only extracts top-level paragraphs and headings. When the
extractor encounters a top-level table, it skips the table content and emits a
warning. Footnotes, text boxes, tracked changes, and images are not yet handled.

Both supported entry points—the CLI and `extract_document`—enforce a 20 MiB
maximum for the on-disk `.docx` input package before extraction begins. The
bound applies to the input package only; rendered Markdown and output files
have no corresponding size limit in this release. ZIP packages are additionally
limited to 10,000 members and 100 MiB of total uncompressed content.

## Testing strategy

The test suite combines four layers:

- Unit tests for model extraction, metadata normalization, escaping, warning
  generation, and rendering.
- Hypothesis property tests for comment-range reconstruction and CriticMarkup
  escaping, described below.
- Behavioural `pytest-bdd` scenarios that exercise the CLI through
  `python -m docx_comment_extractor.cli`.
- `syrupy` snapshots that lock down rendered Markdown for synthetic fixtures.
  The supplied sample document is not checked into the repository, so it cannot
  back a snapshot; its output is verified by the manual smoke test recorded in
  the users' guide instead.

Synthetic fixture documents force deterministic comment timestamps by setting a
fixed `w:date` attribute on each generated comment. Without that override, the
snapshot output would drift with the wall clock.

### Property tests

`tests/unit/test_renderer_properties.py` searches the rendering contract rather
than sampling it.

The reconstruction generator lays disjoint comment ranges over a stream of
fragment slots and then cuts that stream into blocks, so it produces ranges
that cross paragraph boundaries, ranges confined to a single fragment, adjacent
ranges, empty fragments, blocks with no fragments, and headings at every level.
Nesting and overlap are excluded, matching the range semantics recorded above.

Reconstruction is checked by three oracles that read the rendered output
independently of the renderer. The first removes only the documented range
markers and comment bodies and asserts the remainder is the source text with
heading prefixes and blank-line joins, in source order. The second walks the
output as a token stream and asserts that every range closes exactly once,
after its matching start, in source order. The third walks the model rather
than the output and asserts each highlight encloses exactly its own source
span; without it, a rendering that preserves the text and balances the markers
could still highlight the wrong span. Generated document text avoids delimiter
and ampersand characters, so the reconstruction oracle needs no escaping logic
of its own.

Escaping is checked by a further four properties: text containing every
supported delimiter, backslashes, raw HTML, and Markdown link syntax must leave
no delimiter, bracket, entity, or live raw HTML construct behind; inverting the
documented transformations must recover the original text; text containing no
escapable character must be returned unchanged; and the brackets escaping
consumes must equal the raw HTML tags it matched plus the block openers it
guarded, counted from the input rather than searched in the output.

The escape properties assert consumer semantics rather than lexical shape, and
the oracles live in `tests/support_renderer_oracles.py` so that they can be
tested in their own right. CommonMark treats a character as escaped only when
an odd-length backslash run precedes it, and a CriticMarkup reader consumes a
whole range at a time, so delimiter-like text inside one is content rather than
markup. An earlier form of this check accepted any immediately preceding
backslash and therefore passed a live Markdown link assembled from a source
backslash.

The CriticMarkup consumer is modelled explicitly rather than assumed. The
supported consumer reads the document left to right and consumes each complete
`{==…==}{>>…<<}` pair as one span, and CriticMarkup processing is taken to run
before Markdown parsing, which is why a highlight interior may hold delimiter
text but must still contain no live raw HTML or active bracket. The model is a
committed reader, `consumed_spans` plus `composition_violation`, and it is
calibrated against the same independent CommonMark parser only where Markdown
classification is at stake; the CriticMarkup half is a stated model rather than
a verified downstream integration, because no CriticMarkup renderer is in the
dependency set. The composition property therefore claims safety under that
model, not under every consumer an operator might choose.

Two further tests keep those oracles honest:

- `tests/unit/test_renderer_oracle_fidelity.py` calibrates the raw-HTML oracle
  against `markdown-it-py` over a corpus of live constructs, near-misses, and
  escaping residues, and asserts that real escaping output contains no token a
  CommonMark parser recognizes. The corpus pins the line-start start condition
  explicitly, including indented and uppercase openers, openers after a line
  feed, after a carriage return, and after a line break, openers reached
  through a blockquote or list container at every nesting the alphabet reaches,
  and the split-run shapes Word produces, because the oracle and the renderer
  once shared the same blind spot and agreeing on a defect excluded nothing. It
  also pins the continuation shapes: an opener on a later line of an ordered
  list item, a nested list, a container that survives a blank line, a container
  inside a container, and each of those with a carriage return or CRLF in place
  of the line feed. The corpus covers the shapes the specification recognizes,
  the near-misses around them, and the residues real escaping leaves behind; it
  is a curated corpus rather than an exhaustive catalogue of CommonMark HTML.
  The claims resting on it are bounded accordingly: it establishes the
  behaviour on the shapes it draws, not on every HTML form a parser accepts.
  `markdown-it-py` is declared in the development dependency group, not the
  production one, and the calibration is imported unconditionally so it cannot
  silently skip.
- `tests/unit/test_renderer_oracle_sensitivity.py` deliberately breaks one
  renderer behaviour at a time and shows the oracle rejects the result. It
  covers per-fragment escaping, grouping that ignores comment boundaries, a
  removed backslash-doubling rule, three generations of pre-repair HTML-block
  guard, the pre-repair raw-HTML pattern, live markup in a comment reference,
  and a highlight displaced onto the wrong span, alongside controls showing the
  unmutated renderer is accepted and that the reference and extent oracles
  accept correct output. The guard is mutated three times because it was
  repaired three times: the first form acted only at string position zero and
  matched block tag names case-sensitively; the second recognized every line
  but relied on a bare multiline `^`, which matches after a line feed and
  nowhere else, and folded away indentation without folding away blockquote or
  list markers; and the third reached every line and container, but folded a
  column-bounded prefix, which a continuation line defeats because its content
  column depends on a container opened on an earlier line. The raw-HTML pattern
  is mutated separately: its first character class omitted `?`, so a mid-line
  processing instruction survived escaping.

The coverage has limits worth recording. The round-trip property uses an
alphabet without backslashes, because an input already containing an escape
sequence cannot be recovered unambiguously by inverting the documented
transformations; backslash handling is covered by the safety property instead.
Escaping is not asserted to be idempotent, because reprocessing escaped input
can add backslashes. Because ranges are disjoint, the order in which several
comment ends attached to one fragment are emitted is not observable: disjoint
ranges cannot share their final fragment. Delimiters, a bare ampersand, a
source-backslash link, and a raw HTML span containing a delimiter are pinned
with `@example` as well as generated, so sensitivity does not depend on the
random draw.

## Observability

The CLI records validation, extraction, warning-summary, and output-write
decisions as structured standard-library logging events. Every event contains
`operation` and `outcome`. Events may also contain an exception class name in
`error` or bounded `comment_count` and `warning_count` values. No event
contains document text, comment bodies, rendered Markdown, or raw filesystem
paths.

Each CLI invocation owns an injected `OperationMetrics` instance. It maintains
bounded counters by operation and outcome plus monotonic duration totals, with
lock-protected records for concurrent callers. `reset()` clears an owner for
deterministic reuse, and `snapshot()` returns a thread-safe copy. `main` builds
one runtime dependency bundle per invocation and passes it through the real
Cyclopts command path, so a single owner and clock serve both the command-path
boundaries and the terminal-failure handlers. Metric state is not shared across
invocations or persisted. Failure events use stable categories, including
`argument_parsing`, rather than requiring consumers to match free-form
exception text. Metrics contain no document text, comment bodies, rendered
Markdown, or raw filesystem paths.

File output uses a same-directory temporary file and atomic replacement, so a
failed write leaves an existing output file unchanged.

The package deliberately leaves handlers, formatting, and logging levels
unconfigured at import. Applications embedding the public API and command
operators can apply local logging policy without import-time side effects.

The console command can opt into a default sink with `--verbose`, which
installs one CLI-scoped handler on the `docx_comment_extractor.cli` logger for
that run. The handler targets the standard error stream in place at invocation
time, and the run restores the logger's previous level when it finishes, so the
opt-in is scoped to the invocation rather than the process. Without the flag
the events stay inert and the standalone command writes nothing extra to
standard error.

## Sample smoke run

Running:

```bash
uv run python -m docx_comment_extractor.cli \
  commented-pentagon-draft-sam-c.docx > /tmp/pentagon-comments.md
```

produced a 5,497-line Markdown document containing 247 highlights and 247
inline CriticMarkup comments.
