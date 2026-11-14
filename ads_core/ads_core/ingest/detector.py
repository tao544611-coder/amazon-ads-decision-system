# -*- coding: utf-8 -*-
"""列名指纹识别器（设计 §2）。

detect(headers) → DetectionResult；不确定即停（置信不足或前二分差 < margin →
unknown + ambiguities，等待 --type 人工指定）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml


def normalize_header(h: str) -> str:
    """归一化：去 BOM → strip → 下划线视为空格 → 连续空白折叠 → 小写。

    下划线归一（O-1 增补 2）：本地清洗派生 csv 列名用 snake_case（ad_group 等），
    与指纹的空格形式（ad group）对齐，避免 negative/必要列判定失真。
    """
    return re.sub(r"\s+", " ", h.replace("_", " ").lstrip("\ufeff").strip()).lower()


@dataclass
class DetectionResult:
    detected_type: str
    confidence: float
    matched: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    ambiguities: dict[str, Any] = field(default_factory=dict)
    attribution_window_days: int | None = None
    variant: str | None = None  # D11：命中 report_variants 变体名（None=主类型直判）

    @property
    def is_unknown(self) -> bool:
        return self.detected_type == "unknown"


def load_fingerprints(path: str | None = None) -> dict[str, Any]:
    if path:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    res = resources.files("ads_core.ingest").joinpath("fingerprints.yaml")
    with res.open("r", encoding="utf-8") as f:  # type: ignore[union-attr]
        return yaml.safe_load(f)


_WINDOW_RE = re.compile(r"\b(\d{1,2})\s*day\b")


def infer_attribution_window(headers: list[str]) -> int | None:
    """从列名中的 'N day' 推断归因窗；多窗取最小完整窗出现顺序中的首个度量窗。

    设计 O-3：事实层优先取与成熟度一致的窗；此处仅登记报表主窗（首个命中）。
    """
    seen: list[int] = []
    for h in headers:
        m = _WINDOW_RE.search(normalize_header(h))
        if m:
            n = int(m.group(1))
            if n in (1, 7, 14, 30) and n not in seen:
                seen.append(n)
    for n in seen:
        if n != 1:  # 跳过 '1 day'（点击窗），取转化窗
            return n
    return None


def _group_satisfied(group: dict[str, list[str]], norm_headers: set[str]) -> tuple[bool, list[str]]:
    """组判定：any_of 命中任一；all_of 全命中。返回 (是否满足, 命中列)。"""
    if "any_of" in group:
        hits = [c for c in group["any_of"] if c in norm_headers]
        return bool(hits), hits
    hits = [c for c in group["all_of"] if c in norm_headers]
    return len(hits) == len(group["all_of"]), hits


def detect(headers: list[str], fingerprints: dict[str, Any] | None = None) -> DetectionResult:
    fp = fingerprints or load_fingerprints()
    norm = {normalize_header(h) for h in headers}
    threshold = float(fp.get("confidence_threshold", 0.85))
    margin = float(fp.get("margin", 0.1))

    # D11 候选集：report_types（主类型）+ report_variants（源变体 → maps_to 映射）
    candidates: list[tuple[str, dict[str, Any], str | None]] = [
        (name, spec, None) for name, spec in fp["report_types"].items()
    ]
    for vname, vspec in (fp.get("report_variants") or {}).items():
        candidates.append((vname, vspec, vspec.get("maps_to")))

    scores: list[tuple[str, float, list[str], list[str], str | None, str | None]] = []
    ambiguous_types: dict[str, Any] = {}

    for name, spec, maps_to in candidates:
        negative_hit = [c for grp in spec.get("negative", []) for c in grp if c in norm]
        if negative_hit:
            ambiguous_types[name] = {"reason": "negative_column_present", "columns": negative_hit}
            continue

        matched: list[str] = []
        missing: list[str] = []
        groups_total = len(spec["required"])
        groups_hit = 0
        for group in spec["required"]:
            ok, hits = _group_satisfied(group, norm)
            if ok:
                groups_hit += 1
                matched.extend(hits)
            else:
                missing.append(group.get("any_of") or group.get("all_of") or [])

        required_ratio = groups_hit / groups_total if groups_total else 0.0
        dist_total = len(spec.get("distinguishing", []))
        dist_hit = 0
        for group in spec.get("distinguishing", []):
            ok, hits = _group_satisfied(group, norm)
            if ok:
                dist_hit += 1
                matched.extend(hits)
        # 设计 §2.3 公式实现注记：无区分列配置的类型（placement/purchased_product）
        # 区分项视为满足（dist_ratio=1.0），否则其满分恒 0.8 < 阈值 0.85，永远 unknown。
        dist_ratio = (dist_hit / dist_total) if dist_total else 1.0
        confidence = required_ratio * 0.8 + dist_ratio * 0.2
        scores.append((name, round(confidence, 4), matched, missing, maps_to, name if maps_to else None))

    scores.sort(key=lambda s: s[1], reverse=True)
    window = infer_attribution_window(headers)

    if not scores:
        return DetectionResult(
            detected_type="unknown",
            confidence=0.0,
            ambiguities={"reason": "no_report_type_scored"},
            attribution_window_days=window,
        )

    top1, runner = scores[0], scores[1] if len(scores) > 1 else None
    top1_name, top1_conf, top1_maps_to, top1_variant = top1[0], top1[1], top1[4], top1[5]
    runner_conf = runner[1] if runner else 0.0

    if top1_conf < threshold or (top1_conf - runner_conf) < margin:
        return DetectionResult(
            detected_type="unknown",
            confidence=top1_conf,
            matched=top1[2],
            missing_required=[m for m in top1[3]],
            ambiguities={
                "reason": "low_confidence_or_margin",
                "top_candidates": [
                    {"type": name, "confidence": c} for name, c, _, _, _, _ in scores[:3]
                ],
                "excluded_by_negative": ambiguous_types,
            },
            attribution_window_days=window,
        )

    return DetectionResult(
        detected_type=top1_maps_to or top1_name,
        confidence=top1_conf,
        matched=top1[2],
        missing_required=[],
        ambiguities={},
        attribution_window_days=window,
        variant=top1_variant,
    )
