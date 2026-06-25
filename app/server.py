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
    from fastapi import FastAPI, Request, Header, HTTPException, UploadFile, File
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
    """单回合：流式产出工具进度事件与逐字回答（NDJSON 行）。"""
    messages = SESSIONS.get(session_id)
    if messages is None:
        if len(SESSIONS) >= MAX_SESSIONS:
            SESSIONS.clear()  # 简单防膨胀
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": message})

    try:
        with TycClient() as tyc:
            while True:
                stream = _ds.chat.completions.create(
                    model=DEEPSEEK_MODEL, messages=messages, tools=OPENAI_TOOLS,
                    max_tokens=8192, stream=True,
                )
                content_parts: list[str] = []
                tool_acc: dict[int, dict[str, str]] = {}
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        content_parts.append(delta.content)
                        yield _emit({"type": "delta", "content": delta.content})  # 逐字推送
                    for tc in (delta.tool_calls or []):
                        slot = tool_acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments

                assistant: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
                if tool_acc:
                    assistant["tool_calls"] = [
                        {"id": s["id"] or f"call_{i}", "type": "function",
                         "function": {"name": s["name"], "arguments": s["args"]}}
                        for i, s in enumerate(v for _, v in sorted(tool_acc.items()))
                    ]
                messages.append(assistant)

                if not tool_acc:
                    SESSIONS[session_id] = messages
                    yield _emit({"type": "done", "session_id": session_id})
                    return

                yield _emit({"type": "reset"})  # 清掉工具轮可能的过场文字，准备显示进度
                for call in assistant["tool_calls"]:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    label = TOOL_LABEL.get(name, name)
                    yield _emit({"type": "tool", "label": label, "target": progress_target(args)})
                    out = dispatch(tyc, name, args)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
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


@app.post("/api/ocr")
async def ocr(file: UploadFile = File(...), x_access_code: str | None = Header(default=None)):
    """上传招聘截图 → 返回识别出的文字（供前端回填输入框）。"""
    if ACCESS_CODE and x_access_code != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="访问码错误")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片太大（限 8MB）")
    try:
        from .ocr import image_to_text
        text = image_to_text(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"识别失败：{e}")
    return {"text": text}


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
