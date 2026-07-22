# docx-comment-extractor

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](
https://deepwiki.com/leynos/docx-comment-extractor)

`docx-comment-extractor` reads a Microsoft Word `.docx` file and emits Markdown
with Word comments rendered inline using CriticMarkup.

## What it does

- Preserves headings and paragraph order from the source document.
- Reconstructs Word comment ranges from OOXML comment markers exposed through
  `python-docx`.
- Renders commented spans as CriticMarkup highlights followed immediately by
  CriticMarkup comments.
- Supports comment ranges that span multiple runs and multiple paragraphs.

## Usage

Build the development environment:

```bash
make build
```

Extract to standard output:

```bash
docx-comment-extractor INPUT.docx > output.md
```

Write directly to a file:

```bash
docx-comment-extractor INPUT.docx --output output.md
```

## Documentation

- [Users' guide](docs/users-guide.md)
- [Comment extraction design](docs/comment-extraction-design.md)
- [Execution plan](docs/execplans/comment-extractor-foundation.md)
