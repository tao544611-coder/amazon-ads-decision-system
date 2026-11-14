# -*- coding: utf-8 -*-
"""CLI 入口（typer · 设计 §6 · 全部命令支持 --json，Q1 唯一接入面）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from sqlalchemy import select

from . import config
from .canonical import build_canonical
from .events import add_business_event, add_keyword_rank
from .facts import build_facts
from .ingest import ingest_file
from .ingest.detector import DetectionResult, detect
from .ingest.reader import read_table
from .methodology import latest_released, methodology_import
from .profile import map_campaign, update_profile, upsert_product
from .quality import check_batch_caliber, check_file_level, check_supersede, decide_status
from .storage import (
    AuditLog,
    ImportBatch,
    MethodologyVersion,
    QualityFinding,
    RawDataset,
    reset_engine,
    session_scope,
)
from .storage.models import now_iso

app = typer.Typer(add_completion=False, help="亚马逊广告智能优化与决策系统 · 阶段一")
methodology_app = typer.Typer(help="方法论版本管理")
batch_app = typer.Typer(help="导入批次")
quality_app = typer.Typer(help="质检")
canonical_app = typer.Typer(help="canonical 统一层")
facts_app = typer.Typer(help="事实指标")
product_app = typer.Typer(help="产品与档案")
event_app = typer.Typer(help="业务事件")
app.add_typer(methodology_app, name="methodology")
app.add_typer(batch_app, name="batch")
app.add_typer(quality_app, name="quality")
app.add_typer(canonical_app, name="canonical")
app.add_typer(facts_app, name="facts")
app.add_typer(product_app, name="product")
app.add_typer(event_app, name="event")

JSON_OPT = Annotated[bool, typer.Option("--json", help="以 JSON 输出结果")]


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, default=str))
    else:
        for k, v in data.items():
            typer.echo(f"{k}: {json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v}")


@app.callback()
def _main() -> None:
    reset_engine()  # 每次调用重解析 ADS_HOME（测试安全）


# ---------------------------------------------------------------- init


@app.command("init")
def init(json_out: JSON_OPT = False) -> None:
    """建库+迁移+目录（data/raw, data/db）。"""
    config.ensure_dirs()
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    command.upgrade(cfg, "head")
    _emit({"status": "ok", "db": str(config.db_path()), "raw_dir": str(config.raw_dir())}, json_out)


# ---------------------------------------------------------------- methodology


@methodology_app.command("import")
def methodology_import_cmd(
    yaml_path: str,
    json_out: JSON_OPT = False,
) -> None:
    """导入方法论版本（校验 status=released、hash 落库）。"""
    with session_scope() as s:
        result = methodology_import(s, yaml_path)
        s.commit()
    _emit({
        "version_id": result.version_id,
        "version_no": result.version_no,
        "rule_count": result.rule_count,
        "duplicate": result.duplicate,
    }, json_out)


@methodology_app.command("show")
def methodology_show(
    latest: bool = False,
    json_out: JSON_OPT = False,
) -> None:
    with session_scope() as s:
        if latest:
            version = latest_released(s)
            versions = [version] if version else []
        else:
            versions = list(
                s.scalars(
                    select(MethodologyVersion).order_by(MethodologyVersion.created_at.desc())
                ).all()
            )
        payload = [
            {
                "id": v.id, "version_no": v.version_no, "status": v.status,
                "title": v.title, "file_hash": v.file_hash, "approved_by": v.approved_by,
            }
            for v in versions
        ]
    _emit({"versions": payload, "count": len(payload)}, json_out)


# ---------------------------------------------------------------- ingest


@app.command("ingest")
def ingest(
    files: list[str],
    type: Optional[str] = typer.Option(None, "--type", help="人工指定报表类型（识别不确定时兜底）"),
    ad_product_type: str = typer.Option("sp", "--ad-product-type", help="sp|sb|sd"),
    source_system: str = typer.Option("amazon_console", "--source-system"),
    source_type: str = typer.Option("manual_upload", "--source-type"),
    by: str = typer.Option("cli", "--by", help="导入人"),
    json_out: JSON_OPT = False,
) -> None:
    """导入报表文件（幂等：同 hash 返回既有批次）。多文件共享一个批次，末文件收口。"""
    results = []
    shared_batch: str | None = None
    with session_scope() as s:
        for i, f in enumerate(files):
            defer = i < len(files) - 1  # 非最后一个文件延迟终态判定
            r = ingest_file(
                s, f,
                imported_by=by, source_type=source_type, source_system=source_system,
                forced_type=type, ad_product_type=ad_product_type,
                batch_id=shared_batch, defer_finalize=defer,
            )
            shared_batch = shared_batch or r.batch_id
            results.append({
                "file": Path(f).name, "batch_id": r.batch_id,
                "raw_dataset_id": r.raw_dataset_id, "duplicate": r.duplicate,
                "detected_type": r.detected_type,
                "confidence": r.confidence,
                "attribution_window_days": r.attribution_window_days,
                "rows": r.row_count,
                "data_start_date": r.data_start_date, "data_end_date": r.data_end_date,
                "superseded": r.superseded,
                "findings": [
                    {"code": x.check_code, "severity": x.severity, "message": x.message}
                    for x in r.findings
                ],
            })
        s.commit()
        batch_status = s.get(ImportBatch, shared_batch).status if shared_batch else None
    _emit({"batch_id": shared_batch, "batch_status": batch_status, "files": results}, json_out)


# ---------------------------------------------------------------- batch


@batch_app.command("list")
def batch_list(
    status: Optional[str] = typer.Option(None, "--status"),
    json_out: JSON_OPT = False,
) -> None:
    with session_scope() as s:
        stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc())
        if status:
            stmt = stmt.where(ImportBatch.status == status)
        rows = [
            {
                "id": b.id, "status": b.status, "imported_at": b.imported_at,
                "imported_by": b.imported_by, "source_system": b.source_system,
                "summary": b.summary_json,
            }
            for b in s.scalars(stmt).all()
        ]
    _emit({"batches": rows, "count": len(rows)}, json_out)


@batch_app.command("show")
def batch_show(batch_id: str, json_out: JSON_OPT = False) -> None:
    with session_scope() as s:
        b = s.get(ImportBatch, batch_id)
        if b is None:
            _emit({"error": f"批次不存在：{batch_id}"}, json_out)
            raise typer.Exit(1)
        raws = s.scalars(select(RawDataset).where(RawDataset.batch_id == batch_id)).all()
        findings = s.scalars(select(QualityFinding).where(QualityFinding.batch_id == batch_id)).all()
        payload = {
            "id": b.id, "status": b.status, "imported_at": b.imported_at,
            "imported_by": b.imported_by, "source_type": b.source_type,
            "source_system": b.source_system, "summary": b.summary_json,
            "raw_datasets": [
                {
                    "id": r.id, "file_name": r.file_name, "file_hash": r.file_hash,
                    "report_type": r.report_type, "ad_product_type": r.ad_product_type,
                    "status": r.status, "superseded_by": r.superseded_by,
                    "rows": r.row_count, "data_start_date": r.data_start_date,
                    "data_end_date": r.data_end_date, "data_as_of": r.data_as_of,
                    "attribution_window_days": r.attribution_window_days,
                }
                for r in raws
            ],
            "findings": [
                {"code": f.check_code, "severity": f.severity, "message": f.message}
                for f in findings
            ],
        }
    _emit(payload, json_out)


@batch_app.command("confirm")
def batch_confirm(
    batch_id: str,
    as_: str = typer.Option("complete", "--as", help="complete|partial"),
    by: str = typer.Option("cli", "--by"),
    json_out: JSON_OPT = False,
) -> None:
    """人工确认 needs_confirm 批次 → complete/partial。"""
    if as_ not in ("complete", "partial"):
        _emit({"error": "--as 仅支持 complete|partial"}, json_out)
        raise typer.Exit(1)
    with session_scope() as s:
        b = s.get(ImportBatch, batch_id)
        if b is None:
            _emit({"error": f"批次不存在：{batch_id}"}, json_out)
            raise typer.Exit(1)
        if b.status != "needs_confirm":
            _emit({"error": f"批次状态 {b.status} 非 needs_confirm"}, json_out)
            raise typer.Exit(1)
        from .quality import transition

        b.status = transition(b.status, as_)
        from .storage.audit import write_audit

        write_audit(s, by, "batch.confirm", "import_batch", batch_id, {"as": as_})
        s.commit()
    _emit({"batch_id": batch_id, "status": as_}, json_out)


# ---------------------------------------------------------------- quality


@quality_app.command("rerun")
def quality_rerun(batch_id: str, by: str = typer.Option("cli", "--by"), json_out: JSON_OPT = False) -> None:
    """重跑批次质检（重新读取已落盘原始文件）。"""
    findings_payload = []
    final = None
    with session_scope() as s:
        b = s.get(ImportBatch, batch_id)
        if b is None:
            _emit({"error": f"批次不存在：{batch_id}"}, json_out)
            raise typer.Exit(1)
        from .quality import transition

        b.status = transition(b.status, "quality_running")
        all_findings = []
        raws = s.scalars(select(RawDataset).where(RawDataset.batch_id == batch_id)).all()
        for raw in raws:
            table = read_table(raw.stored_path)
            det = detect(table.headers)
            if raw.report_type and det.is_unknown:
                # 人工指定过的类型在重跑时保持，避免再次 unknown
                det = DetectionResult(
                    detected_type=raw.report_type, confidence=1.0,
                    matched=det.matched, missing_required=[],
                    ambiguities={"carried_from_raw": True},
                    attribution_window_days=det.attribution_window_days,
                )
            fs = check_file_level(raw, table, det)
            sup = check_supersede(session=s, raw=raw)
            if sup:
                fs.append(sup)
            all_findings.extend(fs)
        all_findings.extend(check_batch_caliber(s, batch_id))
        final = decide_status(all_findings)
        b.status = transition(b.status, final)
        for f in all_findings:
            s.add(QualityFinding(
                batch_id=batch_id, raw_dataset_id=f.raw_dataset_id,
                check_code=f.check_code, severity=f.severity, message=f.message,
                details_json=json.dumps(f.details, ensure_ascii=False) if f.details else None,
                created_at=now_iso(),
            ))
            findings_payload.append({"code": f.check_code, "severity": f.severity, "message": f.message})
        s.commit()
    _emit({"batch_id": batch_id, "status": final, "findings": findings_payload}, json_out)


# ---------------------------------------------------------------- canonical


@canonical_app.command("build")
def canonical_build(batch_id: str, by: str = typer.Option("cli", "--by"), json_out: JSON_OPT = False) -> None:
    try:
        with session_scope() as s:
            result = build_canonical(s, batch_id, actor=by)
            s.commit()
        _emit({
            "batch_id": batch_id,
            "rows_written": result.rows_written,
            "skipped_raw": result.skipped_raw,
            "findings": [
                {"code": f.check_code, "severity": f.severity, "message": f.message}
                for f in result.findings
            ],
        }, json_out)
    except ValueError as exc:
        _emit({"error": str(exc)}, json_out)
        raise typer.Exit(1)


# ---------------------------------------------------------------- facts


@facts_app.command("build")
def facts_build(
    batch: Optional[str] = typer.Option(None, "--batch"),
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
    grains: Optional[str] = typer.Option(None, "--grains", help="逗号分隔：campaign,adgroup,target,searchterm,placement,product"),
    mature_only: bool = typer.Option(False, "--mature-only", help="仅写入成熟行（未成熟行跳过并计数）"),
    by: str = typer.Option("cli", "--by"),
    json_out: JSON_OPT = False,
) -> None:
    grain_list = [g.strip() for g in grains.split(",")] if grains else None
    try:
        with session_scope() as s:
            result = build_facts(
                s, batch_id=batch, date_from=date_from, date_to=date_to,
                grains=grain_list, mature_only=mature_only, actor=by,
            )
            s.commit()
        _emit({
            "rows_written": result.rows_written,
            "rows_skipped_existing": result.rows_skipped_existing,
            "rows_skipped_immature": result.rows_skipped_immature,
            "by_grain": result.by_grain,
            "maturity_stat": result.maturity_stat,
            "qf10": result.qf10,
        }, json_out)
    except Exception as exc:
        _emit({"error": str(exc)}, json_out)
        raise typer.Exit(1)


@facts_app.command("maturity")
def facts_maturity(
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
    json_out: JSON_OPT = False,
) -> None:
    """未成熟占比统计（QF-10 查询视图）。"""
    with session_scope() as s:
        from .storage.models import MetricDaily

        stmt = select(MetricDaily)
        if date_from:
            stmt = stmt.where(MetricDaily.date >= date_from)
        if date_to:
            stmt = stmt.where(MetricDaily.date <= date_to)
        rows = s.scalars(stmt).all()
    total = len(rows)
    immature = sum(1 for r in rows if r.maturity == "immature_observation")
    by_type: dict[str, dict] = {}
    for r in rows:
        bucket = by_type.setdefault(r.ad_product_type, {"total": 0, "immature": 0})
        bucket["total"] += 1
        if r.maturity == "immature_observation":
            bucket["immature"] += 1
    _emit({
        "total": total,
        "immature": immature,
        "immature_ratio": round(immature / total, 4) if total else None,
        "by_ad_product_type": by_type,
    }, json_out)


# ---------------------------------------------------------------- product / profile


@product_app.command("add")
def product_add(
    sku: str,
    asin: Optional[str] = typer.Option(None, "--asin"),
    title: Optional[str] = typer.Option(None, "--title"),
    by: str = typer.Option("cli", "--by"),
    json_out: JSON_OPT = False,
) -> None:
    with session_scope() as s:
        r = upsert_product(s, sku=sku, asin=asin, title=title, actor=by)
        s.commit()
    _emit({"product_id": r.product_id, "sku": r.sku, "created": r.created}, json_out)


@product_app.command("profile-update")
def profile_update(
    sku: str,
    file: str = typer.Option(..., "--file", help="payload YAML（A_basic..F_special 结构）"),
    reason: str = typer.Option(..., "--reason"),
    by: str = typer.Option(..., "--by"),
    summary: Optional[str] = typer.Option(None, "--summary"),
    json_out: JSON_OPT = False,
) -> None:
    import yaml as _yaml

    from .storage.models import Product as _Product

    with open(file, encoding="utf-8") as f:
        payload = _yaml.safe_load(f)
    with session_scope() as s:
        product = s.scalars(select(_Product).where(_Product.sku == sku)).first()
        if product is None:
            _emit({"error": f"SKU 不存在：{sku}（先 ads product add）"}, json_out)
            raise typer.Exit(1)
        v = update_profile(s, product.id, payload, reason=reason, changed_by=by, change_summary=summary)
        s.commit()
    _emit({"version_id": v.id, "product_id": v.product_id, "version_no": v.version_no, "diff": v.diff_json}, json_out)


@product_app.command("map-campaign")
def product_map_campaign(
    sku: str,
    campaign_id: str,
    by: str = typer.Option(..., "--by"),
    campaign_name: Optional[str] = typer.Option(None, "--name"),
    ad_product_type: Optional[str] = typer.Option(None, "--ad-product-type"),
    json_out: JSON_OPT = False,
) -> None:
    from .storage.models import Product as _Product

    with session_scope() as s:
        product = s.scalars(select(_Product).where(_Product.sku == sku)).first()
        if product is None:
            _emit({"error": f"SKU 不存在：{sku}"}, json_out)
            raise typer.Exit(1)
        entry = map_campaign(
            s, campaign_id=campaign_id, product_id=product.id, mapped_by=by,
            campaign_name=campaign_name, ad_product_type=ad_product_type,
        )
        s.commit()
    _emit({"campaign_map_id": entry.id, "campaign_id": campaign_id, "product_id": product.id}, json_out)


# ---------------------------------------------------------------- events


@event_app.command("add")
def event_add(
    type: str = typer.Option(..., "--type"),
    sku: Optional[str] = typer.Option(None, "--sku"),
    asin: Optional[str] = typer.Option(None, "--asin"),
    from_: str = typer.Option(..., "--from"),
    to: Optional[str] = typer.Option(None, "--to"),
    desc: str = typer.Option(..., "--desc"),
    by: str = typer.Option(..., "--by"),
    json_out: JSON_OPT = False,
) -> None:
    product_id = None
    with session_scope() as s:
        if sku:
            from .storage.models import Product as _Product

            product = s.scalars(select(_Product).where(_Product.sku == sku)).first()
            if product is None:
                _emit({"error": f"SKU 不存在：{sku}"}, json_out)
                raise typer.Exit(1)
            product_id = product.id
        e = add_business_event(
            s, event_type=type, starts_at=from_, description=desc, recorded_by=by,
            product_id=product_id, asin=asin, ends_at=to,
        )
        s.commit()
    _emit({"event_id": e.id, "event_type": type, "starts_at": from_}, json_out)


@event_app.command("rank-add")
def rank_add(
    sku: str = typer.Option(..., "--sku"),
    kw: str = typer.Option(..., "--kw"),
    on: str = typer.Option(..., "--on", help="快照日 YYYY-MM-DD"),
    rank: Optional[int] = typer.Option(None, "--rank"),
    top: Optional[int] = typer.Option(None, "--top"),
    by: str = typer.Option(..., "--by"),
    json_out: JSON_OPT = False,
) -> None:
    from .storage.models import Product as _Product

    with session_scope() as s:
        product = s.scalars(select(_Product).where(_Product.sku == sku)).first()
        if product is None:
            _emit({"error": f"SKU 不存在：{sku}"}, json_out)
            raise typer.Exit(1)
        snap = add_keyword_rank(
            s, product.id, keyword=kw, captured_on=on, recorded_by=by,
            organic_rank=rank, top_level=top, source="manual",
        )
        s.commit()
    _emit({"snapshot_id": snap.id, "keyword": kw, "captured_on": on}, json_out)


# ---------------------------------------------------------------- audit


@app.command("audit")
def audit(
    tail: int = typer.Argument(20),
    json_out: JSON_OPT = False,
) -> None:
    with session_scope() as s:
        rows = s.scalars(
            select(AuditLog).order_by(AuditLog.ts.desc()).limit(tail)
        ).all()
        payload = [
            {"ts": a.ts, "actor": a.actor, "action": a.action,
             "object_type": a.object_type, "object_id": a.object_id}
            for a in reversed(rows)
        ]
    _emit({"entries": payload, "count": len(payload)}, json_out)


if __name__ == "__main__":
    app()
