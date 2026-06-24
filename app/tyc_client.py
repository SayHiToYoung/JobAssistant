"""天眼查 AI · MCP 客户端封装（数据层）。

通过 MCP（Streamable HTTP / JSON-RPC 2.0）接入天眼查企业信息网关。
纯标准库实现，无第三方依赖。

文档：https://ai.tianyancha.com/guide.md
端点：https://mcp.tianyancha.com/v1
认证：HTTP 头 Authorization: <API-KEY>（原始 key，不带 Bearer）
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_ENDPOINT = "https://mcp.tianyancha.com/v1"
PROTOCOL_VERSION = "2024-11-05"

# 项目根目录（app/ 的上一级），.env 在这里
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: str = ".env") -> None:
    """把项目根目录 .env 里的键值加载进环境变量（不覆盖已存在的）。"""
    p = PROJECT_ROOT / path
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if not val:  # 空占位行（如 DEEPSEEK_API_KEY=）跳过，不污染环境变量
            continue
        os.environ.setdefault(key, val)


class TycError(RuntimeError):
    """天眼查 MCP 调用错误。"""


class TycClient:
    """天眼查 MCP 客户端。

    用法：
        with TycClient() as tyc:
            print(tyc.basic_profile("北京金堤科技有限公司"))
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: int = 60,
    ) -> None:
        _load_dotenv()
        self.api_key = api_key or os.environ.get("TYC_API_KEY")
        if not self.api_key:
            raise TycError(
                "缺少 API Key：请设置环境变量 TYC_API_KEY，或在 .env 写入 "
                "TYC_API_KEY=xxx，或显式传入 TycClient(api_key=...)。"
            )
        self.endpoint = endpoint
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._next_id = 0

    # ---- 底层 JSON-RPC ----------------------------------------------------

    def _rpc(self, method: str, params: Optional[dict] = None, *, notify: bool = False) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._next_id += 1
            body["id"] = self._next_id
        if params is not None:
            body["params"] = params

        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:  # noqa: PERF203
            raise TycError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
        except urllib.error.URLError as e:
            raise TycError(f"网络错误：{e.reason}") from e

        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid

        raw = resp.read().decode("utf-8")
        if notify:
            return None

        payload = self._parse(raw)
        if "error" in payload:
            err = payload["error"]
            raise TycError(f"MCP 错误 {err.get('code')}: {err.get('message')}")
        return payload.get("result")

    @staticmethod
    def _parse(raw: str) -> dict:
        """同时兼容纯 JSON 与 SSE（event/data 多行）两种响应。"""
        text = raw.lstrip()
        if text.startswith("{"):
            return json.loads(text)
        # SSE：取最后一条带 data 的 JSON
        result: Optional[dict] = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        result = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
        if result is None:
            raise TycError(f"无法解析响应：{raw[:200]}")
        return result

    # ---- 会话管理 ---------------------------------------------------------

    def connect(self) -> "TycClient":
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tyc-python-client", "version": "1.0.0"},
            },
        )
        self._rpc("notifications/initialized", notify=True)
        return self

    def __enter__(self) -> "TycClient":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        return None

    # ---- 通用能力 ---------------------------------------------------------

    def list_tools(self) -> list[dict]:
        """列出全部可用工具（含 name / description / inputSchema）。"""
        return self._rpc("tools/list").get("tools", [])

    def call(self, name: str, **arguments: Any) -> str:
        """调用任意 MCP 工具，返回聚合后的文本（通常是 Markdown）。"""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return self._extract_text(result)

    @staticmethod
    def _extract_text(result: Any) -> str:
        if isinstance(result, dict):
            parts = result.get("content")
            if isinstance(parts, list):
                texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                joined = "\n".join(t for t in texts if t)
                if joined:
                    return joined
        return json.dumps(result, ensure_ascii=False, indent=2)

    # ---- 常用业务快捷方法 -------------------------------------------------

    def search_companies(self, query: str, page: int = 1, page_size: int = 20) -> str:
        """按名称/简称/统一社会信用代码等关键词搜索企业候选列表。"""
        return self.call("search_companies", query=query, page=page, page_size=page_size)

    def basic_profile(self, company_name: str) -> str:
        """企业基础画像：工商登记、联系方式、标签、规模、历史变更等。"""
        return self.call("get_company_basic_profile", company_name=company_name)

    def people(self, company_name: str) -> str:
        """企业人员：主要人员、董监高、核心团队、关联人员、私募高管等。"""
        return self.call("get_company_people", company_name=company_name)

    def group_profile(self, company_name: str) -> str:
        """集团/股权关系画像（识别集团并穿透成员、董监高投资、间接投资）。"""
        return self.call("get_company_group_profile", company_name=company_name)

    def capabilities(self, company_id: str) -> str:
        """返回该企业可进一步查询的维度清单（配合 call 调用具体维度）。"""
        return self.call("get_company_capabilities", company_id=company_id)
