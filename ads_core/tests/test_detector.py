# -*- coding: utf-8 -*-
"""识别器测试：7 类报表列名指纹 + 不确定即停（设计 §2.3）。"""

from __future__ import annotations

from ads_core.ingest.detector import detect, infer_attribution_window, normalize_header

from .conftest import HEADERS


def test_normalize_header():
    assert normalize_header(" Portfolio Name ") == "portfolio name"
    assert normalize_header("\ufeffDate") == "date"
    assert normalize_header("7 Day Total Sales") == "7 day total sales"


def test_detect_all_seven_types():
    expectations = {
        "campaign": "campaign",
        "adgroup": "adgroup",
        "target": "target",
        "searchterm": "searchterm",
        "placement": "placement",
        "advertised_product": "advertised_product",
        "purchased_product": "purchased_product",
    }
    for rtype, expected in expectations.items():
        result = detect(HEADERS[rtype])
        assert result.detected_type == expected, (
            f"{rtype} 识别为 {result.detected_type}（conf={result.confidence}, "
            f"ambiguities={result.ambiguities}）"
        )
        assert result.confidence >= 0.85


def test_detect_unknown_on_garbage():
    result = detect(["Nothing", "Recognizable", "Here"])
    assert result.is_unknown
    assert result.ambiguities.get("reason") in {"low_confidence_or_margin", "no_report_type_scored"}


def test_detect_needs_type_when_ambiguous():
    """缺关键列（campaign 无度量列）→ 不猜测 → unknown + ambiguities。"""
    result = detect(["Date", "Campaign Id"])
    assert result.is_unknown


def test_attribution_window_inference():
    assert infer_attribution_window(HEADERS["campaign"]) == 7
    sb_headers = [h.replace("7 Day", "14 Day") for h in HEADERS["campaign"]]
    assert infer_attribution_window(sb_headers) == 14
    assert infer_attribution_window(["Date", "Impressions"]) is None
