# -*- coding: utf-8 -*-
"""依映射表前缀规则补全 campaign_map 归属依据（2026-11-14 Gordon 指示）。

规则：活动名前缀 = 后台 SKU 前缀 + KNGT（映射表 2026-10-12 定稿 + 台账旁证：
GDIEBKNGT→6384、GJEBBKNGT→6941、HCCBWKNGT→7221 全部吻合）。
GJHIUBKNGT-* 6 个活动 → GJHIUB-YCNGT-AA2-K（6978 黑青 B0HB4XFV2F）→ 6978。
6979 的 ASIN 投放在这些活动内（W46 facts），6979 自身无 GJHJUBKNGT/GJHJDGKNGT 前缀活动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ads_core"))

from ads_core.storage import CampaignMap, session_scope  # noqa: E402
from ads_core.storage.audit import write_audit  # noqa: E402
from ads_core.storage.models import now_iso  # noqa: E402
from sqlalchemy import select  # noqa: E402

NOTE = (
    "归属依据：活动名前缀 GJHIUBKNGT = 后台 SKU 前缀 GJHIUB-YCNGT-AA2-K"
    "（6978 黑青 B0HB4XFV2F，在营产品映射表 2026-10-12 定稿）+ KNGT；"
    "6979 ASIN 亦投放本活动（W46 facts 2026-11-12），其量按 advertised 粒度拆分（阶段二）。"
)


def main() -> None:
    with session_scope() as s:
        rows = s.scalars(select(CampaignMap)).all()
        updated = 0
        for row in rows:
            if (row.campaign_name or "").startswith("GJHIUBKNGT-"):
                row.note = NOTE
                row.mapped_by = "agent:zcode(映射表前缀规则+Gordon指示)"
                updated += 1
        write_audit(
            s, "agent:zcode", "campaign_map.note_update", "campaign_map", None,
            {"updated": updated, "basis": "在营产品映射表 2026-10-12 前缀规则"},
        )
        s.commit()
        print(json.dumps({"updated": updated}, ensure_ascii=False))


if __name__ == "__main__":
    main()
