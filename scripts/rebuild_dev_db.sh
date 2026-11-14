#!/usr/bin/env bash
# 重建开发库并跑通全流程（阶段一验收演示）：
# init → 方法论 → 产品+档案 → campaign 映射 → 样本导入 → canonical → facts → maturity
# 用法：cd ads_core && bash ../scripts/rebuild_dev_db.sh
set -euo pipefail

ADS=".venv/Scripts/ads.exe"
S="../samples/raw-reports"
HOME_DIR=".."

echo "== 0. 重建 =="
rm -rf "$HOME_DIR/data"
$ADS init --json < /dev/null | tail -1

echo "== 1. 方法论 v1.0.0 =="
$ADS methodology import "../methodology/v1.0.0.yaml" --json < /dev/null | tail -1

echo "== 2. 产品与档案 =="
$ADS product add 6978 --asin B0HB4XFV2F --title "6978 电加热饭盒" --by "agent:zcode" --json < /dev/null | tail -1
$ADS product add 6979 --asin B0H8YN7YX7 --title "6979 电加热饭盒" --by "agent:zcode" --json < /dev/null | tail -1
$ADS product profile-update 6978 --file "../profiles/6978_profile_v1.yaml" \
  --reason "v1 初始建档（products.md + W46 facts 2026-11-12 + 行动台账）" --by "agent:zcode" --json < /dev/null | tail -1
$ADS product profile-update 6979 --file "../profiles/6979_profile_v1.yaml" \
  --reason "v1 初始建档（products.md + W46 facts 2026-11-12 + 行动台账）" --by "agent:zcode" --json < /dev/null | tail -1

echo "== 3. campaign 映射（6978_6979 组共用活动，挂 6978 待 Gordon 确认拆分口径）=="
declare -A CMAP=(
  [202499238611642]="GJHIUBKNGT-AJZCore Exact"
  [116990224855579]="GJHIUBKNGT-ACZVehicle Phrase"
  [183064418684129]="GJHIUBKNGT-ACZPortable Phrase"
  [105889771836374]="GJHIUBKNGT-ACZOffice Phrase"
  [210740798701013]="GJHIUBKNGT-ACZDriver Phrase"
  [29437742665971]="GJHIUBKNGT-AZDAuto"
)
for cid in "${!CMAP[@]}"; do
  $ADS product map-campaign 6978 "$cid" --name "${CMAP[$cid]}" --ad-product-type sp \
    --by "agent:zcode(W46facts)" --json < /dev/null | tail -1
done

echo "== 4. 样本导入（5 批）=="
$ADS ingest "$S/20261112_Sponsored_Products_Targeting_report (3).xlsx" \
            "$S/20261112_Sponsored_Products_Search_term_report (3).xlsx" \
            "$S/20261112_Sponsored_Products_Advertised_product_report (3).xlsx" \
            --by Gordon --json < /dev/null | tail -1
$ADS ingest "$S/Sponsored_Products_Campaign_report.csv" --ad-product-type sp --by Gordon --json < /dev/null | tail -1
$ADS ingest "$S/Sponsored_Brands_Campaign_report.xlsx" --ad-product-type sb --by Gordon --json < /dev/null | tail -1
$ADS ingest "$S/Sponsored_Products_Search_term_report (1).xlsx" --by Gordon --json < /dev/null | tail -1
# 领星中文 ×2 + 本地派生 ×1：预期 QF-01 拦截（阶段一不支持源，登记留档）
$ADS ingest "$S/关键词报告(8.22--9.19)-汇总-2101946964824051713.xlsx" \
            "$S/广告位报告(8.22--9.19)-汇总-2101940911973609473.xlsx" \
            "$S/advertised_product_3SKU_明细.csv" \
            --by Gordon --json < /dev/null | tail -1

echo "== 5. canonical（全部 complete 批次）=="
for bid in $($ADS batch list --json < /dev/null | .venv/Scripts/python.exe -c "
import json, sys
for b in json.load(sys.stdin)['batches']:
    if b['status'] in ('complete', 'partial'):
        print(b['id'])
" | tr -d '\r'); do
  $ADS canonical build "$bid" --by Gordon --json < /dev/null | tail -1
done

echo "== 6. 事实指标 =="
$ADS facts build --by Gordon --json < /dev/null | tail -1
$ADS facts maturity --json < /dev/null | tail -1

echo "== 完成 =="
$ADS audit 10 --json < /dev/null | tail -1
