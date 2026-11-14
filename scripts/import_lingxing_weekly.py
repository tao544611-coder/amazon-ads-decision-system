# -*- coding: utf-8 -*-
"""领星周报历史回灌驱动（D11 一期 · 一次性运营脚本，Gordon 2026-11-14 批准）。

23 个「每周投放报告-每日明细」xlsx → 单批次（lingxing_erp）→ finalize。
canonical/facts 由调用方后续单独执行（本脚本只做 ingest + 批次收口）。
"""

from __future__ import annotations

import glob
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ads_core"))

from ads_core.ingest.importer import finalize_batch, ingest_file  # noqa: E402
from ads_core.storage import session_scope  # noqa: E402

SRC_DIR = r"D:\000GordonZou\Amazon_Work\reports\广告投放报表"
SOURCE_SYSTEM = "lingxing_erp"
ACTOR = "gordon_batch"


def main() -> None:
    files = sorted(glob.glob(str(Path(SRC_DIR) / "每周投放报告*.xlsx")))
    print(f"待导入文件数：{len(files)}")
    t0 = time.time()
    batch_id: str | None = None
    dup = []
    with session_scope() as s:
        for i, f in enumerate(files, 1):
            name = Path(f).name
            r = ingest_file(
                s, f, imported_by=ACTOR, source_type="manual_upload",
                source_system=SOURCE_SYSTEM, batch_id=batch_id, defer_finalize=True,
            )
            if batch_id is None:
                batch_id = r.batch_id
            elif r.duplicate:
                dup.append(name)
            if r.duplicate:
                print(f"[{i:2d}/{len(files)}] 跳过（hash 幂等已存在）：{name[:52]}")
                continue
            findings = [f.check_code for f in r.findings]
            print(
                f"[{i:2d}/{len(files)}] {r.status:10s} rows={r.row_count:4d} "
                f"conf={r.confidence:.2f} findings={findings or '无'} | {name[:52]}"
            )
            if i % 8 == 0:
                print(f"  —— 进度 {i}/{len(files)}，累计 {time.time() - t0:.1f}s ——")
        final = finalize_batch(s, batch_id, actor=ACTOR)
        s.commit()
    print(f"批次 {batch_id} 收口状态：{final}")
    if dup:
        print(f"幂等跳过 {len(dup)} 个文件（hash 已存在）")
    print(f"总耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
