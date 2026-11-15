# -*- coding: utf-8 -*-
"""表格读取：xlsx（openpyxl）、csv（utf-8-sig，R7）与 json（领星 MCP 响应，D11 二期）。"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook


@dataclass
class TableData:
    headers: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)


def read_table(path: str | Path) -> TableData:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return _read_csv(p)
    if p.suffix.lower() in {".xlsx", ".xlsm"}:
        return _read_xlsx(p)
    if p.suffix.lower() == ".json":
        return _read_json(p)
    raise ValueError(f"不支持的文件类型：{p.suffix}（支持 xlsx/csv/json）")


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _read_csv(p: Path) -> TableData:
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return TableData()
    headers = [c.lstrip("\ufeff").strip() for c in rows[0]]
    data = TableData(headers=headers)
    for raw in rows[1:]:
        row = {headers[i]: _cell_str(raw[i]) if i < len(raw) else "" for i in range(len(headers))}
        data.rows.append(row)
    return data


def _read_xlsx(p: Path) -> TableData:
    # 完整模式（非 read_only）：广告后台/WPS 缓存 xlsx 的 dimension 元数据常损坏，
    # read_only 模式会按坏维度截断（实测误判 1x1，仅读到标题行）——采集报告备注 5。
    wb = load_workbook(p, read_only=False, data_only=True)
    try:
        ws = wb.active
        matrix = [[_cell_str(c) for c in row] for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    while matrix and not any(matrix[0]):
        matrix.pop(0)  # 去掉前导空行
    if not matrix:
        return TableData()
    headers = [c.lstrip("\ufeff").strip() for c in matrix[0]]
    data = TableData(headers=headers)
    for raw in matrix[1:]:
        if not any(raw):
            continue
        row = {headers[i]: raw[i] if i < len(raw) else "" for i in range(len(headers))}
        data.rows.append(row)
    return data


def _find_json_rows(obj: object) -> list[dict]:
    """在 JSON 结构中定位行列表（D11 二期）。

    支持三种形态（宁缺勿猜，找不到行列表返回空）：
    - 领星 MCP 原始响应：``{"code":1,"data":{"data":[...],"recordsFiltered":N},...}``
      （兼容 ``data.list`` / ``data.records``）；
    - 存档形态：``{"provenance":...,"rows":[...]}``（顶层 ``rows`` / ``records``）；
    - 顶层即行数组。
    """
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        candidates = [obj]
        inner = obj.get("data")
        if isinstance(inner, dict):
            candidates.append(inner)
        for holder in candidates:
            for key in ("rows", "data", "list", "records"):
                v = holder.get(key)
                if isinstance(v, list) and all(isinstance(r, dict) for r in v):
                    return v
    return []


def _read_json(p: Path) -> TableData:
    """JSON 行式数据读取：行列表 → TableData（键为列名，值字符串化，None → 空串）。"""
    with p.open("r", encoding="utf-8") as f:
        try:
            obj = json.load(f)
        except ValueError as exc:
            raise ValueError(f"JSON 无法解析：{p.name}（{exc}）") from exc
    rows_native = _find_json_rows(obj)
    if not rows_native:
        return TableData()
    headers: list[str] = []
    for r in rows_native:
        for k in r.keys():
            if k not in headers:
                headers.append(k)
    data = TableData(headers=headers)
    for r in rows_native:
        data.rows.append({h: _cell_str(r.get(h)) for h in headers})
    return data


_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y",
    "%Y.%m.%d", "%d.%m.%Y",
    "%b %d, %Y",  # 'Dec 06, 2025'（广告后台区间汇总 CSV 实测）
    "%d %b %Y", "%B %d, %Y",
)


def parse_date(value: str) -> date | None:
    """解析报表日期列常见格式；无法解析返回 None（QF-03 捕获）。"""
    v = value.strip()
    if not v:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def parse_number(value: str) -> float | None:
    """解析数值列；空→None（不计 0）；非法→raise ValueError（QF-03 捕获）。"""
    v = (value or "").strip().replace(",", "").replace("£", "").replace("%", "")
    if v in {"", "-", "--", "N/A", "n/a"}:
        return None
    return float(v)
