# Developers' guide

This guide records internal conventions for maintaining docx-comment-extractor.

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
