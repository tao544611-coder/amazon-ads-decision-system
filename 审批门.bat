@echo off
chcp 65001 >nul
cd /d D:\AD system
title 广告决策审批门
echo 正在启动审批门（仅本机可访问）...
ads_core\.venv\Scripts\python.exe webgate\app.py
pause
