"""Public package API for `docx-comment-extractor`."""

from __future__ import annotations

from .extractor import ExtractionError, extract_document
from .renderer import render_document

__all__ = ["ExtractionError", "extract_document", "render_document"]
