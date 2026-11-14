# -*- coding: utf-8 -*-
"""adapters：源适配器（D11）。新数据源在此注册。"""

from .amazon_console import AmazonConsoleAdapter
from .base import AdapterError, CanonicalRow, SourceAdapter
from .lingxing_erp import LingXingErpAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "amazon_console": AmazonConsoleAdapter,
    "lingxing_erp": LingXingErpAdapter,
}

__all__ = [
    "ADAPTERS",
    "AdapterError",
    "CanonicalRow",
    "SourceAdapter",
    "AmazonConsoleAdapter",
    "LingXingErpAdapter",
]
