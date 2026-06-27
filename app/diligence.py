"""求职尽调核心（与具体大模型无关）。

4 个功能板块，各有独立 prompt 与工具子集（Web 端按板块分流，命令行用综合模式）：
- company   公司尽调：工商核心（规模/正规/外包）
- jd        JD 解读：招聘要求翻译拆解（不调工具）
- reviews   口碑风评：新闻舆情 + 全网员工口碑
- dynamics  企业动态：财报/政策/对外形象（联网搜索）

导出：
- TOOLS / OPENAI_TOOLS   工具定义
- MODE_PROMPT / MODE_TOOLS + system_for_mode() / tools_for_mode()
- dispatch() / TOOL_LABEL / progress_target()
- SYSTEM_PROMPT          命令行综合模式（自动判断意图）
"""

from __future__ import annotations

from typing import Any

from .tyc_client import TycClient, TycError
from .websearch import search_company_reviews, search_company_dynamics


# ---- 命令行综合模式（自动判断意图）------------------------------------------

SYSTEM_PROMPT = """\
你是「应届生求职助手」。根据用户输入自动判断意图并处理：
- 公司名 → 公司尽调（工商工具，判断规模/是否正规/是否外包）
- 一段招聘 JD → JD 解读（翻译黑话、拆解要求，不调工具）
- 问口碑/评价/加班 → 口碑风评（company_news_sentiment + search_company_reviews）
- 问财报/动态/发展/政策/对外形象 → 企业动态（search_company_dynamics）
基于工具真实数据，不编造；大白话、简洁，面向没有工作经验的应届生；
引用网络来源时附真实链接、逐字复制 url、勿编造。"""


# ---- 各板块 prompt（Web 端按 mode 分流）-------------------------------------

COMPANY_PROMPT = """\
你是「应届生求职助手」的【公司尽调】模块。用户给一个公司名，你用天眼查工具查工商/经营数据，
从求职者视角判断企业规模、是否正规、是否外包。基于工具真实数据，不编造；大白话、简洁，别长篇大论。

## 工作流程
1. search_companies 锚定主体，复制准确企业名称与 company_id。
2. company_basic_profile 取基础画像（注册资本、实缴、参保人数、成立日期、经营范围、行业、登记状态、企业类型、注册地址）。
3. 视需要 company_people（规模/团队）、company_group_profile（背景/股权）。
4. 需查风险（经营异常/处罚/诉讼/招投标）时，company_capabilities 看维度，再 tyc_call 取明细。

## 评估（逐条给依据）
- 规模：注册资本/实缴、参保人数、人员规模、成立年限（实缴与参保比认缴更真实；参保<10 多为小微/空壳）。
- 是否正规：登记状态、实缴到位、有无经营异常/处罚/严重司法风险、注册地址异常。
- 是否外包：经营范围/行业/名称是否含劳务派遣/人力资源/人力外包/服务外包/ITO/BPO 等；招投标是否人力派遣为主。

## 输出（中文 Markdown，精简）
1. **一句话结论**：建议投递/谨慎/推荐。
2. **关键事实**：规模、正规性、是否外包，各列简洁数据依据（要点或小表，别堆砌）。
3. **提醒**：面试/签约要确认的点（用工单位、社保、合同主体、加班/外派等）。
4. 末尾一行导流：「想看口碑风评或企业动态，点上方对应功能。」

只做尽调，不要查舆情/口碑/动态（那是其他功能）。控制篇幅，先给求职者最该知道的。"""


JD_PROMPT = """\
你是「应届生求职助手」的【JD 解读】模块。用户贴一段招聘 JD（或截图 OCR 文字），你翻译成大白话、
拆解要求、识别套路。默认不调用任何工具。

## 重点识别
- 黑话：抗压/快节奏=加班多；弹性工作制=下班晚/加班不计；狼性/结果导向=高强度；扁平化/年轻团队=可能缺带教与晋升；
  螺丝钉/独当一面=一人多岗；薪资面议/有竞争力=可能偏低或压价。指出言外之意但不武断，提醒以面试确认为准。
- 技术名词：一句话解释是什么、要不要会、硬要求还是了解即可。
- 要求性质：本科/专业/英语=硬门槛；了解/熟悉=希望会不强制；精通/深入=较高要求；者优先/加分=没有也能投。
- 风险：招聘主体是人力/外包派往别处；小公司开大厂全家桶；销售话术包装成管培/技术。

## 输出（中文 Markdown）
1. **一句话总览**：什么岗、靠不靠谱、值不值得投。
2. **大白话翻译**：逐块翻译黑话与技术名词。
3. **要求拆解**：✅硬性门槛 / ➕加分项 / 🈳套话水分。
4. **隐藏信号**：加班/外包/萝卜坑/要求虚高/画饼，逐条给 JD 依据原文。
5. **匹配度自评清单**：列关键能力点供对照，说明“达到 60–70% 就可投，不必样样满足”。
6. **面试可能考点 + 准备建议**。
7. **数据局限**：JD 没写清、需面试确认的（真实薪资、社保基数、加班、合同主体等）。"""


REVIEWS_PROMPT = """\
你是「应届生求职助手」的【口碑风评】模块。用户给一个公司名，你查这家公司的口碑与风评，
帮判断“值不值得来这里工作”。

## 工作流程
1. search_companies 锚定主体（拿准确企业名称）。
2. company_news_sentiment 查新闻舆情（情感倾向、负面事件：裁员/欠薪/纠纷/处罚/暴雷），只采纳与该公司明确相关的。
3. search_company_reviews 查全网员工口碑（牛客/知乎/职Q：加班/待遇/晋升/管理）。

## 输出（中文 Markdown）
1. **一句话口碑结论**：总体偏正面/中性/负面，值不值得去。
2. **📰 舆情风评**：近期新闻舆情倾向、有无负面（无则“近期未见明显负面”）。
3. **💬 员工口碑**：分 👍正面 / 👎负面，提炼加班/待遇/晋升/管理。**务必附真实来源链接**：引用观点用 markdown 链接，
   末尾列「🔗 参考来源」（每条 [标题](链接)）；链接逐字复制 search_company_reviews 返回的真实 url，严禁编造。
4. **提醒**：口碑为网友主观、有水军泄愤，仅供参考、需面试核实。"""


DYNAMICS_PROMPT = """\
你是「应届生求职助手」的【企业动态】模块。系统已联网搜索到该公司的资料，你据此总结这家公司近期的
经营动态与对外形象，帮判断“发展势头和前景如何”——侧重经营/发展/品牌，区别于口碑的员工视角。

## 输出（中文 Markdown；不要再列“参考来源”清单，来源卡片已单独展示）
1. **📝 概述**：用一段话综述该公司近期动态与对外形象（发展势头、在做什么、行业地位）。
2. **🔑 关键细节**：分点列出，每条尽量带具体数字/事实（营收、增长率、融资额、排放量、PUE 等），
   并用 markdown 链接把该条依据的来源附在句末 [来源](url)（逐字复制给定资料里的真实 url，严禁编造）。

只基于给定资料，资料没有的不要编；时效与准确性以原文为准。"""


# ---- 工具定义（Anthropic 风格）-----------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_companies",
        "description": "按公司名称/简称/统一社会信用代码搜索企业候选列表，用于锚定主体。"
        "当用户给出公司名、或主体可能不唯一、或你需要拿到准确企业名称和 company_id 时调用。"
        "返回候选表，含企业名称、统一社会信用代码、法定代表人、注册资本、成立日期、企业ID 等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "公司名称、简称或统一社会信用代码"},
                "page_size": {"type": "number", "description": "返回候选数量，默认 5"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "company_basic_profile",
        "description": "获取企业基础画像：工商登记、注册资本、实缴资本、参保人数、成立日期、"
        "经营范围、行业分类、登记状态、企业类型、注册地址、联系方式等。"
        "判断企业规模、是否正规、是否外包时优先调用。传入从 search_companies 复制的准确企业名称。",
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string", "description": "准确的企业名称"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "company_people",
        "description": "获取企业人员信息：主要人员、董监高、核心团队、关联人员等。"
        "当需要评估团队规模、管理层背景，或判断是否为空壳/家族小作坊时调用。",
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string", "description": "准确的企业名称"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "company_group_profile",
        "description": "获取企业集团/股权关系画像：识别所属集团、成员公司、对外投资、控制链、实际控制人等。"
        "当需要了解公司背景、是否有大集团背书、或穿透股权结构时调用。",
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string", "description": "准确的企业名称"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "company_capabilities",
        "description": "传入 company_id，返回该企业可进一步查询的维度清单（如司法风险、经营异常、"
        "行政处罚、招投标、知识产权等）。当你需要某个专项维度但不确定有没有数据时，先调用它，"
        "再用 tyc_call 取具体维度。company_id 从 search_companies 候选表的企业ID列复制。",
        "input_schema": {
            "type": "object",
            "properties": {"company_id": {"type": "string", "description": "企业ID（来自 search_companies 候选表）"}},
            "required": ["company_id"],
        },
    },
    {
        "name": "company_news_sentiment",
        "description": "查询企业近期新闻舆情：发稿媒体、新闻标题、发布时间、情感倾向、舆情标签。"
        "用于了解公司舆情与负面信息（裁员、欠薪、纠纷、处罚、暴雷等）。"
        "注意返回结果可能混入同名或泛行业新闻，需甄别只采纳与该公司明确相关的。传入准确企业名称。",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "准确的企业名称"},
                "page_size": {"type": "number", "description": "返回新闻条数，默认 15"},
            },
            "required": ["company_name"],
        },
    },
    {
        "name": "search_company_reviews",
        "description": "查全网公开口碑：用搜索引擎搜该公司在牛客/脉脉/知乎等平台的在职/前员工讨论，"
        "了解加班强度、薪资待遇、晋升空间、管理氛围等真实反馈。"
        "注意结果是网友主观分享、有水军与泄愤，需交叉甄别、标注不确定性。传入准确企业名称。",
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string", "description": "准确的企业名称"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "search_company_dynamics",
        "description": "查企业近期经营动态与对外形象：用搜索引擎搜该公司的财报/业绩、营收/融资、"
        "战略与政策（如 ESG、新业务）、重要新闻与品牌形象。用于判断公司发展势头与前景。传入准确企业名称。",
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string", "description": "准确的企业名称"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "tyc_call",
        "description": "通用入口：调用任意天眼查工具获取具体维度明细。"
        "当需要 company_capabilities 列出的专项维度数据（司法诉讼、经营异常、行政处罚、招投标 search_bids、"
        "专利 search_patents、商标 search_trademarks 等）时调用。"
        "arguments 通常需包含 company_name 或 company_id，具体以 capabilities 提示为准。",
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "天眼查工具名，如 search_bids / call_tool"},
                "arguments": {
                    "type": "object",
                    "description": "传给该工具的参数对象，如 {\"query\": \"某公司\"} 或 {\"company_id\": \"...\", \"tool_name\": \"...\"}",
                    "additionalProperties": True,
                },
            },
            "required": ["tool_name", "arguments"],
        },
    },
]


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Anthropic 风格工具定义转成 OpenAI/DeepSeek function calling 格式。"""
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]


OPENAI_TOOLS = to_openai_tools(TOOLS)


# 工具名 → 友好中文进度文案
TOOL_LABEL = {
    "search_companies": "搜索企业候选",
    "company_basic_profile": "查工商基础画像",
    "company_people": "查企业人员",
    "company_group_profile": "查股权/集团关系",
    "company_capabilities": "查可用数据维度",
    "company_news_sentiment": "查新闻舆情",
    "search_company_reviews": "查全网口碑",
    "search_company_dynamics": "搜企业动态",
    "tyc_call": "查专项维度",
}


# ---- 板块 → prompt / 工具子集 ------------------------------------------------

MODE_PROMPT = {
    "company": COMPANY_PROMPT,
    "jd": JD_PROMPT,
    "reviews": REVIEWS_PROMPT,
    "dynamics": DYNAMICS_PROMPT,
}

MODE_TOOLS = {
    "company": ["search_companies", "company_basic_profile", "company_people",
                "company_group_profile", "company_capabilities", "tyc_call"],
    "jd": [],
    "reviews": ["search_companies", "company_news_sentiment", "search_company_reviews"],
    "dynamics": ["search_company_dynamics"],
}


def system_for_mode(mode: str) -> str:
    """按板块返回 system prompt；未知 mode 回退综合模式。"""
    return MODE_PROMPT.get(mode, SYSTEM_PROMPT)


def tools_for_mode(mode: str) -> list[dict[str, Any]]:
    """按板块返回该板块允许的工具子集（OpenAI 格式）；未知 mode 给全部。"""
    names = MODE_TOOLS.get(mode)
    if names is None:
        return OPENAI_TOOLS
    return [t for t in OPENAI_TOOLS if t["function"]["name"] in names]


def dispatch(tyc: TycClient, name: str, args: dict[str, Any]) -> str:
    """把模型的工具调用映射到数据层。返回工具结果文本。"""
    try:
        if name == "search_companies":
            return tyc.search_companies(args["query"], page_size=int(args.get("page_size", 5)))
        if name == "company_basic_profile":
            return tyc.basic_profile(args["company_name"])
        if name == "company_people":
            return tyc.people(args["company_name"])
        if name == "company_group_profile":
            return tyc.group_profile(args["company_name"])
        if name == "company_capabilities":
            return tyc.capabilities(args["company_id"])
        if name == "company_news_sentiment":
            return tyc.call("call_tool", company_name=args["company_name"],
                            tool_name="get_news_sentiment",
                            arguments={"page": 1, "page_size": int(args.get("page_size", 15))})
        if name == "search_company_reviews":
            return search_company_reviews(args["company_name"])
        if name == "search_company_dynamics":
            return search_company_dynamics(args["company_name"])
        if name == "tyc_call":
            return tyc.call(args["tool_name"], **(args.get("arguments") or {}))
        return f"未知工具：{name}"
    except TycError as e:
        return f"天眼查调用失败：{e}"
    except Exception as e:  # noqa: BLE001
        return f"工具执行出错：{e}"


def progress_target(args: dict[str, Any]) -> str:
    """从工具参数里提取一个可展示的查询目标（公司名/ID），用于进度文案。"""
    inner = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
    return (
        args.get("query") or args.get("company_name") or args.get("company_id")
        or inner.get("query") or inner.get("company_name") or inner.get("company_id") or ""
    )
