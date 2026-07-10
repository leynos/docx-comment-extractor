"""Select the optional Rust backend or its pure-Python fallback."""

from __future__ import annotations

PACKAGE_NAME = "docx_comment_extractor"

try:  # pragma: no cover - Rust optional
    rust = __import__(f"_{PACKAGE_NAME}_rs")
    hello = rust.hello  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python fallback
    from .pure import hello as hello

__all__ = ["hello"]
