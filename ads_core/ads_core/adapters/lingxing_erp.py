# -*- coding: utf-8 -*-
"""领星 ERP 报表适配器（D11 一期：每周投放报告-每日明细）。

行级映射（24 列中文表头 → can_target 统一行）：
- 「类型」列（SP/SB/SB2/SD）逐行分派 ad_product_type——单文件混表是常态
  （实测 23 份周报中 20 份 SP+SB 混合），不能按文件级分派；
- 「投放」列 = 关键词文本或商品投放（'商品:"B0TEST0006"'）→ target_text，
  商品投放解析出 advertised_asin；无关键词 Id 列 → target_id 以投放文本兜底（O-1 同款约定）；
- 「广告活动」名作 campaign 业务键（领星导出无 Campaign Id 列，O-1 校准记录 1）；
- 匹配方式中文归一（词组/精准/广泛 → phrase/exact/broad），原值保留 attrs；
- 汇总版报表（无「日期」列）在识别层即被拒，不会进入本适配器。
"""

from __future__ import annotations

import re
from typing import Any

from ..ingest.detector import load_fingerprints, normalize_header
from ..ingest.reader import TableData, parse_date, parse_number
from ..storage.models import RawDataset
from .base import AdapterError, CanonicalRow, SourceAdapter

COL = {
    "date": ["日期"],
    "campaign": ["广告活动"],
    "adgroup": ["广告组"],
    "portfolio": ["广告组合"],
    "target_text": ["投放"],
    "match_type": ["匹配方式"],
    "type": ["类型"],
    "store": ["店铺名称"],
    "note": ["备注"],
}

# 「类型」列 → ad_product_type（SB2=SB 升级版，归属 sb；未知值显式报错不静默吞）
TYPE_MAP = {"sp": "sp", "sb": "sb", "sb2": "sb", "sd": "sd", "sbv": "sbv"}

# 匹配方式中文 → canonical 统一值（与后台英文口径对齐）；'--'/空 → None
MATCH_MAP = {"精准": "exact", "词组": "phrase", "广泛": "broad"}

_PRODUCT_TARGET_RE = re.compile(r'商品\s*[:：]\s*"([A-Z0-9]{10})"')


def _pick(norm_row: dict[str, str], keys: list[str]) -> tuple[str | None, str]:
    for k in keys:
        if k in norm_row:
            return k, norm_row[k]
    return None, ""


def _num(value: str) -> float | None:
    try:
        return parse_number(value)
    except ValueError as exc:
        raise AdapterError(f"数值列无法解析：{value!r}") from exc


def _int(value: str) -> int | None:
    v = _num(value)
    if v is None:
        return None
    return int(round(v))


class LingXingErpAdapter(SourceAdapter):
    source_system = "lingxing_erp"

    def supports(self, raw: RawDataset) -> bool:
        return (
            super().supports(raw)
            and (raw.source_system or self.source_system) == self.source_system
        )

    def transform(self, raw: RawDataset, table: TableData) -> list[CanonicalRow]:
        if raw.report_type is None or raw.report_type == "unknown":
            raise AdapterError("报表类型 unknown，无法适配")
        if len(table) == 0:
            raise AdapterError("数据体为空")

        fp = load_fingerprints()
        aliases = {k: [normalize_header(a) for a in v] for k, v in fp["metric_aliases"].items()}
        rows: list[CanonicalRow] = []

        for raw_row in table.rows:
            norm = {normalize_header(k): v for k, v in raw_row.items()}

            date_col, date_val = _pick(norm, COL["date"])
            parsed = parse_date(date_val) if date_val else None
            if parsed is None:
                raise AdapterError(f"日期列无法解析：{date_val!r}")

            _, campaign = _pick(norm, COL["campaign"])
            if not campaign:
                raise AdapterError("缺「广告活动」列值（领星导出以活动名作业务键）")

            _, type_val = _pick(norm, COL["type"])
            ad_type = TYPE_MAP.get(type_val.strip().lower())
            if ad_type is None:
                raise AdapterError(f"未知「类型」列值：{type_val!r}（TYPE_MAP 需增补）")

            _, target_text = _pick(norm, COL["target_text"])
            asin_hit = _PRODUCT_TARGET_RE.search(target_text or "")

            _, match_raw = _pick(norm, COL["match_type"])
            match_type = MATCH_MAP.get((match_raw or "").strip())

            row = CanonicalRow(
                report_type=raw.report_type or "unknown",
                date=parsed.isoformat(),
                marketplace=raw.marketplace or "UK",
                currency=raw.currency or "GBP",
                ad_product_type=ad_type,  # 逐行分派（混合表）
                campaign_id=campaign.strip(),
            )
            row.campaign_name = campaign.strip()
            row.adgroup_id = _pick(norm, COL["adgroup"])[1].strip() or None
            row.adgroup_name = row.adgroup_id
            row.target_text = target_text.strip() or None
            row.target_id = row.target_text
            row.match_type = match_type
            row.advertised_asin = asin_hit.group(1) if asin_hit else None

            col_map: dict[str, str] = {"date": date_col, "campaign_id": "广告活动"}
            for metric, alias_list in aliases.items():
                col = next((a for a in alias_list if a in norm), None)
                if col is None:
                    continue
                val = norm[col]
                if not val:
                    continue
                col_map[metric] = col
                if metric in {"impressions", "clicks", "orders", "units"}:
                    setattr(row, metric, _int(val))
                else:
                    setattr(row, metric, _num(val))

            attrs: dict[str, Any] = dict(raw_row)
            attrs["__lingxing"] = {
                "store": _pick(norm, COL["store"])[1] or None,
                "type_raw": type_val,
                "match_type_raw": match_raw or None,
            }
            row.attrs = attrs
            row.lineage = {
                "source_system": self.source_system,
                "source_col_map": col_map,
            }
            rows.append(row)

        return rows
