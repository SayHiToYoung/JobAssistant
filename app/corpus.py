"""公众号文章本地语料库（内部爆料底库）。

把某个「员工吐槽/企业爆料」类公众号的文章抓下来存成本地 JSON 库，
用户输入公司名时在库里检索命中的文章，交给大模型汇总成这家公司的舆情。

- 库文件：data/wechat_corpus.json，形如 [{url,title,author,publish_time,text,added_at}, ...]
- 入库：批量给文章链接 → 抓取解析 → 去重后写库
- 检索：按公司名（含简称/全称）在标题+正文里匹配

命令行批量灌库：
    python -m app.corpus add <url1> <url2> ...
    python -m app.corpus list
    python -m app.corpus stats
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from .tyc_client import PROJECT_ROOT
from .wechat import fetch_article, extract_wechat_urls, fetch_album_articles

CORPUS_PATH = PROJECT_ROOT / "data" / "wechat_corpus.json"
ALBUMS_PATH = PROJECT_ROOT / "albums.txt"  # 合集清单（配置、入 git、随部署同步）；refresh 据此定时追新

# 公司名归一化时剥掉的通用后缀，便于「小米科技有限责任公司」和「小米」互相匹配
_SUFFIXES = [
    "股份有限公司", "有限责任公司", "有限公司", "集团股份", "控股集团",
    "集团", "股份", "控股", "公司", "科技", "技术", "网络", "信息",
]


def load() -> list[dict[str, Any]]:
    if not CORPUS_PATH.exists():
        return []
    try:
        return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save(items: list[dict[str, Any]]) -> None:
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert(article: dict[str, Any]) -> bool:
    """把一篇已解析的文章写入库；已存在（同 url）则跳过。返回是否新增。"""
    url = article.get("url", "")
    if not url:
        return False
    items = load()
    if any(it.get("url") == url for it in items):
        return False
    items.append({
        "url": url,
        "title": article.get("title", ""),
        "author": article.get("author", ""),
        "publish_time": article.get("publish_time", ""),
        "text": article.get("text", ""),
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save(items)
    return True


def add_urls(urls: list[str]) -> dict[str, Any]:
    """批量抓取文章链接并入库。返回 {added, skipped, errors:[(url,err)]}。"""
    items = load()
    known = {it.get("url") for it in items}
    added, skipped, errors = 0, 0, []
    for u in urls:
        if u in known:
            skipped += 1
            continue
        art, err = fetch_article(u)
        if err:
            errors.append((u, err))
            continue
        items.append({
            "url": u,
            "title": art.get("title", ""),
            "author": art.get("author", ""),
            "publish_time": art.get("publish_time", ""),
            "text": art.get("text", ""),
            "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        known.add(u)
        added += 1
    if added:
        save(items)
    return {"added": added, "skipped": skipped, "errors": errors}


def _canon_album(url: str) -> str | None:
    """把合集链接归一化成只含 __biz + album_id 的标准形式（去掉 sessionid 等易变参数）。"""
    biz = re.search(r"__biz=([^&#]+)", url)
    aid = re.search(r"album_id=([^&#]+)", url)
    if not (biz and aid):
        return None
    return (f"https://mp.weixin.qq.com/mp/appmsgalbum?__biz={biz.group(1)}"
            f"&action=getalbum&album_id={aid.group(1)}&scene=126")


def remember_album(url: str) -> None:
    """把一个合集链接记进清单（去重），refresh 时会重扒它们追新。"""
    canon = _canon_album(url)
    if not canon:
        return
    known = load_albums()
    if canon not in known:
        ALBUMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ALBUMS_PATH.open("a", encoding="utf-8") as f:
            f.write(canon + "\n")


def load_albums() -> list[str]:
    if not ALBUMS_PATH.exists():
        return []
    return [ln.strip() for ln in ALBUMS_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _keys(name: str) -> set[str]:
    """由公司名生成匹配关键词：全称、剥后缀的核心词、以及长核心词的两字简称。"""
    name = (name or "").strip()
    core = name
    for s in _SUFFIXES:
        core = core.replace(s, "")
    core = core.strip()
    keys = {name, core}
    if len(core) >= 4:      # 「字节跳动」→ 也用「字节」，「阿里巴巴」→「阿里」
        keys.add(core[:2])
    return {k for k in keys if len(k) >= 2}


def search(company: str, limit: int = 6) -> list[dict[str, Any]]:
    """在库里找讲这家公司的文章，按相关度+新鲜度排序返回。"""
    keys = _keys(company)
    if not keys:
        return []
    scored = []
    for it in load():
        title = it.get("title", "")
        text = it.get("text", "")
        in_title = any(k in title for k in keys)
        hits = sum(text.count(k) for k in keys)
        if not in_title and hits == 0:
            continue
        score = (2 if in_title else 0) + min(hits, 10)
        scored.append((score, it.get("publish_time", ""), it))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [it for _, _, it in scored[:limit]]


# 多篇爆料汇总成「某公司舆情」的系统提示
INSIDER_SYSTEM = (
    "你是帮应届生做求职避雷的分析助手。下面是若干篇来自「员工吐槽/企业爆料」类公众号、"
    "都与同一家公司相关的文章正文。请综合它们，输出这家公司的内部舆情：\n"
    "1. 一句话总体印象；\n"
    "2. 反复被提到的问题分条列出（加班强度、薪酬/期权兑现、管理与文化、裁员/画饼等），"
    "每条带上具体细节，并标明大致有几篇文章提到；\n"
    "3. 给应届生的求职提示：面试重点问什么、哪些信号要警惕；\n"
    "4. 客观性提醒：这些是单方爆料/主观分享，可能有情绪或个例，需交叉验证。\n"
    "只依据给你的正文，不要编造。引用某篇观点时用 markdown 链接附上该文 URL（逐字复制）。"
)


def _cli() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "stats"
    if cmd == "add":
        urls: list[str] = []
        for a in args[1:]:
            urls.extend(extract_wechat_urls(a) or ([a] if a.startswith("http") else []))
        if not urls:
            print("用法：python -m app.corpus add <文章链接> [更多链接...]")
            return
        print(f"准备入库 {len(urls)} 篇…")
        r = add_urls(urls)
        print(f"新增 {r['added']}，跳过（已存在）{r['skipped']}，失败 {len(r['errors'])}")
        for u, e in r["errors"]:
            print(f"  × {u}\n    {e}")
    elif cmd == "album":
        if len(args) < 2:
            print("用法：python -m app.corpus album <合集/专辑链接>")
            return
        arts, err = fetch_album_articles(args[1])
        if not arts:
            print(f"扒取失败：{err}")
            return
        print(f"合集共 {len(arts)} 篇，开始抓正文入库…" + (f"（翻页有警告：{err}）" if err else ""))
        r = add_urls([a["url"] for a in arts])
        remember_album(args[1])  # 记住这个合集，refresh 会定时重扒追新
        print(f"新增 {r['added']}，跳过（已存在）{r['skipped']}，失败 {len(r['errors'])}")
        for u, e in r["errors"]:
            print(f"  × {u}\n    {e}")
    elif cmd == "refresh":
        albums = load_albums()
        if not albums:
            print("还没有记住任何合集。先用 python -m app.corpus album <合集链接> 扒一次。")
            return
        total_added = 0
        for al in albums:
            arts, err = fetch_album_articles(al)
            r = add_urls([a["url"] for a in arts])
            total_added += r["added"]
            print(f"合集 {al[-24:]}：{len(arts)} 篇，新增 {r['added']}")
        print(f"刷新完成，本次共新增 {total_added} 篇。库现有 {len(load())} 篇。")
    elif cmd == "list":
        for it in load():
            print(f"[{it.get('publish_time','')[:10] or '??'}] {it.get('title','')}\n    {it.get('url','')}")
    else:  # stats
        items = load()
        print(f"库中共 {len(items)} 篇文章  ({CORPUS_PATH})")


if __name__ == "__main__":
    _cli()
