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

The `scripts/` directory holds the shared spelling-policy helper, whose modules
import each other by flat top-level name. Both the
`[tool.ty.environment] extra-paths = ["scripts"]` stanza in `pyproject.toml`
and the `--extra-search-path scripts` flag in `make typecheck` put that
directory on ty's module search path so those imports resolve.

## Command flow

`docx_comment_extractor.cli` owns path validation and output selection. The
command validates the input and any output destination before extraction. It
then extracts a normalized model, renders Markdown, and writes either to
standard output or the requested file. Expected validation, extraction, and
write failures become concise user-facing errors with exit status 2. File
output is written through a same-directory temporary file and atomically
replaced, so a failed write leaves an existing output file unchanged. Both
supported entry points—the CLI and `extract_document`—reject an on-disk input
package larger than 20 MiB before extraction; this bound applies to the `.docx`
input package, not rendered Markdown or output files.

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
  fixtures. The supplied sample document is not checked into the repository, so
  it cannot back a snapshot; its output is verified by the manual smoke test
  recorded in the users' guide instead.

### Property tests

`tests/unit/test_renderer_properties.py` covers six properties with Hypothesis:
two for reconstruction and four for escaping. The oracles they assert against
live in `tests/support_renderer_oracles.py`, so that a second test can validate
the oracles themselves.

`test_render_preserves_source_text_and_range_structure` generates a bounded
`DocumentModel`. Comment ranges are laid out as disjoint spans over a stream of
fragment slots and then cut into blocks, so the generator produces ranges that
stay open across paragraph boundaries, ranges confined to one fragment,
adjacent ranges, empty fragments, blocks with no fragments at all, and headings
at every level. Ranges never nest or overlap, which matches the supported range
semantics in the design document.

`test_render_is_safe_across_fragment_boundaries` generates the same model shape
over an alphabet of reactive characters, so that adjacent fragments can
reassemble a construct neither fragment contains, and asserts the rendered
output is inert to a consumer.

Three independent oracles check the rendered Markdown. The reconstruction
oracle splits the output on the blank line between blocks, deletes only the
documented range markers and comment bodies, and asserts that what remains is
the source text with heading prefixes and block joins, in source order. The
range oracle walks the output as a token stream, pairs every close with the
start it belongs to using a stack, and asserts that each range closes exactly
once, after its matching start, in source order. The highlight oracle walks the
model and asserts each rendered highlight encloses exactly its own source span;
without it, a rendering that preserved the text and balanced the markers could
still highlight the wrong span.

The generator avoids delimiter and ampersand characters in document text. That
keeps the reconstruction oracle free of any escaping logic, so it cannot
reproduce the algorithm it is checking.

Four escaping properties cover `escape_criticmarkup_text`:

- `test_escape_neutralizes_every_live_construct` joins arbitrary text —
  including every CriticMarkup delimiter, Unicode, backslashes, raw HTML
  samples, and Markdown link syntax — and asserts that no delimiter, square
  bracket, entity, or live raw HTML construct survives unguarded.
- `test_escape_preserves_text_outside_documented_transformations` inverts the
  documented escapes and asserts the original text is recovered.
- `test_escape_leaves_inert_text_untouched` asserts that text built only from
  characters used by no escape sequence is returned unchanged. Its alphabet
  excludes the backslash along with every delimiter character, because the
  renderer doubles source backslashes; backslash handling is covered by the
  neutralization property, and the round-trip property's alphabet is
  backslash-free for the same reason.
- `test_escape_consumes_exactly_the_raw_html_tags_it_matches` asserts that
  escaping consumes exactly the brackets it accounts for: one closing bracket
  per matched tag, and one opening bracket per matched tag plus one per
  HTML-block opener the guard neutralizes. The count is anchored to the input
  rather than searched in the output: the pattern preserves a matched tag's
  content verbatim, so a literal `<` inside that content can be re-matched
  against the output and report a false positive.

The escape properties assert consumer semantics rather than lexical shape. A
CommonMark reader treats a character as escaped only when an odd-length
backslash run precedes it, and a CriticMarkup reader consumes a whole range at
a time, so delimiter-like text inside one is content rather than markup.
Checking for a preceding backslash without counting the run passed a live
Markdown link assembled from a source backslash; that case is now a pinned
example.

#### Validating the oracles

Two further modules check the oracles rather than the renderer.

`tests/unit/test_renderer_oracle_fidelity.py` calibrates the raw-HTML oracle
against `markdown-it-py` over a corpus of live constructs, near-misses such as
`<a~>` and `<script&gt;`, and the residues real escaping leaves behind. The
corpus is curated rather than exhaustive: it pins the shapes the specification
recognizes and the near-misses around them, not every HTML form a parser
accepts. The module also runs the real escape function over a sample set and
asserts the output contains no token a CommonMark parser recognizes.
`markdown-it-py` is declared in the development dependency group, not the
production one, and it is imported unconditionally so the calibration cannot
silently skip out of a passing run.

`tests/unit/test_renderer_oracle_sensitivity.py` mutates the renderer with
`monkeypatch`, one behaviour at a time, and shows the oracle rejects the
result. It covers per-fragment escaping, grouping that ignores comment
boundaries, a removed backslash-doubling rule, the pre-repair HTML-block guard
that missed indented and case-insensitive openers, live markup in a comment
reference, and a highlight displaced onto the wrong span. Control cases confirm
the unmutated renderer is accepted and that the reference and extent oracles
accept correct output, so a rejection cannot be explained by an oracle that
rejects everything.

Limits of the coverage. The round-trip oracle uses an alphabet without
backslashes, because `strip_documented_escapes` inverts the bracket rule by
replacing `\[` with `[`, so on input that already contains an escape sequence
it stops one backslash short; the restriction is a property of that inverse,
not of the encoder, which renders `[` and `\[` differently. Text that already
carries escapes is covered by the neutralization and inert-text properties
instead. Idempotence is deliberately not asserted, because escaping an already
escaped string can add backslashes. The reconstruction generator does not model
nested or overlapping ranges, which the extractor does not produce.
Consequently the order in which the renderer emits several comment ends
attached to a single fragment is not observable under the supported semantics:
ranges would have to share their final fragment, and sharing a slot is overlap.
The composition property's domain excludes malformed and overlapping ranges as
well; those are covered by the extractor and CLI tests instead.

The bounds are explicit: disjoint ranges, at most three comments, three
fragments per range, two gap fragments per gap, and three block cuts. The
composition property checks safety under a committed CriticMarkup reader model
— an anchored left-to-right consumer that takes each `{==…==}{>>…<<}` pair as
one span, with CriticMarkup processing before Markdown parsing — rather than
under a verified downstream pipeline; no CriticMarkup renderer is in the
dependency set. A bare carriage return is also out of scope, because the
extractor emits `"\n"` for both break elements and XML parsing normalizes a
literal CR in character data. The accounting property
(`test_escape_consumes_exactly_the_raw_html_tags_it_matches`) establishes
intact-tag removal and aggregate bracket counts, not exact transformed
positions; the per-construct check carries consumer safety.

Each delimiter, a bare ampersand, a source-backslash link, and a raw HTML span
containing a delimiter are pinned with `@example` in addition to being
generated. Drawing them is not enough on every run, and a property whose
sensitivity depends on the draw can pass a defective build by luck.

## Structured observability

The CLI emits bounded structured events through the Python standard library
logger named `docx_comment_extractor.cli`. Importing the package configures
nothing: no handler, no format, and no logging level, so embedding applications
and operators retain control of their own policies.

The console command is the exception, and only when asked. Passing `--verbose`
installs a CLI-scoped handler on the `docx_comment_extractor.cli` logger for
the duration of the run and relaxes that logger's level so its records are not
filtered out. The handler presents the event message followed by its bounded
`name=value` fields. Both the handler's stream and the logger's level are set
from the invocation in progress and restored when it ends, so the opt-in never
leaks into a later call in the same process. Without the flag no handler is
installed and the events stay inert, which keeps standard error empty on a
successful run.

Events use `operation` and `outcome` fields. Failure events may add the safe
exception class name as `error`; successful extraction and warning summaries
may add `comment_count` and `warning_count`. Operations cover validation,
extraction, warning reporting, and output writes.

Each CLI invocation owns an injected `OperationMetrics` instance, which keeps
bounded counters by operation and outcome together with monotonic duration
totals. Records are protected by a lock for concurrent callers. `reset()`
clears an owner for deterministic reuse, and `snapshot()` returns a thread-safe
copy. `main` builds one runtime dependency bundle per invocation and passes it
through the real Cyclopts command path, so a single owner and clock serve both
the command-path boundaries and the terminal-failure handlers. Invocations do
not share metric state, and metrics are not persisted. Failure events use
stable categories, including `argument_parsing`, so consumers do not need to
match free-form exception text.

Events and metrics must not include document text, comment bodies, rendered
Markdown, or raw filesystem paths. This bounded schema makes decision and
failure points observable without leaking source material. The package itself
still installs no handler, formatter, or logging level at import; only the
`--verbose` console run opts into the sink described above.

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
