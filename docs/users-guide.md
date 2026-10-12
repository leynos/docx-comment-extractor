# docx-comment-extractor users' guide

## Overview

`docx-comment-extractor` reads a Microsoft Word `.docx` document and emits
Markdown with Word comments rendered inline using CriticMarkup highlight and
comment markers.

## Command-line interface

The CLI installs as:

```bash
docx-comment-extractor INPUT.docx [--output OUTPUT.md] [--verbose]
```

Run the module entry point directly as well:

```bash
python -m docx_comment_extractor.cli INPUT.docx
```

## Python API

The package exports `ExtractionError`, `extract_document`, and
`render_document` for applications that need to process documents in Python.
`extract_document` returns a result containing the normalized document and any
non-fatal warnings. Pass the document to `render_document` to produce Markdown:

```python
from pathlib import Path

from docx_comment_extractor import (
    ExtractionError,
    extract_document,
    render_document,
)

try:
    result = extract_document(Path("draft.docx"))
except ExtractionError as error:
    print(error)
else:
    print(render_document(result.document))
    for warning in result.warnings:
        print(f"{warning.code}: {warning.message}")
```

## Writing to standard output

When `--output` is omitted, the extractor writes Markdown to standard output
and keeps standard error quiet unless there is a warning or an error.

```bash
docx-comment-extractor draft.docx > draft.md
```

## Writing to a file

When `--output` is provided, the extractor writes the Markdown file and prints
a short success summary to standard error.

```bash
docx-comment-extractor draft.docx --output draft.md
```

File output is written through a same-directory temporary file and atomically
replaced. An existing output file remains unchanged if writing or replacement
fails. The CLI rejects an output path that aliases the input document.

## Verbose logging

Passing `--verbose` (or `-v`) makes the command emit bounded structured
operational events to standard error. The option is off by default; without it
the events stay inert and the command writes nothing extra to standard error.

```bash
docx-comment-extractor draft.docx --output draft.md --verbose
```

The events carry `operation` and `outcome` fields together with counters and
durations. They never include document text, comment bodies, rendered Markdown,
or file paths. The option affects only that one run: the logger's level and
sink are restored when the run ends.

## Output format

The extractor preserves document order and maps Word heading styles to
hash-prefixed Markdown headings. Word comments are rendered inline using a
CriticMarkup highlight followed immediately by a CriticMarkup comment:

```text
Before {==commented text==}{>>Sam C, 2026-04-09T20:35:31Z: Needs evidence.<<} after.
```

## Comment ranges and metadata

Comment ranges spanning multiple Word runs are reconstructed as one inline
CriticMarkup highlight. Ranges spanning multiple paragraphs remain open across
the blank line separating those paragraphs in Markdown. Multi-paragraph comment
bodies are flattened with ` / `.

Literal CriticMarkup delimiters in source text and comment bodies are escaped
before rendering, so text such as `{>>literal<<}` remains literal Markdown:

```text
\{>>literal<<\}
```

Raw HTML and Markdown link syntax in source text and comment bodies are also
neutralized before rendering, so untrusted content remains literal text.

The escape is deliberately wider than a Markdown parser's own rules for one
case. Text that could begin an HTML block — an unclosed `<script`, or a tag or
comment reached through a list or blockquote marker — is escaped even where the
parser would have treated it as ordinary indented code. This affects text that
was already indented as code and contains a `<`, and the change is visible in
the rendered page: a code block that used to display the literal `<script` now
displays the literal `&lt;script`, because an entity is not decoded inside a
code block. (Those are the displayed forms; the serialized HTML is
`<pre><code>&amp;lt;script` rather than `<pre><code>&lt;script`.) The change is
preferred to the alternative, where a missed opener swallows the rest of the
document.

Comment metadata follows these rules:

- Author names are stripped of surrounding whitespace; a blank author is
  omitted.
- Naive timestamps are interpreted as UTC. Timezone-aware timestamps are
  converted to UTC and rendered at second precision as `YYYY-MM-DDTHH:MM:SSZ`.
- Available author and timestamp metadata precedes the comment body. Missing
  metadata is omitted without adding placeholder punctuation.

## Warnings and errors

User-facing validation failures, such as a missing input path or a non-`.docx`
extension, are reported through `rich` panels and cause exit status 2.
Extraction and output-write failures use the same status.

Before extraction, both supported entry points—the CLI and
`extract_document`—reject an on-disk `.docx` package larger than 20 MiB. This
bound applies to the input package only; rendered Markdown and the selected
output destination are not subject to this limit. ZIP packages are additionally
limited to 10,000 members and 100 MiB of total uncompressed content.

Unsupported top-level tables are skipped with a warning rather than failing the
whole extraction. The current release does not yet extract tables, footnotes,
tracked changes, text boxes, or images.

## Example smoke test

The supplied sample document can be rendered with:

```bash
uv run python -m docx_comment_extractor.cli \
  commented-pentagon-draft-sam-c.docx > /tmp/pentagon-comments.md
```

In the reference run used during implementation, the generated Markdown
contained 247 inline comments and 247 highlighted spans. The sample document is
not checked into the repository, so this step is a manual check rather than an
automated test; you need your own copy of the file to run it.
