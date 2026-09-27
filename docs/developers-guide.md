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
generate-coverage call in every workflow to declare at least one of those
sources, every declared source to name the same version, that version to be
inside `requires-python`, and every call to measure on that one version,
because the pull-request ratchet is only meaningful against a baseline measured
on the same Python. A `setup-python` step guarded by `if:` or allowed to fail
with `continue-on-error` declares nothing. A Hypothesis property checks the
reading against a naive model. The contract uses `packaging` and `hypothesis`,
both development dependencies.
