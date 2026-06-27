"""博查 AI 搜索封装（纯标准库）。

用搜索引擎查公司在牛客/脉脉/知乎等的公开员工口碑，以及公司的财报/政策/对外形象。
拿到的是搜索引擎已索引的公开网页摘要，不直接爬平台、相对合规。

需要环境变量 BOCHA_API_KEY（博查 https://open.bochaai.com）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"


def bocha_search(query: str, count: int = 8) -> tuple[list[dict[str, Any]], str]:
    """通用博查搜索。返回 (结果列表, 错误信息)；成功时错误为空串。

    结果项字段：name(标题)、url、siteName(来源名)、siteIcon(logo)、
    snippet、summary、datePublished。
    """
    key = os.environ.get("BOCHA_API_KEY")
    if not key:
        return [], "未配置 BOCHA_API_KEY（可在 .env 填入博查 key 后启用）。"

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
            return [], f"博查额度不足或鉴权失败：{detail[:150]}"
        return [], f"博查搜索失败：HTTP {e.code} {detail[:150]}"
    except Exception as e:  # noqa: BLE001
        return [], f"博查搜索出错：{e}"

    pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    return pages, ""


def _format_pages(pages: list[dict[str, Any]]) -> str:
    lines = []
    for i, x in enumerate(pages, 1):
        title = (x.get("name") or "").strip()
        url = (x.get("url") or "").strip()
        site = (x.get("siteName") or "").strip()
        date = (x.get("datePublished") or "")[:10]
        snippet = (x.get("summary") or x.get("snippet") or "").strip().replace("\n", " ")
        meta = " · ".join(p for p in (site, date) if p)
        lines.append(f"{i}. [{title}]({url})\n   {meta}\n   {snippet[:320]}")
    return "\n".join(lines)


def search_company_reviews(company_name: str, count: int = 8) -> str:
    """搜索公司全网员工口碑（牛客/脉脉/知乎等），返回 Markdown。"""
    query = f"{company_name} 员工评价 口碑 牛客 脉脉 知乎 加班 待遇 晋升 入职体验 离职"
    pages, err = bocha_search(query, count)
    if err:
        return err
    if not pages:
        return f"未搜到「{company_name}」的公开口碑讨论。"
    head = (
        f"# 全网员工口碑：{company_name}\n\n"
        "> 以下为搜索引擎公开网页摘要（牛客/知乎/脉脉等），属网友主观分享，有水军与泄愤，仅供参考、需交叉甄别。\n"
        "> 每条标题即原帖链接，引用时逐字复制真实 url、勿编造。\n"
    )
    return head + "\n" + _format_pages(pages)


def search_company_dynamics(company_name: str, count: int = 8) -> str:
    """搜索公司近期经营动态与对外形象（财报/政策/战略/新闻），返回 Markdown。"""
    query = f"{company_name} 财报 业绩 营收 融资 战略 政策 ESG 业务 最新动态 新闻"
    pages, err = bocha_search(query, count)
    if err:
        return err
    if not pages:
        return f"未搜到「{company_name}」的近期动态。"
    head = (
        f"# 企业动态搜索：{company_name}\n\n"
        "> 以下为搜索引擎公开网页摘要，关注公司经营动态与对外形象，时效与准确性以原文为准。\n"
        "> 每条标题即原文链接，引用时逐字复制真实 url、勿编造。\n"
    )
    return head + "\n" + _format_pages(pages)
