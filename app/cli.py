"""求职尽调助手 · 命令行入口（Claude / DeepSeek 二选一）。

运行（在项目根目录）：
    python -m app.cli 北京金堤科技有限公司              # 默认 DeepSeek，直接尽调
    python -m app.cli                                  # 交互问答
    python -m app.cli --provider claude 小米科技        # 改用 Claude
    python -m app.cli --selftest                       # 自检（不消耗大模型额度）

环境变量：
    TYC_API_KEY        天眼查 API-KEY（必填，.env 已含）
    DEEPSEEK_API_KEY   DeepSeek key（--provider deepseek 时必填）
    ANTHROPIC_API_KEY  Claude key（--provider claude 时必填）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .tyc_client import TycClient, TycError, _load_dotenv
from .diligence import SYSTEM_PROMPT, TOOLS, OPENAI_TOOLS, dispatch

CLAUDE_MODEL = "claude-opus-4-8"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


# ---- 各 provider 的单回合实现 -------------------------------------------------

def _turn_claude(client: Any, tyc: TycClient, messages: list[dict]) -> str:
    while True:
        resp = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT, tools=TOOLS, messages=messages,
        )
        if resp.stop_reason == "refusal":
            return "（请求被安全策略拒绝，请换一种问法。）"
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return "".join(b.text for b in resp.content if b.type == "text")
        results = []
        for tu in tool_uses:
            print(f"  🔧 {tu.name}({json.dumps(tu.input, ensure_ascii=False)})")
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": dispatch(tyc, tu.name, tu.input)})
        messages.append({"role": "user", "content": results})


def _turn_deepseek(client: Any, tyc: TycClient, messages: list[dict]) -> str:
    while True:
        resp = client.chat.completions.create(
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
            return msg.content or ""
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"  🔧 {tc.function.name}({json.dumps(args, ensure_ascii=False)})")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": dispatch(tyc, tc.function.name, args)})


# ---- provider 装配 -----------------------------------------------------------

def _build(provider: str):
    """返回 (client, messages 初值, 单回合函数, 模型名)；缺 key/依赖时抛 SystemExit。"""
    if provider == "claude":
        try:
            import anthropic
        except ImportError:
            raise SystemExit("缺少依赖：请先 pip install anthropic")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("请先设置 ANTHROPIC_API_KEY（Claude key）")
        return anthropic.Anthropic(), [], _turn_claude, CLAUDE_MODEL
    # deepseek
    try:
        import openai
    except ImportError:
        raise SystemExit("缺少依赖：请先 pip install openai")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("请先设置 DEEPSEEK_API_KEY")
    client = openai.OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=DEEPSEEK_BASE_URL)
    return client, [{"role": "system", "content": SYSTEM_PROMPT}], _turn_deepseek, DEEPSEEK_MODEL


def selftest(provider: str) -> int:
    print("== 自检 ==")
    try:
        with TycClient() as tyc:
            tools = tyc.list_tools()
            print(f"✅ 天眼查 MCP 已连接，远端工具 {len(tools)} 个")
            names = {t["name"] for t in tools}
            need = {"search_companies", "get_company_basic_profile", "get_company_people",
                    "get_company_group_profile", "get_company_capabilities", "search_bids"}
            missing = need - names
            print("✅ 关键远端工具齐全" if not missing else f"⚠️ 缺少远端工具：{missing}")
            sample = tyc.search_companies("北京金堤科技有限公司", page_size=2)
            print("✅ search_companies 实测成功" if ("候选" in sample or "企业" in sample) else "⚠️ 返回异常")
    except TycError as e:
        print(f"❌ 天眼查连接失败：{e}")
        return 1
    print(f"✅ 暴露给模型的工具 {len(TOOLS)} 个：" + "、".join(t["name"] for t in TOOLS))
    key_env = "ANTHROPIC_API_KEY" if provider == "claude" else "DEEPSEEK_API_KEY"
    print(f"✅ 已检测到 {key_env}" if os.environ.get(key_env) else f"⚠️ 未设置 {key_env}（运行尽调前需设置）")
    print("自检完成。")
    return 0


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="应届生求职尽调助手（命令行）")
    parser.add_argument("company", nargs="*", help="公司名（留空进入交互模式）")
    parser.add_argument("--provider", choices=["deepseek", "claude"], default="deepseek",
                        help="选择大模型，默认 deepseek")
    parser.add_argument("--selftest", action="store_true", help="自检后退出")
    args = parser.parse_args()

    if args.selftest:
        return selftest(args.provider)

    client, messages, turn, model = _build(args.provider)

    with TycClient() as tyc:
        print(f"🎓 应届生求职尽调助手已就绪（数据来自天眼查，模型 {args.provider}:{model}）")
        print("   输入公司名开始尽调，输入 exit / 退出 结束。\n")
        pending = " ".join(args.company).strip() or None

        while True:
            if pending is None:
                try:
                    user = input("你> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if user.lower() in ("exit", "quit", "退出", "q"):
                    break
                if not user:
                    continue
            else:
                user = pending
                pending = None
                print(f"你> {user}")

            messages.append({"role": "user", "content": user})
            print("\n助手>\n" + turn(client, tyc, messages) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
