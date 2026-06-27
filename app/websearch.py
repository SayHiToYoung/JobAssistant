"""全网口碑搜索（博查 AI 搜索封装，纯标准库）。

用搜索引擎查公司在牛客/脉脉/知乎等平台的公开讨论（在职/前员工分享），
拿到的是搜索引擎已索引的公开网页摘要，不直接爬平台、相对合规。

需要环境变量 BOCHA_API_KEY（博查 https://open.bochaai.com）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"


def search_company_reviews(company_name: str, count: int = 8) -> str:
    """搜索公司全网口碑，返回 Markdown 摘要列表。"""
    key = os.environ.get("BOCHA_API_KEY")
    if not key:
        return "未配置 BOCHA_API_KEY，无法查询全网口碑（可在 .env 填入博查 key 后启用）。"

    query = f"{company_name} 员工评价 口碑 牛客 脉脉 知乎 加班 待遇 晋升"
    body = json.dumps(
        {"query": query, "summary": True, "count": count, "freshness": "noLimit"}
    ).encode("utf-8")
    req = urllib.request.Request(
        BOCHA_ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if e.code == 403:
            return f"博查额度不足或鉴权失败：{detail[:150]}"
        return f"全网口碑搜索失败：HTTP {e.code} {detail[:150]}"
    except Exception as e:  # noqa: BLE001
        return f"全网口碑搜索出错：{e}"

    pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    if not pages:
        return f"未搜到「{company_name}」的公开口碑讨论。"

    lines = [
        f"# 全网口碑搜索：{company_name}",
        "",
        "> 以下为搜索引擎公开网页摘要（牛客/知乎/脉脉等），属网友主观分享，有水军与泄愤，仅供参考、需交叉甄别。",
        "> 每条标题即原帖链接，请在报告中引用这些真实链接（逐字复制 url，勿编造）以便用户点击核实。",
        "",
    ]
    for i, x in enumerate(pages, 1):
        title = (x.get("name") or "").strip()
        url = (x.get("url") or "").strip()
        snippet = (x.get("summary") or x.get("snippet") or "").strip().replace("\n", " ")
        lines.append(f"{i}. [{title}]({url})\n   {snippet[:300]}")
    return "\n".join(lines)
