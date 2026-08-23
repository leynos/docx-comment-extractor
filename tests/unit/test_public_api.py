"""Tests for the supported package-level API."""

from __future__ import annotations

import pytest

import docx_comment_extractor as package


@pytest.mark.parametrize("public_name", package.__all__)
def test_package_exports_every_declared_public_name(public_name: str) -> None:
    """Every name declared by ``__all__`` should be available from the package."""
    assert getattr(package, public_name, None) is not None, (
        "the package should export every name declared in its public API"
    )
