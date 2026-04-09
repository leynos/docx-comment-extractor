"""Public package API for `docx-comment-extractor`."""

from __future__ import annotations

from .extractor import extract_document
from .renderer import render_document

__all__ = ["extract_document", "render_document"]
