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
  comment metadata, and reconstructs comment ranges from `commentRangeStart`
  and `commentRangeEnd` markers. Its injectable document loader translates
  known package and filesystem failures into `ExtractionError`.
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
   `commentRangeStart` and `commentRangeEnd`
   markers and attach those boundaries to text fragments.
4. Ignore the zero-text `commentReference` runs that Word inserts after comment
   ends.

Access to `Paragraph._element` is confined to
`_iter_paragraph_xml_children`. This adapter is the single compatibility point
for future `python-docx` private-interface changes.

The supplied sample document contains 247 comments and 46 cross-paragraph
comment ranges. A quick structure check also showed no nested or overlapping
comment ranges, so the first release can model comment boundaries as ordered
start and end markers rather than a general overlap graph.

## Rendering rules

- Word heading styles named `Heading 1` through `Heading 6`, with or without a
  space before the number, are rendered as ATX (hash-prefixed) headings.
- Comment metadata is normalized to
  `Author, YYYY-MM-DDTHH:MM:SSZ: comment text` when author and timestamp are
  available.
- Multi-paragraph comment bodies are flattened with ` / `.
- Cross-paragraph comment spans stay open across the rendered blank line
  between paragraphs so that the highlight range remains contiguous.
- Literal CriticMarkup delimiters in source text or comment bodies are escaped
  with backslashes before rendering.

## Unsupported content

The first release only extracts top-level paragraphs and headings. When the
extractor encounters a top-level table, it skips the table content and emits a
warning. Footnotes, text boxes, tracked changes, and images are not yet handled.

## Testing strategy

The test suite combines three layers:

- Unit tests for model extraction, metadata normalization, escaping, warning
  generation, and rendering.
- Behavioural `pytest-bdd` scenarios that exercise the CLI through
  `python -m docx_comment_extractor.cli`.
- `syrupy` snapshots that lock down rendered Markdown for synthetic fixtures
  and for the opening excerpt of the supplied sample document.

Synthetic fixture documents force deterministic comment timestamps by setting a
fixed `w:date` attribute on each generated comment. Without that override, the
snapshot output would drift with the wall clock.

A future Hypothesis suite should generate balanced comment boundaries across
fragments and paragraphs. Each generated range must close exactly once after
its matching start, and reconstruction must preserve source-text order. A
second property should generate arbitrary text containing all supported
CriticMarkup delimiters and verify that each literal delimiter is neutralized
without changing other text. Escaping is not expected to be idempotent because
reprocessing escaped input can add backslashes.

## Observability

The CLI records validation, extraction, warning-summary, and output-write
decisions as structured standard-library logging events. Every event contains
`operation` and `outcome`. Events may also contain an exception class name in
`error` or bounded `comment_count` and `warning_count` values. No event contains
document text, comment bodies, rendered Markdown, or raw filesystem paths.

The package deliberately leaves handlers, formatting, and logging levels
unconfigured. Applications embedding the public API and command operators can
apply local logging policy without import-time side effects.

## Sample smoke run

Running:

```bash
uv run python -m docx_comment_extractor.cli \
  commented-pentagon-draft-sam-c.docx > /tmp/pentagon-comments.md
```

produced a 5,497-line Markdown document containing 247 highlights and 247
inline CriticMarkup comments. The opening excerpt begins:

```text
# Pentagon

by Drew Fallon

## Moscow, November 2002

### Chapter 1: Night Market Dreams
```
