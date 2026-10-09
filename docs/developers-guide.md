# docx-comment-extractor developers' guide

This guide records internal conventions for maintaining docx-comment-extractor.

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
- `lxml` provides XML parsing errors for corrupt-package handling.
- `cyclopts` defines the command-line interface (CLI).
- `rich` presents user-facing status and error messages.

The development dependency group adds `pytest`, `pytest-bdd`, and `syrupy` for
unit, behavioural, and snapshot tests. `lxml-stubs` provides XML typing for
static analysis. Ruff provides linting and formatting, whilst the
repository-managed `ty` tool provides static type checking.

The `scripts/` directory holds the shared spelling-policy helper, whose
modules import each other by flat top-level name. Both the
`[tool.ty.environment] extra-paths = ["scripts"]` stanza in
`pyproject.toml` and the `--extra-search-path scripts` flag in
`make typecheck` put that directory on ty's module search path so those
imports resolve.

## Command flow

`docx_comment_extractor.cli` owns path validation and output selection. The
command validates the input and any output destination before extraction. It
then extracts a normalized model, renders Markdown, and writes either to
standard output or the requested file. Expected validation, extraction, and
write failures become concise user-facing errors with exit status 2. File
output is written through a same-directory temporary file and atomically
replaced, so a failed write leaves an existing output file unchanged. Both
supported entry points—the CLI and `extract_document`—reject an on-disk input
package larger than 20 MiB before extraction; this bound applies to the
`.docx` input package, not rendered Markdown or output files.

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

The test suite has four complementary layers:

- Unit tests cover extraction, loader errors, normalized metadata, range
  boundaries, rendering, escaping, and warnings.
- Property tests in `tests/unit/test_renderer_properties.py` search the
  bounded input space described below.
- Behavioural tests invoke the module entry point in a subprocess and verify
  standard streams, output files, and path-validation failures.
- Snapshot tests preserve complete rendered output for deterministic synthetic
  fixtures and an excerpt from the supplied sample document.

### Property tests

`tests/unit/test_renderer_properties.py` covers two properties with Hypothesis.

`test_render_preserves_source_text_and_range_structure` generates a bounded
`DocumentModel`. Comment ranges are laid out as disjoint spans over a stream of
fragment slots and then cut into blocks, so the generator produces ranges that
stay open across paragraph boundaries, ranges confined to one fragment,
adjacent ranges, empty fragments, blocks with no fragments at all, and headings
at every level. Ranges never nest or overlap, which matches the supported range
semantics in the design document.

Two independent oracles check the rendered Markdown. The reconstruction oracle
splits the output on the blank line between blocks, deletes only the documented
range markers and comment bodies, and asserts that what remains is the source
text with heading prefixes and block joins, in source order. The range oracle
walks the output as a token stream, pairs every close with the start it belongs
to using a stack, and asserts that each range closes exactly once, after its
matching start, in source order.

The generator avoids delimiter and ampersand characters in document text. That
keeps the reconstruction oracle free of any escaping logic, so it cannot
reproduce the algorithm it is checking.

Three escaping properties cover `escape_criticmarkup_text`:

- `test_escape_neutralizes_every_live_construct` joins arbitrary text —
  including every CriticMarkup delimiter, Unicode, backslashes, raw HTML
  samples, and Markdown link syntax — and asserts that no delimiter, square
  bracket, entity, or complete raw HTML tag survives unguarded.
- `test_escape_preserves_text_outside_documented_transformations` inverts the
  documented escapes and asserts the original text is recovered.
- `test_escape_leaves_inert_text_untouched` asserts that text built only from
  characters used by no escape sequence is returned byte for byte, backslashes
  included.

Limits of the coverage. The round-trip oracle uses an alphabet without
backslashes, because an input that already contains an escape sequence cannot
be recovered unambiguously by inverting the documented transformations; text
that already carries escapes is covered by the neutralization and inert-text
properties instead. Neutralization is checked soundly rather than completely: a
delimiter preceded by a source backslash is accepted as already guarded.
Idempotence is deliberately not asserted, because escaping an already escaped
string can add backslashes. The reconstruction generator does not model nested
or overlapping ranges, which the extractor does not produce. Consequently the
order in which the renderer emits several comment ends attached to a single
fragment is not observable under the supported semantics: ranges would have to
share their final fragment, and sharing a slot is overlap.

Each delimiter, a bare ampersand, and a raw HTML span containing a delimiter
are pinned with `@example` in addition to being generated. Drawing them is not
enough on every run, and a property whose sensitivity depends on the draw can
pass a defective build by luck. The suite detects nineteen of twenty injected
defects in `renderer.py` on every run; the twentieth is the unobservable case
above.

## Structured observability

The CLI emits bounded structured events through the Python standard library
logger named `docx_comment_extractor.cli`. The package does not configure a
handler, format, or logging level; embedding applications and operators retain
control of those policies.

Events use `operation` and `outcome` fields. Failure events may add the safe
exception class name as `error`; successful extraction and warning summaries
may add `comment_count` and `warning_count`. Operations cover validation,
extraction, warning reporting, and output writes.

Each CLI invocation owns an injected `OperationMetrics` instance, which keeps
bounded counters by operation and outcome together with monotonic duration
totals. Records are protected by a lock for concurrent callers. `reset()`
clears an owner for deterministic reuse, and `snapshot()` returns a
thread-safe copy. `main` builds one runtime dependency bundle per invocation
and passes it through the real Cyclopts command path, so a single owner and
clock serve both the command-path boundaries and the terminal-failure
handlers. Invocations do not share metric state, and metrics are not
persisted. Failure events use stable categories, including
`argument_parsing`, so consumers do not need to match free-form exception
text.

Events and metrics must not include document text, comment bodies, rendered
Markdown, or raw filesystem paths. This bounded schema makes decision and
failure points observable without leaking source material. Logging remains
unconfigured by default: the package installs no handler, formatter, or
logging level.

## Coverage workflow contract

Both coverage lanes set up Python 3.14 with `actions/setup-python`, inside the
project's `requires-python` (`>=3.14`). generate-coverage chooses its
interpreter from its `python-version` input, then `UV_PYTHON`, then
`.python-version`, then the `python3` on `PATH`, which is the most recent
`setup-python` step before the call in its job; `uv sync` refuses an
interpreter outside `requires-python`. `tests/test_coverage_python_version.py`,
with its reader in `tests/coverage_python_sources.py`, requires every
generate-coverage call in the pull-request lane and the publisher to declare at
least one of those sources, every declared source to name the same version,
that version to be inside `requires-python`, and both lanes to measure on that
one version. A `setup-python` step guarded by `if:` or allowed to fail with
`continue-on-error` declares nothing. The ratchet baseline key already carries
the interpreter (`ratchet-baseline-<os>-py<major.minor>-`), so a lane on
another Python would miss its baseline rather than compare against the wrong
one; the contract turns that silent restart into a failure. It uses
`packaging`, a development dependency.

The CodeScene coverage boundary (CV-005) is held by
`make test-workflow-contracts`, which runs `cv005-contracts check`, the shared
contract library in `leynos/shared-actions` (`packages/cv005-contracts`), from
a full commit named by `CV005_CONTRACTS_REF` in the Makefile; CI runs it as its
own step. A fix to the rules is therefore a pin bump. The target needs `uv`,
which fetches the Python 3.13 the library runs under. The repository's
parameters are in `.github/cv005.toml`: its `repository` name and the
`[selection]` inputs the baseline measures, which the publisher's generator
must carry and every pull-request lane must match. The clauses are described in
the [scripting standards](scripting-standards.md), and the library's own suite
proves each one, so this repository keeps no copy of the readers or the refusal
cases.

## Markdown formatting and lint

`make fmt` rewrites the Markdown files Git tracks, plus untracked files it does
not ignore, with `mdtablefix --in-place`, then runs `markdownlint-cli2 --fix`.
`make check-fmt` runs `mdtablefix --check` over the same selection with the
same rewrite flags (`MDTABLEFIX_SELECT` and `MDTABLEFIX_RULES` in the
`Makefile`). `make markdownlint` lints the Markdown files selected by the glob
and the ignore rules in `.markdownlint-cli2.jsonc`. Both `mdtablefix` (0.6.1 or
later) and `markdownlint-cli2` must be on `PATH`. CI installs `mdtablefix`
0.6.1 through the shared `install-mdtablefix` action and lints through the
pinned `markdownlint-cli2-action`. `.markdownlint-cli2.jsonc` holds the rules,
the ignores and `"gitignore": true`.
