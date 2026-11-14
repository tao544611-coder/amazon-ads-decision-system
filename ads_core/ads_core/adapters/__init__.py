# -*- coding: utf-8 -*-
"""adapters：源适配器（D11）。领星等后续按 D11 增补。"""

from .amazon_console import AmazonConsoleAdapter
from .base import AdapterError, CanonicalRow, SourceAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "amazon_console": AmazonConsoleAdapter,
}

__all__ = ["ADAPTERS", "AdapterError", "CanonicalRow", "SourceAdapter", "AmazonConsoleAdapter"]
