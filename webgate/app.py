# -*- coding: utf-8 -*-
"""审批门本地 Web 服务（一期 · Gordon 2026-11-14 批准方案）。

纯展示层（D1 合规）：不直连数据库，全部读写经 ads CLI 子进程 --json。
仅监听 127.0.0.1:8765，无外部暴露；写操作继承引擎全部守卫（T6/幂等/状态机）。

启动：python webgate/app.py（或 审批门.bat）→ 自动开浏览器。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

BASE = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).resolve().parent / "page.html"
ADS_EXE = BASE / "ads_core" / ".venv" / "Scripts" / "ads.exe"
HOST, PORT = "127.0.0.1", 8765
APPROVER = os.environ.get("WEBGATE_APPROVER", "Gordon")
DECISIONS = {"approve": "approve", "modify": "modify", "reject": "reject", "defer": "defer"}


class CliError(Exception):
    """CLI 调用失败（引擎拒绝/结构错误），消息透传页面。"""


def _parse_cli_out(stdout: str, returncode: int) -> dict:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise CliError(f"CLI 无输出（exit={returncode}）")
    try:
        data = json.loads(lines[-1])
    except ValueError as exc:
        raise CliError(f"CLI 输出非 JSON：{lines[-1][:200]}") from exc
    if returncode != 0 or (isinstance(data, dict) and data.get("error")):
        msg = data.get("error") if isinstance(data, dict) else stdout[-300:]
        raise CliError(str(msg))
    return data


def cli(*args: str, timeout: int = 90) -> dict:
    """调用 ads CLI（--json）并解析输出；错误统一抛 CliError。"""
    cmd = [str(ADS_EXE)] if ADS_EXE.is_file() else [sys.executable, "-m", "ads_core.cli"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [*cmd, *args, "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, cwd=str(BASE), env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"CLI 超时（>{timeout}s）：{' '.join(args[:3])}") from exc
    return _parse_cli_out(proc.stdout or "", proc.returncode)


async def index(request):
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


async def api_proposals(request):
    try:
        data = cli("proposal", "list")
    except CliError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return JSONResponse(data)


async def api_proposal_detail(request):
    pid = request.path_params["proposal_id"]
    try:
        data = cli("proposal", "show", pid)
    except CliError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return JSONResponse(data)


async def api_decide(request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    decision = body.get("decision")
    if decision not in DECISIONS:
        return JSONResponse({"error": f"非法决策：{decision}"}, status_code=400)
    pid = str(body.get("proposal_id", ""))
    if not pid:
        return JSONResponse({"error": "缺 proposal_id"}, status_code=400)
    comment = (body.get("comment") or "").strip()
    if decision in ("reject", "defer") and not comment:
        return JSONResponse({"error": "驳回/暂缓必须填写备注（裁决要有理由）"}, status_code=400)

    args = ["proposal", "approve", pid, "--decision", decision, "--by", APPROVER,
            "--actor", "webgate"]
    tmp_path = None
    try:
        if decision == "modify":
            modified = body.get("modified")
            if not isinstance(modified, dict):
                return JSONResponse({"error": "修改批准缺修改稿"}, status_code=400)
            fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="gate_modify_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(modified, f, ensure_ascii=False)
            args += ["--file", tmp_path]
        if comment:
            args += ["--comment", comment]
        data = cli(*args)
    except CliError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return JSONResponse(data)


async def api_action(request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    pid = str(body.get("proposal_id", ""))
    if not pid:
        return JSONResponse({"error": "缺 proposal_id"}, status_code=400)
    try:
        data = cli("action", "create", pid, "--mode", "manual",
                   "--executor", "lingxing-pending", "--by", APPROVER, "--actor", "webgate")
    except CliError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return JSONResponse(data)


routes = [
    Route("/", index),
    Route("/api/proposals", api_proposals),
    Route("/api/proposal/{proposal_id}", api_proposal_detail),
    Route("/api/decide", api_decide, methods=["POST"]),
    Route("/api/action", api_action, methods=["POST"]),
]
app = Starlette(routes=routes)


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sk.bind((HOST, port))
            return False
        except OSError:
            return True


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    if _port_busy(PORT):
        print(f"端口 {PORT} 已被占用——若审批门已在运行，直接打开 {url}")
        webbrowser.open(url)
        return
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"审批门服务启动：{url}（仅本机可访问，Ctrl+C 停止）")
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
