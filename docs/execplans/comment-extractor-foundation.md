# Implement the inline CriticMarkup extraction CLI

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

Build a command-line tool that reads a `.docx` file and emits Markdown whose
body text stays in document order whilst Word comments are inserted inline
using CriticMarkup. A commented span should appear as a highlighted passage
followed immediately by a CriticMarkup comment, for example
`{==commented text==}{>>Author, 2025-12-06: note<<}`.

After this change, a user can run one command against
`commented-pentagon-draft-sam-c.docx` and receive a Markdown document that:

- preserves headings and paragraph flow from the source document,
- places each Word comment next to the text it annotates,
- handles comment ranges that span multiple runs and multiple paragraphs, and
- reports problems through a readable `rich` terminal interface.

The implementation must be observable end to end. The primary proof is a
behavioural test that drives the CLI against fixture documents and compares the
produced Markdown with approved expectations, plus a manual smoke command on
the supplied sample document.

## Repository orientation

This repository is currently a minimal Python package skeleton. The package
lives in `docx_comment_extractor/`. Tooling is driven through `Makefile` and
`uv`. There is no existing CLI, no extraction pipeline, and no test suite.
Documentation currently consists of `README.md`, `docs/users-guide.md`, and the
shared style guidance in `docs/documentation-style-guide.md`.

The sample document `commented-pentagon-draft-sam-c.docx` is a real source of
requirements rather than just demo data. Inspection of its OOXML (Office Open
XML) package shows:

- `word/comments.xml` is present, so comments are stored in the standard Word
  comments part.
- The main body uses `w:commentRangeStart`, `w:commentRangeEnd`, and
  `w:commentReference` markers to anchor comments.
- The document has 247 comments.
- 46 comment ranges span more than one paragraph.
- The body has headings and normal paragraphs, but no tables.

Those findings mean a paragraph-only or single-run-only solution will be wrong
for the provided sample file.

## Constraints

- Use `python-docx` as the document parsing entry point. Open the document with
  `docx.Document(...)` and treat the package parts exposed by `python-docx` as
  the supported route into the OOXML tree. Do not replace the parser with a
  separate `.zip` plus ad-hoc XML-only implementation.
- Use `cyclopts` for the CLI surface.
- Use `rich` for terminal presentation, especially for validation failures,
  warnings, and success summaries.
- Use `pytest` and `pytest-bdd` for testing. New functionality must follow a
  red, green, refactor cycle.
- Update `docs/users-guide.md` with actual usage once behaviour is settled.
- Record the extraction and rendering design in a dedicated design document,
  most likely `docs/comment-extraction-design.md`, unless implementation shows
  an Architectural Decision Record (ADR) is the better fit.
- Preserve the repository’s existing quality gates. For Python code these are
  `make check-fmt`, `make lint`, `make typecheck`, and `make test`, run
  sequentially. For Markdown changes also run `make markdownlint` and
  `make nixie`.
- Keep Markdown and prose in en-GB Oxford style and wrap paragraphs at
  80 columns.
- The user approved implementation on 2026-04-09. Keep this document updated
  as milestones land.

## Tolerances (exception triggers)

- Scope: if the first implementation needs more than about 12 new source and
  test files or more than about 700 net lines of code, stop and reassess the
  package split before proceeding.
- Interface: if there is a strong need for more than one public CLI command or
  more than two output modes in the first cut, stop and confirm the surface
  with the user.
- Dependencies: if a new runtime dependency beyond `python-docx`, `cyclopts`,
  and `rich` appears necessary, stop and justify it before adding it.
- Markdown construction: if a Python-native `mdast`-style library is not clean
  to integrate within one focused milestone, do not block delivery on it.
  Instead, proceed with an internal document model that mirrors `mdast`
  concepts and serialize directly.
- Unsupported document features: if correct handling of tables, footnotes,
  text boxes, tracked changes, or images becomes necessary for the supplied
  sample or for agreed acceptance tests, stop and expand the plan before
  implementing them.
- Escaping: if CriticMarkup escaping rules become ambiguous for real fixture
  content after two design attempts, stop and document the competing options.
- Iterations: if any milestone still has failing tests after three focused
  fix attempts, stop and capture the blocker in `Decision Log`.

## Risks

- Risk: `python-docx` exposes comment content via `Document.comments`, but the
  comment anchor range is still represented in low-level OOXML markers.
  Severity: high Likelihood: high Mitigation: design the extractor as a hybrid
  over `python-docx` objects and the underlying XML elements obtained from
  those objects. Prototype anchor reconstruction before wiring the renderer.

- Risk: 46 comments in the sample document span multiple paragraphs, which is
  awkward for inline Markdown plus CriticMarkup serialization. Severity: high
  Likelihood: high Mitigation: establish the serialization rule in tests first.
  Prefer one logical highlight range spanning internal newlines, with the
  CriticMarkup comment emitted immediately after the closing marker. If that
  proves unreadable in fixtures, document and adopt a deterministic paragraph-
  fragment fallback.

- Risk: Word comment text can contain multiple paragraphs and metadata, but
  CriticMarkup comments are inline and plain-text oriented. Severity: medium
  Likelihood: medium Mitigation: normalize comment bodies into a single inline
  string, joining paragraphs with a visible separator such as ` / ` or ` | `
  chosen by test expectation. Preserve author and timestamp when available.

- Risk: direct string concatenation can create malformed Markdown when comment
  boundaries cut through emphasis, punctuation, or whitespace. Severity: medium
  Likelihood: medium Mitigation: define an internal block and inline token
  model first. Only the final stage should render Markdown text. This is where
  a Python-native `mdast`-like structure may help, but it is not required if
  the internal model is explicit and well tested.

- Risk: CLI output will be hard to trust without approved fixture output.
  Severity: medium Likelihood: high Mitigation: create small synthetic `.docx`
  fixtures in tests for single-run, multi-run, and cross-paragraph cases, then
  add the supplied sample document as a smoke fixture.

## Proposed architecture

Implement the first cut around four layers.

`docx_comment_extractor.cli`

- Own the `cyclopts` application and argument validation.
- Expose one command that accepts an input `.docx` path and an optional output
  path. Standard output remains the default sink for easy shell piping.
- Use `rich.console.Console` plus `rich.panel.Panel` or `rich.traceback` for
  human-readable error reporting.

`docx_comment_extractor.extractor`

- Open the document via `python-docx`.
- Walk the main document body in source order, collecting block items
  paragraph-by-paragraph.
- Reconstruct comment anchor ranges by scanning the underlying XML elements for
  `w:commentRangeStart` and `w:commentRangeEnd`.
- Resolve comment bodies and metadata through `Document.comments`.
- Produce a typed internal representation such as:
  `DocumentModel -> BlockModel -> InlineToken`, with comment anchors carrying
  `comment_id`, `author`, `date`, `comment_text`, and the spanned source text.

`docx_comment_extractor.renderer`

- Convert the internal model into Markdown.
- Map Word heading styles to ATX headings (`Heading1` -> `#`, `Heading2` ->
  `##`, and so on).
- Serialize a commented span as
  `{==span text==}{>>author, timestamp: comment text<<}`.
- Escape or normalize sequences that would break CriticMarkup or Markdown.
- Keep blank-line behaviour deterministic and fixture-backed.

`docx_comment_extractor.models`

- Hold the small immutable data structures and helper functions that make unit
  tests cheap to write.

If warnings are needed, add `docx_comment_extractor.reporting` rather than
burying `rich` calls inside extraction logic.

## CLI contract to implement

The initial CLI should be small and explicit:

```plaintext
docx-comment-extractor INPUT_DOCX [--output OUTPUT_MD]
```

Expected behaviour:

1. On success without `--output`, write Markdown to standard output and print
   nothing else.
2. On success with `--output`, write the file and print a short `rich` success
   summary to standard error or the terminal.
3. On user error, such as a missing input file or a non-`.docx` extension,
   exit non-zero with a concise `rich` error.
4. On extraction warnings, such as an encountered but unsupported body feature,
   keep producing output where safe and show a warning summary.

Do not add format-selection flags, JSON output, or multiple subcommands in the
first milestone.

## Markdown and CriticMarkup rules

These rules must be baked into tests before implementation is considered done.

1. Plain headings and paragraphs preserve document order.
2. A commented span becomes a CriticMarkup highlight immediately followed by a
   CriticMarkup comment.
3. Comment metadata is normalized as:
   `Author, YYYY-MM-DDTHH:MM:SSZ: comment text` when metadata is present.
4. Multi-paragraph comment bodies are flattened into a single inline comment
   string using a documented separator.
5. Leading and trailing whitespace around the highlighted source span remains
   outside the highlight unless Word anchored it inside the range.
6. Cross-paragraph comment ranges are represented deterministically and covered
   by a dedicated fixture.
7. Literal CriticMarkup delimiter sequences in source or comment text are
   escaped or transformed consistently and covered by unit tests.

## Testing strategy

Begin with tests. No production extraction code should be added before the
first failing tests land.

### Behavioural tests

Add `pytest-bdd` scenarios describing the user-visible CLI contract. Create at
least these scenarios:

1. Extract a simple document with one inline comment to standard output.
2. Extract a document whose comment range spans multiple runs in one paragraph.
3. Extract a document whose comment range spans multiple paragraphs.
4. Write output to `--output` and report success cleanly.
5. Reject a missing input path with a non-zero exit and readable error text.

The behavioural tests should run the installed CLI or module entry point, not
call private helpers.

### Unit tests

Add focused tests for:

- paragraph-style to Markdown heading mapping,
- comment metadata normalization,
- CriticMarkup escaping,
- anchor reconstruction from XML markers,
- cross-paragraph span flattening,
- renderer whitespace handling around highlighted spans, and
- warning generation for unsupported body features.

### Fixtures

Use a mix of:

- tiny synthetic `.docx` fixtures created in tests using `python-docx`,
- approved expected Markdown snapshots stored under `tests/fixtures/`, and
- the provided `commented-pentagon-draft-sam-c.docx` as a smoke and regression
  fixture.

The sample fixture is large enough that the approved output should focus on a
small, asserted excerpt plus structural counts unless storing the full rendered
Markdown proves useful and stable.

## Implementation milestones

### Milestone 1: establish fixtures, tests, and dependency wiring

Update `pyproject.toml` to add runtime dependencies `python-docx`, `cyclopts`,
and `rich`, and dev dependencies `pytest-bdd` plus any typing stubs that are
actually needed. Add the CLI entry point. Create the initial failing
behavioural and unit tests, plus the design document stub and users’ guide
placeholder sections.

Success signal:

```bash
make test
```

Expected result before production code exists:

```plaintext
New behavioural and unit tests fail for missing CLI and missing extraction logic.
Existing tests, if any, continue to pass.
```

### Milestone 2: reconstruct comment anchors and build the internal model

Implement document loading, paragraph traversal, comment metadata lookup, and
anchor reconstruction. This milestone ends when unit tests can prove that the
extractor returns correct block and inline models for simple, multi-run, and
cross-paragraph fixtures.

Success signal:

```bash
uv run pytest -v tests/unit
```

Expected result:

```plaintext
All extraction and model unit tests pass.
Behavioural CLI tests may still fail on final rendering or presentation.
```

### Milestone 3: render Markdown and expose the CLI

Implement the renderer, the `cyclopts` command, and `rich` success and failure
paths. Make the behavioural scenarios pass. Keep standard output clean when it
is meant to contain the Markdown document.

Success signal:

```bash
uv run pytest -v tests/features
```

Expected result:

```plaintext
All CLI scenarios pass, including output-file and error-path cases.
```

### Milestone 4: document, smoke test, and harden

Update `docs/users-guide.md`, `README.md`, and the design document with the
actual CLI contract and known limitations. Run the CLI against
`commented-pentagon-draft-sam-c.docx`, capture a small excerpt of the produced
Markdown in the design document or commit notes, and tighten any gaps found in
tests.

Success signal:

```bash
uv run python -m docx_comment_extractor.cli \
  commented-pentagon-draft-sam-c.docx > /tmp/pentagon-comments.md
```

Expected result:

```plaintext
The command exits 0 and the generated Markdown contains headings plus inline
CriticMarkup comment markers matching the sample document's anchored comments.
```

## Validation and gate replay

Run the repository gates sequentially with `tee` logs. Use `/tmp` for logs
only, not build output. Recommended log names:

```bash
make fmt 2>&1 | tee /tmp/fmt-$(basename "$PWD")-$(git branch --show).out
make markdownlint 2>&1 | tee /tmp/markdownlint-$(basename "$PWD")-$(git branch --show).out
make nixie 2>&1 | tee /tmp/nixie-$(basename "$PWD")-$(git branch --show).out
make check-fmt 2>&1 | tee /tmp/check-fmt-$(basename "$PWD")-$(git branch --show).out
make lint 2>&1 | tee /tmp/lint-$(basename "$PWD")-$(git branch --show).out
make typecheck 2>&1 | tee /tmp/typecheck-$(basename "$PWD")-$(git branch --show).out
make test 2>&1 | tee /tmp/test-$(basename "$PWD")-$(git branch --show).out
```

If only the plan document changes in this turn, the required gates are:

```bash
make fmt
make markdownlint
make nixie
```

## Approval gate

This document is a draft only. Do not start Milestone 1 implementation until
the user explicitly approves the plan or requests edits to it.

## Progress

- [x] 2026-04-09T20:35:31+01:00: Read repository instructions, tooling, and
  documentation guidance.
- [x] 2026-04-09T20:35:31+01:00: Inspected the supplied sample document and
  confirmed that comment anchors use OOXML range markers and that 46 comments
  span multiple paragraphs.
- [x] 2026-04-09T20:35:31+01:00: Drafted the initial ExecPlan.
- [x] 2026-04-09T21:34:00+01:00: User approved implementation and requested
  users' guide updates, comprehensive unit and behavioural coverage, and
  snapshot or golden tests using `syrupy`.
- [x] 2026-04-09T21:52:00+01:00: Added runtime dependencies
  `python-docx`, `cyclopts`, and `rich`, plus `pytest-bdd` and `syrupy` for
  test coverage and golden snapshots.
- [x] 2026-04-09T21:53:00+01:00: Established the red phase. The new unit and
  behavioural tests failed during collection because
  `docx_comment_extractor.extractor` and `docx_comment_extractor.renderer` did
  not exist yet.
- [x] 2026-04-09T22:05:00+01:00: Implemented the internal document model,
  extractor, Markdown renderer, and CLI.
- [x] 2026-04-09T22:07:00+01:00: Added `pytest-bdd` CLI scenarios and seven
  committed `syrupy` snapshots covering synthetic fixtures and the supplied
  sample document excerpt.
- [x] Implement Milestone 1.
- [x] Implement Milestone 2.
- [x] Implement Milestone 3.
- [x] 2026-04-09T22:14:00+01:00: Smoke-tested the provided sample document and
  generated `/tmp/pentagon-comments.md` with 247 highlights, 247 inline
  comments, and no warnings on standard error.
- [x] Updated `README.md`, `docs/users-guide.md`, and
  `docs/comment-extraction-design.md` with the implemented CLI contract,
  limitations, and smoke-test findings.
- [x] Implement Milestone 4.
- [x] 2026-04-09T22:31:00+01:00: Replayed `make fmt`,
  `make markdownlint`, `make nixie`, `make check-fmt`, `make lint`,
  `make typecheck`, and `make test` successfully.

## Surprises & Discoveries

- 2026-04-09T20:35:31+01:00: The sample `.docx` is not a toy case. It contains
  247 comments, and 46 of those comment ranges cross paragraph boundaries.
- 2026-04-09T20:35:31+01:00: `python-docx` 1.2.0 documents comment access via
  `Document.comments`, but the anchor reconstruction still depends on
  `w:commentRangeStart` and `w:commentRangeEnd` markers in the main document
  XML.
- 2026-04-09T20:35:31+01:00: The current repository has no tests, CLI surface,
  or design docs yet, so the first milestone must establish all three.
- 2026-04-09T21:20:00+01:00: The supplied sample document does not contain
  nested or overlapping comment ranges. The maximum observed active depth is
  one, which lowers the first-cut rendering risk.
- 2026-04-09T22:01:00+01:00: Synthetic `.docx` fixtures inherit the current
  timestamp when `Document.add_comment(...)` is used. Snapshot tests were
  unstable until the helper forced a deterministic `w:date` attribute on each
  synthetic comment.
- 2026-04-09T22:04:00+01:00: `python-docx` inserts an extra run containing a
  `commentReference` marker after each `commentRangeEnd`. It contributes no
  visible text, so the extractor must ignore zero-text runs rather than
  treating them as content fragments.
- 2026-04-09T22:14:00+01:00: The supplied sample document rendered cleanly
  without warnings, which confirms that the first release's paragraph and
  heading support is enough for the initial acceptance target.
- 2026-04-09T22:26:00+01:00: `cyclopts` resolves command annotations via
  `typing.get_type_hints()` when the decorated command is registered. `Path`
  therefore needed to remain a real runtime import in `cli.py`, even though
  most other files could move type-only imports behind `TYPE_CHECKING`.

## Decision Log

- 2026-04-09T20:35:31+01:00: Use `python-docx` as the parsing entry point, but
  permit low-level XML traversal through `python-docx`'s underlying objects.
  This satisfies the requested parser choice without pretending the high-level
  API alone exposes anchor ranges.
- 2026-04-09T20:35:31+01:00: Treat cross-paragraph comment ranges as a first-
  class requirement in v1 because the supplied sample contains many of them.
- 2026-04-09T20:35:31+01:00: Keep `mdast` optional. The plan requires an
  explicit internal document model, which can later map cleanly to an
  `mdast`-style AST if a Python-native option proves worthwhile.
- 2026-04-09T20:35:31+01:00: Keep the initial CLI to one command with optional
  file output. A broader command surface would add design risk without helping
  the primary use case.
- 2026-04-09T21:34:00+01:00: Treat `syrupy` snapshots as the primary golden
  mechanism. Use them for rendered Markdown regressions while keeping
  behavioural assertions focused on CLI contract and error handling.
- 2026-04-09T22:00:00+01:00: Represent comment ranges using fragment-local
  start and end comment identifier tuples instead of pre-rendered inline
  strings. This keeps extraction and rendering separate while still handling
  cross-paragraph spans deterministically.
- 2026-04-09T22:01:00+01:00: Pin synthetic comment timestamps in the test
  fixture builder. Stable golden tests matter more than mirroring live-clock
  metadata in generated fixtures.
- 2026-04-09T22:14:00+01:00: Keep the sample smoke result in the design and
  user documentation, not only in the test suite. This gives future readers a
  concrete reference output and acceptance baseline.
- 2026-04-09T22:26:00+01:00: Keep `Path` as a runtime import in the CLI module
  and suppress the corresponding Ruff `TC003` warning narrowly. This is a real
  `cyclopts` runtime requirement rather than dead import noise.

## Outcomes & Retrospective

The first release is complete. The tool now provides a single
`docx-comment-extractor INPUT.docx [--output OUTPUT.md]` command backed by
`cyclopts`, `python-docx`, and `rich`. It preserves paragraph and heading
order, reconstructs Word comment ranges from OOXML markers, renders inline
CriticMarkup comments, warns on unsupported top-level tables, and documents the
delivered behaviour in the README, users' guide, and design note.

The most useful implementation lessons were:

- comment bodies and comment anchors live on different abstraction levels in
  `python-docx`, so a hybrid model is the right boundary,
- deterministic test fixtures require pinned comment timestamps, and
- `cyclopts` performs real runtime annotation resolution, which makes some
  imports operational rather than type-only.
