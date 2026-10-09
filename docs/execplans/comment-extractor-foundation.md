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

- Open the document through an injectable loader whose default uses
  `python-docx`, and translate known loading failures into `ExtractionError`.
- Walk the main document body in source order, collecting block items
  through `Document.iter_inner_content()`.
- Reconstruct comment anchor ranges by scanning the underlying XML elements for
  `w:commentRangeStart` and `w:commentRangeEnd`.
- Isolate private `Paragraph._element` access in one compatibility adapter.
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

The CLI emits bounded standard-library logging events for validation,
extraction, warnings, and output writes. Logging configuration remains the
responsibility of the calling application or operator, and events exclude raw
document content and filesystem paths.

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

### Property tests

Delivered after the initial release, as a follow-up to the 2026-07-23 decision
log entry. `tests/unit/test_renderer_properties.py` adds a bounded Hypothesis
suite with two properties.

Reconstruction generates a `DocumentModel` by laying disjoint comment ranges
over a stream of fragment slots and cutting that stream into blocks, so
generated documents include cross-paragraph ranges, single-fragment ranges,
adjacent ranges, empty fragments, fragment-free blocks, and headings at every
level. Two oracles check the rendered Markdown independently of the renderer:
one asserts that removing only the documented markers and comment bodies leaves
the source text with heading prefixes and block joins in source order, and the
other asserts that each range closes exactly once, after its matching start.

Escaping is covered by three properties over `escape_criticmarkup_text`:
arbitrary text containing every supported delimiter, Unicode, backslashes, raw
HTML, and Markdown link syntax must leave no live construct unguarded;
inverting the documented transformations must recover the original text; and
text built only from non-escapable characters must be returned unchanged.

`hypothesis` was added to the `dev` dependency group and `uv.lock` regenerated
with `uv lock`, following the repository's dependency workflow.

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

Approval was granted on 2026-04-09, and implementation is complete. This gate is
closed; future changes must update this living plan and follow the repository
quality gates.

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
- [x] 2026-07-23: Addressed review follow-up by adding an injectable document
  loader and explicit extraction error, expanding CLI validation coverage,
  documenting developer workflows, and adding bounded structured events.

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
- 2026-07-23: Keep package loading behind an injectable `DocumentLoader` and
  translate expected third-party failures into `ExtractionError` at that
  boundary.
- 2026-07-23: Use unconfigured standard-library logging with bounded operation,
  outcome, error-class, and count fields. Exclude source payloads and raw paths.
- 2026-07-23: Recommend future property-based tests for balanced cross-paragraph
  ranges and CriticMarkup escaping without asserting false idempotence.
  **Delivered 2026-10-03** in `tests/unit/test_renderer_properties.py` with
  Hypothesis added to the `dev` dependency group. The recommendation to avoid a
  false idempotence assertion is honoured: idempotence is not asserted
  anywhere, and the guide and design document record why.
- 2026-08-15: Use same-directory temporary files and atomic replacement for
  output writes; the delivered outcome preserves an existing output file when
  writing or replacement fails.
- 2026-09-26: Resolve the `docs/documentation-style-guide.md` rebase conflict in
  favour of the branch's concrete `` `color` `` example. Main reworded the line
  to "library using American spelling" purely to dodge the shared dictionary;
  the branch supersedes that workaround with a narrow, deliberate spelling
  exception (`patterns.ignore = ['`color`']`), which `generate_typos_config.py`
  merges into `typos.toml`. Adopting main's wording would have orphaned that
  exception as dead configuration.
- 2026-09-29: Restack onto the advanced `origin/main` (`39d476d`), which had
  gained a coverage-interpreter contract and action bumps since the previous
  rebase. Two conflicts arose and both preserve intent on each side. The
  `pyproject.toml` `dev` group takes the union: main's
  `packaging>=26.3,<27.0` plus the branch's `pytest-bdd`, `syrupy`, and
  `lxml-stubs`. `docs/developers-guide.md` was an add/add conflict because
  both branches independently created it; the resolution keeps the branch's
  six-section guide verbatim, adopts main's introductory sentence, and
  appends main's `## Coverage workflow contract` section last, which places it
  after every line the branch's later commits touch so they replay without
  further conflict. `uv.lock` was regenerated afterwards, moving `packaging`
  from 26.0 to 26.3 because the merged pin excludes 26.0.

- 2026-10-03: Put `scripts/` on ty's module search path in both
  `[tool.ty.environment] extra-paths` in `pyproject.toml` and
  `--extra-search-path scripts` in `make typecheck`, so the spelling helper's
  flat top-level imports resolve during type checking.

- 2026-10-03: Restack onto the advanced `origin/main` (`057d323`), which had
  gained the shared CV-005 contract migration, six commits of coverage and
  dependency bumps, and a Ruff bump to 0.16.9. Only `uv.lock` conflicted, and
  it did so as an add/add at four separate commits because both branches added
  the file from a base without it. Every conflict took the upstream side, per
  the lock-file policy, and `uv lock` rebuilt the file afterwards; no hand
  merge of lock content was attempted. `docs/scripting-standards.md`,
  `docs/developers-guide.md`, and `pyproject.toml` auto-merged, and the
  CV-005 text and `.github/cv005.toml` survive alongside the branch's own
  wording.

- 2026-10-03: Route the CLI's runtime seams through the real Cyclopts command
  path. Cyclopts can only inject parsing-time values, so a
  `Parameter(parse=False)` bundle never reaches the command; instead `main`
  builds one `_RuntimeDependencies`, `_build_app` binds the command to it by
  closure, and the command reuses the public `extract_comments` docstring for
  help text. One owner and one clock now serve both the command path and the
  terminal-failure handlers, which removes the previously dead injected
  `metrics`/`clock` parameters. `_production_dependencies` is the single place
  that chooses the concrete metrics owner and clock, so `extract_comments`
  constructs neither: it delegates the default policy to that factory. The
  standard-output write is also a `_RuntimeDependencies.stdout` seam rather
  than a direct `sys.stdout` call.

- 2026-10-03: Declare `lxml` as a direct runtime dependency. The plan named
  only `python-docx`, `cyclopts`, and `rich`, but the extraction boundary must
  catch `XMLSyntaxError` from `lxml.etree` when a malformed OOXML package
  fails to parse, and translate it into `ExtractionError`. Relying on the
  transitive copy pulled in by `python-docx` would leave the import working but
  undeclared.

- 2026-10-03: Validate ZIP package metadata before extraction:
  `MAX_PACKAGE_MEMBERS` rejects packages with more than 10,000 members and
  `MAX_UNCOMPRESSED_BYTES` rejects packages whose declared uncompressed content
  exceeds 100 MiB. The plan did not address decompressed size, and an on-disk
  size check alone accepts a small archive that expands enormously, so these
  metadata checks reject such packages before they are loaded.

- 2026-10-03: Third restack attempt against `origin/main` resolved to an
  explicit no-op. The target was still `057d323`, which is exactly the
  boundary the previous restack replayed onto, so `OLD_BASE == TARGET` and
  there were no commits to replay. The branch is 25 commits ahead, 0 behind.
  This is the first restack with an empty range, and the earlier two both had
  real conflicts, so "rebase again" was *not* safe to assume as a repeat of
  that shape: an empty range is a distinct outcome that warrants a recorded
  decision rather than a silent rewrite. The boundary was confirmed from
  three independent sources before being accepted, on the principle that a
  local tracking ref alone is not evidence — a lesson from the previous
  restack, where pushes over a command-scoped SSH URL left
  `origin/comment-extractor-foundation` stale. Here `git rev-parse
  origin/main`, the GitHub API `git/ref/heads/main` and `commits/main`
  endpoints, and the PR's `baseRefOid` all agreed on `057d323`. Because
  nothing was replayed, no conflict resolution, no `uv lock` rebuild, and no
  lock-file policy application arose. The four required gates were run
  anyway, since they are a condition of the request rather than of the
  rewrite: `check-fmt`, `test` (366 passed), `typecheck`, and `lint` were all
  green on the unchanged candidate `7400c24`, and the tree hash
  `45695d78e454ecd119c6590790d2a7cf14673314` was byte-identical before and
  after. Weave did not participate: although the driver is registered
  globally, the repository has no tracked `.gitattributes` and no
  `.git/info/attributes`, so `git check-attr merge` reports `unspecified` for
  every path, including `uv.lock`, and Git's built-in merge machinery with
  `zdiff3` is what would have run.

- 2026-10-03: Close the "Testing (Property / Proof)" pre-merge warning by
  delivering the property tests the 2026-07-23 entry recommended, rather than
  by arguing the warning down. Three design points were settled by experiment
  before the suite was written, and each changed the result.

  *The reconstruction oracle must not carry escaping logic.* Generated document
  text avoids delimiter and ampersand characters, so the oracle that removes
  markers and comment bodies needs no inverse of `escape_criticmarkup_text`.
  Had the generated text contained delimiters, the oracle would have had to
  unescape, which is the production algorithm restated.

  *A round-trip oracle needs a backslash-free alphabet.* An input such as
  `~\\>` inverts to `~>`, so a text-level round trip is ambiguous whenever the
  input already contains a backslash before a delimiter tail. Exhaustive search
  found 1,763 such cases over a 15-character alphabet, all of them this shape
  and none a defect. The round trip is therefore restricted to a backslash-free
  alphabet, backslashes are covered by a separate pass-through property (they
  are returned byte for byte, since no escape sequence uses one), and
  neutralization is checked as a soundness property that accepts a delimiter
  preceded by a source backslash as already guarded.

  *Block parts are not a reliable structural probe.* An earlier draft asserted
  that splitting the output on the blank-line join yields one part per block.
  That fails when a fragment-free block contributes only a heading prefix, or
  not even that, so it was dropped in favour of the text-preservation oracle,
  which compares the whole document at once.

  Both oracles were validated by exhaustive and randomized search before being
  committed: roughly 1.1 million exhaustive strings for escaping and 30,000
  generated documents for reconstruction, covering 45,366 ranges (7,343 of them
  cross-paragraph), 44,902 empty fragments, and 7,546 fragment-free blocks.

  *The suite was mutation-tested, and that found generator gaps rather than
  production defects.* Twenty deliberate defects were injected into
  `renderer.py` one at a time. The first pass missed three, and each miss was a
  real weakness in what the generator could reach, not a defect in the
  renderer:

  - Dropping the '&' escape rule was invisible because '&' is not in the inert
    alphabet and the only reachable ampersand came from an `&lt;` sample that
    was already entity-shaped. Bare and entity-shaped ampersands are now drawn
    explicitly.
  - Swapping the order of the two escape layers was invisible because no
    generated input put a delimiter inside a raw HTML span. Exhaustive search
    confirms the order is observable — 6,260 strings over a small alphabet
    distinguish it, all of the shape `<a~>` where the span swallows the
    delimiter before the CriticMarkup layer runs. The generator now builds tags
    with delimiter-bearing content.
  - Reversing `end_comment_ids` turned out to be unobservable rather than
    merely unreached. Two ranges can share a final fragment slot only by
    sharing that slot, which is overlap, so under the disjoint semantics the
    extractor produces the `reversed()` call can never iterate over more than
    one element. Enumerating every disjoint set of up to three ranges over up
    to seven slots confirms no such set exists.

  Detection was also flaky run to run, because whether a property caught a
  given mutation depended on the random draw. Each delimiter, a bare ampersand,
  and the order-sensitive `<a~>` shape are now pinned with `@example`, so the
  suite is sensitive to nineteen of the twenty mutations on every run. The
  twentieth is the unreachable one above.

  No production defect was found, so `renderer.py` is unchanged by this work.

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

Review follow-up hardened the loader and output boundaries, added regression
coverage for user-facing validation, documented the development architecture,
and made operational decisions observable without exposing document payloads.

The branch was later rebased onto a `main` that had adopted CodeScene coverage
publication and dependabot action bumps. Two conflicts arose, both resolved by
keeping the branch's intent and folding in main's improvements: the `dev`
dependency group now carries main's `pyyaml`, `ty`, and `ruff==0.16.8` bump
alongside the branch's `pytest-bdd` and `syrupy`, and the style-guide spelling
line kept the branch's concrete example. `uv.lock` was rebuilt after the merge.
Note that `make lint` and `make check-fmt` pin ruff to 0.14.13 in the `Makefile`
independently of the `pyproject.toml` dev-group version, so the 0.16.8 bump does
not change what those gates enforce; CI runs only `make check-fmt`,
`make lint`, `make spelling`, and `make typecheck`.

A second rebase followed the same pattern once `main` advanced again to
`39d476d`. The add/add conflict on `docs/developers-guide.md` confirmed that
the two documents are complementary rather than competing: main documents the
coverage-interpreter contract, whilst the branch documents development setup,
dependencies, command flow, extraction and rendering boundaries, testing
strategy, and observability. Recording both in one file keeps the single
entry point that `README.md` links.

The rebase also demonstrated a recurring maintenance cost worth naming: the
generated `typos.toml` is rewritten whenever the `spelling` gate runs, because
`scripts/generate_typos_config.py` performs a conditional HTTPS fetch of the
shared dictionary. A rebase therefore tends to carry an incidental
`typos.toml` refresh alongside its intended changes, which is best committed
separately so the substantive diff stays legible.
