# -*- coding: utf-8 -*-
"""样本包登记脚本（登记约定第 1-3 步）。

1. 校验 manifest.json 中每条 SHA-256（reports / assets / related_non_target）；
2. 报表样本 → samples/raw-reports/（保留原文件名）；
3. 方法论资产 → samples/assets/（保留 packaged_path 相对目录结构）。

只读源目录（ads-system-base-samples_20261114），只写 samples/ 登记目录。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
SRC = HOME / "ads-system-base-samples_20261114"
MANIFEST = SRC / "manifest.json"
OUT_REPORTS = HOME / "samples" / "raw-reports"
OUT_ASSETS = HOME / "samples" / "assets"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    results: list[dict] = []

    def verify_and_copy(entry: dict, dest_root: Path, strip_prefix: str) -> None:
        packaged = entry["packaged_path"]
        src_file = SRC / packaged
        expected = entry.get("sha256")
        rec = {"path": packaged, "expected": expected}
        if not src_file.is_file():
            rec["result"] = "MISSING_IN_PACKAGE"
            results.append(rec)
            return
        actual = sha256_of(src_file)
        rec["actual"] = actual
        if expected and actual.lower() != expected.lower():
            rec["result"] = "HASH_MISMATCH"
            results.append(rec)
            return
        rel = packaged
        if strip_prefix and rel.startswith(strip_prefix):
            rel = rel[len(strip_prefix):]
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)
        rec["result"] = "OK"
        rec["registered_to"] = str(dest.relative_to(HOME))
        results.append(rec)

    for entry in manifest.get("reports", []):
        verify_and_copy(entry, OUT_REPORTS, "reports/")

    for entry in manifest.get("assets", []):
        verify_and_copy(entry, OUT_ASSETS, "assets/")

    for entry in manifest.get("related_non_target", []):
        verify_and_copy(entry, OUT_ASSETS, "assets/")

    ok = sum(1 for r in results if r["result"] == "OK")
    bad = [r for r in results if r["result"] != "OK"]
    print(json.dumps({
        "total": len(results),
        "ok": ok,
        "bad": len(bad),
        "bad_detail": bad,
    }, ensure_ascii=False, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
