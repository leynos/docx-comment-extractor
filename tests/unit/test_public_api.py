"""Tests for the supported package-level API."""

from __future__ import annotations

import docx_comment_extractor as package
from docx_comment_extractor import extractor, renderer


def test_package_exports_the_required_public_api() -> None:
    """The package should expose exactly the documented v0.1.0 public API."""
    # Asserting the exact contents keeps this test non-vacuous: parametrizing
    # over ``package.__all__`` would pass even if ``__all__`` were emptied.
    assert set(package.__all__) == {
        "ExtractionError",
        "extract_document",
        "render_document",
    }, "the declared public API should match the documented exports"

    assert package.ExtractionError is extractor.ExtractionError, (
        "the package should re-export ExtractionError from its defining module"
    )
    assert package.extract_document is extractor.extract_document, (
        "the package should re-export extract_document from its defining module"
    )
    assert package.render_document is renderer.render_document, (
        "the package should re-export render_document from its defining module"
    )
    assert not hasattr(package, "hello"), (
        "the pre-release placeholder greeting should no longer be exported"
    )
