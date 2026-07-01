"""微信公众号文章抓取与解析（纯标准库）。

公众号文章页 `https://mp.weixin.qq.com/s/...` 本身是公开可读的，
GET 即可拿到 HTML，不需要登录。这里只做「给定文章 URL → 抽出正文纯文本」，
不做任何越权爬取（不搜号、不批量拉历史，链接由用户手动提供）。

偶发会遇到微信的「环境异常/需验证」页或文章已删除，此时返回错误信息，不抛异常。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from typing import Any

# 匹配公众号文章链接（/s/xxx 短链，或带 __biz 参数的长链）
_WECHAT_URL_RE = re.compile(
    r"https?://mp\.weixin\.qq\.com/s[/?][^\s一-鿿）)】\"'<>]+",
    re.IGNORECASE,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 正文里遇到这些块级标签就补一个换行，保留段落感
_BLOCK_TAGS = {"p", "br", "div", "section", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"}


def extract_wechat_urls(text: str) -> list[str]:
    """从一段文字里挑出所有公众号文章链接（去重、保序）。"""
    seen: list[str] = []
    for m in _WECHAT_URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;")
        if u not in seen:
            seen.append(u)
    return seen


class _ContentParser(HTMLParser):
    """只收集 id="js_content" 那个容器内部的可见文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0          # 当前 div/section 嵌套深度
        self._start_depth = -1   # 进入正文容器时的深度（<0 表示尚未进入）
        self._skip = 0           # script/style 屏蔽
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        adict = dict(attrs)
        if tag in ("div", "section"):
            self._depth += 1
            if self._start_depth < 0 and adict.get("id") == "js_content":
                self._start_depth = self._depth
        if self._start_depth < 0:
            return
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._start_depth >= 0 and tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("div", "section"):
            if self._start_depth == self._depth:
                self._start_depth = -2  # 正文容器闭合，之后不再收集
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._start_depth >= 0 and not self._skip:
            self.parts.append(data)


def _clean_text(raw: str) -> str:
    text = unescape(raw)
    text = text.replace("​", "").replace("\xa0", " ")
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:      # 折叠多个空行为一个
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _meta(html: str, prop: str) -> str:
    m = re.search(
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(prop)}["\']',
            html, re.IGNORECASE,
        )
    return unescape(m.group(1)).strip() if m else ""


def _publish_time(html: str) -> str:
    # 静态 HTML 里偶尔带有创建时间（可读串或 Unix 时间戳）
    for pat in (r'var\s+oriCreateTime\s*=\s*["\']([^"\']+)["\']',
                r'var\s+createTime\s*=\s*["\']([^"\']+)["\']',
                r'var\s+ct\s*=\s*["\'](\d{10})["\']',
                r'"publish_time"\s*:\s*"([^"]+)"'):
        m = re.search(pat, html)
        if m:
            v = m.group(1).strip()
            if v.isdigit() and len(v) == 10:  # 秒级时间戳 → 可读日期
                import datetime
                return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M")
            return v
    return ""


def parse_article(html: str) -> dict[str, Any]:
    """把文章 HTML 解析成 {title, author, publish_time, text}。"""
    title = _meta(html, "og:title")
    if not title:
        m = re.search(r'id="activity-name"[^>]*>(.*?)</h1>', html, re.DOTALL)
        title = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
    author = _meta(html, "og:article:author") or _meta(html, "author")
    p = _ContentParser()
    p.feed(html)
    return {
        "title": title,
        "author": author,
        "publish_time": _publish_time(html),
        "text": _clean_text("".join(p.parts)),
    }


def fetch_article(url: str) -> tuple[dict[str, Any], str]:
    """抓取并解析一篇公众号文章。返回 (文章字典, 错误信息)；成功时错误为空串。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {}, f"抓取失败：HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return {}, f"抓取出错：{e}"

    if "js_content" not in html and ("环境异常" in html or "去验证" in html):
        return {}, "微信提示「环境异常」，需要人工验证，稍后重试或换个网络。"
    art = parse_article(html)
    if not art.get("text"):
        if "该内容已被发布者删除" in html or "已删除" in html:
            return {}, "这篇文章已被作者删除。"
        return {}, "没解析到正文（可能是分享页/视频号内容，或页面结构变化）。"
    art["url"] = url
    return art, ""


def fetch_album_articles(album_url: str) -> tuple[list[dict[str, Any]], str]:
    """扒一个公众号「合集/专辑」里的全部文章（自动翻页）。

    返回 ([{title, url, create_time}, ...], 错误信息)；按发布时间正序。
    合集页有公开的 getalbum JSON 接口，用上一页末篇的 msgid/itemidx 翻页。
    """
    biz = re.search(r"__biz=([^&#]+)", album_url)
    aid = re.search(r"album_id=([^&#]+)", album_url)
    if not (biz and aid):
        return [], "不是有效的合集链接（缺 __biz 或 album_id）。"
    biz, aid = biz.group(1), aid.group(1)

    got: dict[str, dict[str, Any]] = {}
    begin_msgid = begin_itemidx = ""
    for _ in range(60):  # 上限保护，每页 40 篇够翻很多
        u = (f"https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz={biz}"
             f"&album_id={aid}&count=40&f=json")
        if begin_msgid:
            u += f"&begin_msgid={begin_msgid}&begin_itemidx={begin_itemidx}"
        req = urllib.request.Request(u, headers={"User-Agent": _UA})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            return list(got.values()), f"翻页出错：{e}"
        resp = data.get("getalbum_resp") or {}
        lst = resp.get("article_list") or []
        if not lst:
            break
        for a in lst:
            url = a.get("url")
            if url:
                got[url] = {"title": a.get("title", ""), "url": url,
                            "create_time": a.get("create_time", "")}
        begin_msgid = lst[-1].get("msgid", "")
        begin_itemidx = lst[-1].get("itemidx", "")
        if str(resp.get("continue_flag")) != "1":
            break
        time.sleep(0.3)  # 礼貌延迟，别把接口打太急

    arts = sorted(got.values(), key=lambda a: int(a.get("create_time") or 0))
    return arts, ""


# 微信文章分析的系统提示（在职员工吐槽 / 加班乱象视角）
ANALYSIS_SYSTEM = (
    "你是帮应届生做求职避雷的分析助手。用户会给你一篇公众号文章的正文，"
    "内容通常是在职/前员工对某公司的吐槽、加班与管理乱象爆料。请你：\n"
    "1. 先一句话点明这篇讲的是哪家公司、什么事；\n"
    "2. 提炼关键槽点（加班强度、薪酬兑现、管理/文化问题、裁员/画饼等），分条列出，带上文中的具体细节或原话；\n"
    "3. 客观性提醒：这是单方爆料/主观分享，可能有情绪或个例，需交叉验证，别当唯一依据。\n"
    "只依据给你的正文，不要编造文中没有的事实。"
)
