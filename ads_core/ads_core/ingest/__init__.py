# -*- coding: utf-8 -*-
"""ingest：读取、指纹识别与导入。"""

from .detector import DetectionResult, detect, load_fingerprints, normalize_header
from .importer import ImportResult, finalize_batch, ingest_file, sha256_file
from .reader import TableData, parse_date, parse_number, read_table

__all__ = [
    "DetectionResult",
    "ImportResult",
    "TableData",
    "detect",
    "finalize_batch",
    "ingest_file",
    "load_fingerprints",
    "normalize_header",
    "parse_date",
    "parse_number",
    "read_table",
    "sha256_file",
]
