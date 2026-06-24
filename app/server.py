"""求职尽调聊天机器人 · Web 后端（FastAPI，DeepSeek 推理）。

复用 diligence 核心，对外提供：
- POST /api/chat   流式（NDJSON）返回工具调用进度 + 最终回答
- POST /api/reset  清空某会话历史
- GET  /api/config 前端探测是否需要访问码
- 静态前端（web/ 目录，移动优先）

运行（项目根目录）：
    python -m uvicorn app.server:app --host 0.0.0.0 --port 8000

环境变量：
    DEEPSEEK_API_KEY   必填
    TYC_API_KEY        必填（.env 已含）
    APP_ACCESS_CODE    可选；设置后前端需输入访问码才能使用（部署上线强烈建议设置）
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Iterator

from .tyc_client import TycClient, _load_dotenv, PROJECT_ROOT
from .diligence import SYSTEM_PROMPT, OPENAI_TOOLS, TOOL_LABEL, dispatch, progress_target

_load_dotenv()

try:
    from fastapi import FastAPI, Request, Header, HTTPException
    from fastapi.responses import StreamingResponse
    from fastapi.staticfiles import StaticFiles
    import openai
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"缺少依赖：请先 pip install -r requirements.txt（{e}）")

if not os.environ.get("DEEPSEEK_API_KEY"):
    raise SystemExit("未设置 DEEPSEEK_API_KEY，请在 .env 填写或设为环境变量后再启动。")

DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
WEB_DIR = PROJECT_ROOT / "web"

ACCESS_CODE = os.environ.get("APP_ACCESS_CODE") or None
_ds = openai.OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=DEEPSEEK_BASE_URL)

# 会话历史（含工具消息）。单实例内存版，足够个人使用；多实例部署需换 Redis 等外部存储。
SESSIONS: dict[str, list[dict]] = {}
MAX_SESSIONS = 500

app = FastAPI(title="求职尽调助手")


def _emit(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def run_stream(session_id: str, message: str) -> Iterator[str]:
    """单回合：流式产出工具进度事件与最终回答（NDJSON 行）。"""
    messages = SESSIONS.get(session_id)
    if messages is None:
        if len(SESSIONS) >= MAX_SESSIONS:
            SESSIONS.clear()  # 简单防膨胀
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": message})

    try:
        with TycClient() as tyc:
            while True:
                resp = _ds.chat.completions.create(
                    model=DEEPSEEK_MODEL, messages=messages, tools=OPENAI_TOOLS, max_tokens=8192,
                )
                msg = resp.choices[0].message

                assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    assistant["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ]
                messages.append(assistant)

                if not msg.tool_calls:
                    SESSIONS[session_id] = messages
                    yield _emit({"type": "answer", "content": msg.content or ""})
                    yield _emit({"type": "done", "session_id": session_id})
                    return

                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    label = TOOL_LABEL.get(tc.function.name, tc.function.name)
                    yield _emit({"type": "tool", "label": label, "target": progress_target(args)})
                    out = dispatch(tyc, tc.function.name, args)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    except Exception as e:  # noqa: BLE001
        yield _emit({"type": "error", "message": str(e)})


@app.get("/api/config")
async def config() -> dict:
    return {"requireAccessCode": bool(ACCESS_CODE)}


@app.post("/api/reset")
async def reset(req: Request) -> dict:
    body = await req.json()
    SESSIONS.pop(body.get("session_id", ""), None)
    return {"ok": True}


@app.post("/api/chat")
async def chat(req: Request, x_access_code: str | None = Header(default=None)):
    if ACCESS_CODE and x_access_code != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="访问码错误")
    body = await req.json()
    session_id = (body.get("session_id") or uuid.uuid4().hex)
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息为空")
    return StreamingResponse(run_stream(session_id, message), media_type="application/x-ndjson")


# 静态前端兜底挂载在最后（API 路由优先匹配）
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
