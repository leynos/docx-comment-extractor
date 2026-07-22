# docx-comment-extractor developers' guide

## Development setup

The project requires Python 3.14 or later and uses `uv` to create and populate
the development environment. Build the environment with:

```bash
make build
```

Run the complete Python quality gates sequentially:

```bash
make check-fmt
make lint
make typecheck
make test
```

Documentation changes also require `make markdownlint` and `make nixie`.

## Dependencies

The runtime dependencies have distinct boundary roles:

- `python-docx` loads Word packages and exposes document content.
- `cyclopts` defines the command-line interface (CLI).
- `rich` presents user-facing status and error messages.

The development dependency group adds `pytest`, `pytest-bdd`, and `syrupy` for
unit, behavioural, and snapshot tests. Ruff provides linting and formatting,
whilst the repository-managed `ty` tool provides static type checking.

## Command flow

`docx_comment_extractor.cli` owns path validation and output selection. The
command validates the input and any output destination before extraction. It
then extracts a normalized model, renders Markdown, and writes either to
standard output or the requested file. Expected validation, extraction, and
write failures become concise user-facing errors with exit status 2.

`extract_document` accepts an injectable `DocumentLoader`. The default loader
is the only boundary that opens a package through `python-docx`. Known package
and filesystem failures are translated into `ExtractionError`, which keeps
third-party exceptions out of the public command boundary and permits tests to
inject deterministic loader failures.

## Extraction and rendering boundaries

`docx_comment_extractor.extractor` turns Word content into the immutable types
in `docx_comment_extractor.models`. Top-level paragraphs and tables are visited
in source order through `Document.iter_inner_content()`. Table content remains
unsupported and produces a non-fatal extraction warning.

Comment anchors still require private paragraph Office Open XML (OOXML) from
`python-docx`. Access to `Paragraph._element` is isolated in
`_iter_paragraph_xml_children`, which is the compatibility adapter to revise if
a future `python-docx` release changes that private interface. The rest of the
extraction pipeline consumes the adapter's small `XmlElement` protocol.

`docx_comment_extractor.renderer` is independent of Word package input. It
converts the normalized model into deterministic Markdown and CriticMarkup,
including cross-paragraph ranges and delimiter escaping.

## Testing strategy

The test suite has three complementary layers:

- Unit tests cover extraction, loader errors, normalized metadata, range
  boundaries, rendering, escaping, and warnings.
- Behavioural tests invoke the module entry point in a subprocess and verify
  standard streams, output files, and path-validation failures.
- Snapshot tests preserve complete rendered output for deterministic synthetic
  fixtures and an excerpt from the supplied sample document.

A future Hypothesis suite should generate fragment streams with balanced
comment start and end boundaries, including ranges that cross paragraph
boundaries. The reconstruction property is that every generated range closes
exactly once, after its matching start, without losing or reordering source
text. A separate generator should combine arbitrary text with every supported
CriticMarkup delimiter. The escaping property is that each literal delimiter
is neutralized whilst all non-delimiter text remains unchanged. Idempotence is
not required because escaping an already escaped string can add backslashes.

## Structured observability

The CLI emits bounded structured events through the Python standard library
logger named `docx_comment_extractor.cli`. The package does not configure a
handler, format, or logging level; embedding applications and operators retain
control of those policies.

Events use `operation` and `outcome` fields. Failure events may add the safe
exception class name as `error`; successful extraction and warning summaries
may add `comment_count` and `warning_count`. Operations cover validation,
extraction, warning reporting, and output writes.

Events must not include document text, comment bodies, rendered Markdown, or
raw filesystem paths. This bounded schema makes decision and failure points
observable without leaking source material.
