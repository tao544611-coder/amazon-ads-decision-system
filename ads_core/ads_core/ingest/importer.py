# -*- coding: utf-8 -*-
"""导入服务 ingest_file（设计 §5）：

hash 幂等检查（已存在→返回既有 batch, duplicate=True）→ 识别 → 登记 →
文件级质检 → QF-08 supersede → 状态机终态。数据不静默覆盖（D6）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..quality import (
    Finding,
    check_batch_caliber,
    check_file_level,
    check_supersede,
    decide_status,
    transition,
)
from ..storage.audit import write_audit
from ..storage.models import (
    ImportBatch,
    QualityFinding,
    RawDataset,
    ReportIdentification,
    now_iso,
)
from .detector import DetectionResult, detect
from .reader import TableData, parse_date, read_table


@dataclass
class ImportResult:
    batch_id: str
    raw_dataset_id: str | None = None
    duplicate: bool = False
    status: str = "failed"
    detected_type: str = "unknown"
    confidence: float = 0.0
    attribution_window_days: int | None = None
    row_count: int = 0
    data_start_date: str | None = None
    data_end_date: str | None = None
    findings: list[Finding] = field(default_factory=list)
    superseded: list[dict] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _data_dates(table: TableData) -> tuple[str | None, str | None, int]:
    """从数据体推断原生日期范围（date 列优先，start date 兜底）。"""
    from .detector import normalize_header

    dates: list[str] = []
    if table.rows:
        first = table.rows[0]
        norm = {normalize_header(k): v for k, v in first.items()}
        col = next(
            (c for c in ("date", "start date", "report date", "开始日期", "日期") if norm.get(c)),
            None,
        )
        if col:
            for row in table.rows:
                v = {normalize_header(k): v for k, v in row.items()}.get(col, "")
                d = parse_date(v) if v else None
                if d:
                    dates.append(d.isoformat())
    dates.sort()
    if dates:
        return dates[0], dates[-1], len(dates)
    return None, None, 0


def ingest_file(
    session: Session,
    path: str | Path,
    imported_by: str = "cli",
    source_type: str = "manual_upload",
    source_system: str = "amazon_console",
    forced_type: str | None = None,
    ad_product_type: str = "sp",
    marketplace: str = "UK",
    currency: str = "GBP",
    batch_id: str | None = None,
    data_as_of: str | None = None,
    defer_finalize: bool = False,
) -> ImportResult:
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"导入文件不存在：{src}")

    file_hash = sha256_file(src)

    # ① 幂等：同 hash 已登记（非 rejected）→ 返回既有批次（T1）；
    #    rejected 登记（误参数批次）允许换参数重导为新 raw
    existing_raw = session.scalars(
        select(RawDataset).where(
            RawDataset.file_hash == file_hash,
            RawDataset.status != "rejected",
        )
    ).first()
    if existing_raw is not None:
        batch_existing = session.get(ImportBatch, existing_raw.batch_id)
        return ImportResult(
            batch_id=existing_raw.batch_id,
            raw_dataset_id=existing_raw.id,
            duplicate=True,
            status=batch_existing.status if batch_existing else "unknown",
            detected_type=existing_raw.report_type or "unknown",
            row_count=existing_raw.row_count or 0,
            data_start_date=existing_raw.data_start_date,
            data_end_date=existing_raw.data_end_date,
            attribution_window_days=existing_raw.attribution_window_days,
        )

    # ② 批次：复用传入批次（可追加）或新建（draft → identified）
    batch = session.get(ImportBatch, batch_id) if batch_id else None
    if batch is None:
        batch = ImportBatch(
            imported_at=now_iso(),
            imported_by=imported_by,
            source_type=source_type,
            source_system=source_system,
            status="draft",
            created_at=now_iso(),
        )
        session.add(batch)
        session.flush()
    if batch.status not in ("draft", "identified"):
        raise ValueError(
            f"批次 {batch.id} 状态 {batch.status}，不能追加文件（仅 draft/identified 可）"
        )
    if batch.status == "draft":
        batch.status = transition(batch.status, "identified")

    # ③ 落盘原始文件：data/raw/<yyyy>/<batch短id>_<原名>
    year = datetime.now(timezone.utc).strftime("%Y")
    dest_dir = config.raw_dir() / year
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"{batch.id[:8]}_{src.name}"
    if src.resolve() != stored.resolve():
        shutil.copy2(src, stored)

    # ④ 读取与识别
    table = read_table(stored)
    detection: DetectionResult = detect(table.headers)
    decided_type = forced_type or (None if detection.is_unknown else detection.detected_type)
    if forced_type:
        detection = DetectionResult(
            detected_type=forced_type,
            confidence=1.0,
            matched=detection.matched,
            missing_required=[],  # 人工指定类型：不做必要列缺失判定（由适配器兜底）
            ambiguities={"forced": True},
            attribution_window_days=detection.attribution_window_days,
        )
        decided_type = forced_type

    start_date, end_date, n_dates = _data_dates(table)
    # O-5：取数日 = 报表自带生成日期（调用方提供）> 导入日
    effective_as_of = data_as_of or now_iso()[:10]

    raw = RawDataset(
        batch_id=batch.id,
        file_name=src.name,
        stored_path=str(stored),
        file_hash=file_hash,
        file_size=src.stat().st_size,
        row_count=len(table),
        report_type=decided_type,
        ad_product_type=ad_product_type if decided_type else None,
        marketplace=marketplace,
        currency=currency,
        attribution_window_days=detection.attribution_window_days,
        data_start_date=start_date,
        data_end_date=end_date,
        data_as_of=effective_as_of,
        status="identified",
        created_at=now_iso(),
    )
    session.add(raw)
    session.flush()

    session.add(ReportIdentification(
        raw_dataset_id=raw.id,
        detected_type=detection.detected_type,
        confidence=detection.confidence,
        matched_columns_json=json.dumps(detection.matched, ensure_ascii=False),
        missing_required_json=json.dumps(detection.missing_required, ensure_ascii=False),
        ambiguities_json=json.dumps(detection.ambiguities, ensure_ascii=False),
        decided_by=f"auto{'+forced' if forced_type else ''}",
        created_at=now_iso(),
    ))

    # ⑤ 文件级质检（QF-01~05）+ QF-08 supersede；QF-06 批级在 finalize 时判
    findings = check_file_level(raw, table, detection)
    sup = check_supersede(session, raw)
    if sup is not None:
        findings.append(sup)
    for f in findings:
        session.add(QualityFinding(
            batch_id=batch.id,
            raw_dataset_id=f.raw_dataset_id,
            check_code=f.check_code,
            severity=f.severity,
            message=f.message,
            details_json=json.dumps(f.details, ensure_ascii=False) if f.details else None,
            created_at=now_iso(),
        ))

    write_audit(session, imported_by, "ingest.file", "raw_dataset", raw.id, {
        "file_name": src.name,
        "file_hash": file_hash,
        "detected_type": decided_type,
        "findings": len(findings),
    })

    final = batch.status
    if not defer_finalize:
        final = finalize_batch(session, batch.id, actor=imported_by)

    return ImportResult(
        batch_id=batch.id,
        raw_dataset_id=raw.id,
        duplicate=False,
        status=final,
        detected_type=decided_type or "unknown",
        confidence=detection.confidence,
        attribution_window_days=detection.attribution_window_days,
        row_count=len(table),
        data_start_date=start_date,
        data_end_date=end_date,
        findings=findings,
        superseded=sup.details.get("superseded", []) if sup else [],
    )


def finalize_batch(session: Session, batch_id: str, actor: str = "cli") -> str:
    """批次收口：QF-06 批级口径 + 全量 findings → 状态机终态。

    多文件批次由最后一个文件或调用方显式触发（identified → quality_running → 终态）。
    """
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise ValueError(f"批次不存在：{batch_id}")
    if batch.status not in ("identified", "quality_running"):
        raise ValueError(f"批次 {batch_id} 状态 {batch.status}，无需 finalize")

    stored = session.scalars(
        select(QualityFinding).where(QualityFinding.batch_id == batch_id)
    ).all()
    all_findings = [
        Finding(f.check_code, f.severity, f.message, f.raw_dataset_id, {})
        for f in stored
    ]
    for f in check_batch_caliber(session, batch_id):
        all_findings.append(f)
        session.add(QualityFinding(
            batch_id=batch_id,
            raw_dataset_id=f.raw_dataset_id,
            check_code=f.check_code,
            severity=f.severity,
            message=f.message,
            details_json=json.dumps(f.details, ensure_ascii=False) if f.details else None,
            created_at=now_iso(),
        ))

    if batch.status == "identified":
        batch.status = transition(batch.status, "quality_running")
    final = decide_status(all_findings)
    batch.status = transition(batch.status, final)
    batch.summary_json = json.dumps(
        {
            "counts": {
                s: sum(1 for f in all_findings if f.severity == s)
                for s in ("error", "warn", "info")
            },
            "by_code": sorted({f.check_code for f in all_findings}),
        },
        ensure_ascii=False,
    )
    if final == "failed":
        for raw in session.scalars(
            select(RawDataset).where(RawDataset.batch_id == batch_id)
        ).all():
            if raw.status == "identified":
                raw.status = "rejected"

    write_audit(session, actor, "batch.finalize", "import_batch", batch_id,
                {"status": final, "findings": len(all_findings)})
    return final
