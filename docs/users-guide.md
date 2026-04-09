# docx-comment-extractor Users' Guide

## Overview

`docx-comment-extractor` reads a Microsoft Word `.docx` document and emits
Markdown with Word comments rendered inline using CriticMarkup highlight and
comment markers.

## Command-line interface

The CLI installs as:

```bash
docx-comment-extractor INPUT.docx [--output OUTPUT.md]
```

You can also run the module entry point directly:

```bash
python -m docx_comment_extractor.cli INPUT.docx
```

## Writing to standard output

When you omit `--output`, the extractor writes Markdown to standard output and
keeps standard error quiet unless there is a warning or an error.

```bash
docx-comment-extractor draft.docx > draft.md
```

## Writing to a file

When you pass `--output`, the extractor writes the Markdown file and prints a
short success summary to standard error.

```bash
docx-comment-extractor draft.docx --output draft.md
```

## Output format

The extractor preserves document order and maps Word heading styles to ATX
Markdown headings. Word comments are rendered inline using a CriticMarkup
highlight followed immediately by a CriticMarkup comment:

```text
Before {==commented text==}{>>Sam C, 2026-04-09T20:35:31Z: Needs evidence.<<} after.
```

Multi-paragraph comment bodies are flattened with ` / `. Comment ranges that
span multiple paragraphs remain open across the blank line separating the
paragraphs in Markdown.

## Warnings and errors

User-facing validation failures, such as a missing input path or a non-`.docx`
extension, are reported through `rich` panels and cause a non-zero exit.

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
contained 247 inline comments and 247 highlighted spans.
