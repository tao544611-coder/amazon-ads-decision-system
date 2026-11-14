# -*- coding: utf-8 -*-
"""真实样本校准回归（O-1/O-2）：samples/ 存在时才执行，否则 skip。

样本含真实经营数据（gitignore），断言仅针对表头识别，不落库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ads_core.ingest.detector import detect
from ads_core.ingest.reader import read_table

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "raw-reports"

REAL_EXPECTATIONS = {
    # 亚马逊控制台英文报表（含 WPS 缓存副本）
    "20261112_Sponsored_Products_Targeting_report (3).xlsx": ("target", 7),
    "20261112_Sponsored_Products_Search_term_report (3).xlsx": ("searchterm", 7),
    "20261112_Sponsored_Products_Advertised_product_report (3).xlsx": ("advertised_product", 7),
    "Sponsored_Products_Campaign_report.csv": ("campaign", 7),
    "Sponsored_Brands_Campaign_report.xlsx": ("campaign", 14),
    "Sponsored_Products_Search_term_report (1).xlsx": ("searchterm", 7),
    # 领星中文 / 本地清洗派生：阶段一不支持（D11 后续增补）→ 必须 unknown
    "关键词报告(8.22--9.19)-汇总-2101946964824051713.xlsx": ("unknown", None),
    "广告位报告(8.22--9.19)-汇总-2101940911973609473.xlsx": ("unknown", None),
    "advertised_product_3SKU_明细.csv": ("unknown", None),
}

pytestmark = pytest.mark.skipif(
    not SAMPLES.is_dir(), reason="samples/raw-reports 不在本机（真实样本仅本地校准）"
)


@pytest.mark.parametrize("file_name,expected", sorted(REAL_EXPECTATIONS.items()))
def test_real_sample_detection(file_name: str, expected: tuple[str, int | None]) -> None:
    path = SAMPLES / file_name
    if not path.is_file():
        pytest.skip(f"样本文件缺失：{file_name}")
    table = read_table(path)
    result = detect(table.headers)
    want_type, want_window = expected
    assert result.detected_type == want_type, (
        f"{file_name}: 识别为 {result.detected_type}（conf={result.confidence}，"
        f"ambiguities={result.ambiguities}）"
    )
    if want_window is not None:
        assert result.attribution_window_days == want_window
    else:
        assert result.attribution_window_days is None or result.is_unknown
