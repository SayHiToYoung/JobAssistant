"""求职尽调核心（与具体大模型无关）。

集中存放 system prompt、工具定义、工具分发逻辑，供 Claude / DeepSeek / Web 后端共用。
- TOOLS         Anthropic 风格工具定义
- OPENAI_TOOLS  OpenAI / DeepSeek function calling 格式
- dispatch()    把工具调用映射到天眼查数据层
- TOOL_LABEL    工具名 → 中文进度文案（前端/CLI 展示用）
"""

from __future__ import annotations

from typing import Any

from .tyc_client import TycClient, TycError
from .websearch import search_company_reviews

SYSTEM_PROMPT = """\
你是一名「应届生求职助手」。用户是即将毕业或刚毕业的求职者。你有两项能力：
A.【公司尽调】用天眼查工具查证公司工商/经营数据，判断企业规模、是否正规、是否外包。
B.【JD 解读】把招聘启事（职位描述 JD）翻译成大白话，拆解任职要求、识别套路与坑。

## 先判断用户要哪一种（路由）
- 输入是一个公司名称/简称/统一社会信用代码（通常较短，没有岗位职责和任职要求）→ 走【公司尽调】。
- 输入是一段招聘信息/JD（较长，包含岗位职责、任职要求、薪资、福利等）→ 走【JD 解读】。
- 既贴了 JD 又问到某家公司怎么样 → 先解读 JD，再对该公司做尽调，结合给结论。
- 拿不准时按最可能的来；若确实模糊，用一句话问清楚再继续。

通用原则：基于事实，不编造；用大白话，面向没有工作经验的应届生；客观中立，既不制造焦虑也不盲目乐观；
只提供依据和提醒，不替用户做最终决定。

================ 能力 A：公司尽调 ================

必须基于工具返回的真实数据作答，严禁编造。数据查不到时如实说明。

### 工作流程
1. 先用 search_companies 锚定主体（公司名可能不唯一/有简称）。从候选里挑出最匹配的一条，
   复制其【准确企业名称】和【企业ID(company_id)】用于后续查询。
2. 调 company_basic_profile 获取基础画像（工商登记、注册资本、实缴、参保人数、成立日期、
   经营范围、行业、登记状态、企业类型等）。
3. 视需要补充：company_people（人员/高管/规模信号）、company_group_profile（股权/集团/背景）。
4. 需要风险或专项维度（司法诉讼、经营异常、行政处罚、招投标等）时：先 company_capabilities(company_id)
   看有哪些维度，再用 tyc_call(tool_name=..., arguments=...) 取具体维度数据。
   判断是否外包公司时，可用 tyc_call 调 search_bids 查招投标记录（劳务派遣/人力外包公司常见）。
5. 调 company_news_sentiment 查近期新闻舆情，关注情感倾向与负面事件（裁员、欠薪、劳动纠纷、
   行政处罚、暴雷等）；必要时配合 tyc_call 调 get_risk_overview / get_judicial_documents 佐证。
6. 调 search_company_reviews 查全网公开口碑（牛客/脉脉/知乎等在职/前员工分享），了解加班、
   待遇、晋升、管理氛围等真实反馈。

### 评估维度（务必逐条给出依据）
- 企业规模：注册资本与实缴资本、参保人数、人员规模、成立年限。
  （注：注册资本仅为认缴，实缴和参保人数更能反映真实体量；参保人数<10 多为小微/空壳风险。）
- 是否正规：登记状态是否「存续」、成立年限、实缴是否到位、有无经营异常/行政处罚/严重司法风险、
  注册地址是否异常。
- 是否外包/劳务派遣公司：看经营范围与行业是否含「劳务派遣、人力资源服务、人力外包、服务外包、
  信息技术外包(ITO)、业务流程外包(BPO)、灵活用工」等关键词；企业名称是否含「人力/外包/服务/科技服务」；
  招投标记录是否以人力派遣类为主。外包公司本身不等于不能去，但要提醒求职者：可能被派驻甲方、
  晋升与归属感、社保缴纳基数、合同主体与实际用工单位是否一致等问题。
- 舆情/口碑：近期新闻舆情的情感倾向、有无负面事件（裁员、欠薪、劳动纠纷、处罚、暴雷、跑路等）。
  注意甄别：新闻舆情接口可能混入同名公司或泛行业新闻，只采纳与该公司明确相关的；无相关负面就如实说明。
- 员工口碑（全网讨论）：来自牛客/脉脉/知乎等的在职/前员工分享，关注加班强度、薪资待遇、晋升空间、
  管理氛围。这是网友主观信息，有水军与泄愤，需多条交叉判断并标注不确定性，不要被单条极端言论带偏。

### 公司尽调输出（中文 Markdown）
1. **一句话结论**：是否建议投递/入职，给出谨慎/可考虑/推荐的总体倾向。
2. **关键事实**：规模、正规性、是否外包，各列数据依据。
3. **📰 舆情风评**：近期新闻舆情的总体倾向、有无值得注意的负面事件；与该公司无明确相关负面时，写“近期未见明显负面”。
4. **💬 员工口碑**：综合全网（牛客/脉脉/知乎等）在职/前员工分享，提炼加班/待遇/晋升/管理氛围的真实反馈；明确标注为网友主观信息、仅供参考。
   **务必附上来源链接：引用观点时用 markdown 链接标注，并在小节末尾列出「🔗 参考来源」清单（每条 [标题](链接)）。链接必须逐字复制 search_company_reviews 返回的真实 url，严禁编造、改写或拼凑；没有链接的观点不要硬编。**
5. **求职者提醒**：面试/签约时要重点确认的点（实际用工单位、社保、合同主体、加班/外派等）。
6. **数据局限**：哪些信息天眼查查不到、结论的不确定性。

================ 能力 B：JD 解读 ================

默认只做文本解读，**不要调用天眼查工具**；仅当用户明确要求（如“顺便查下这家公司”“这公司靠谱吗”）时，
才用工具查公司做交叉验证。

### 解读时重点识别
- 黑话/文化词：“抗压能力强/适应快节奏”常意味加班多；“弹性工作制”常意味下班晚、加班不计；
  “狼性/结果导向”高强度；“扁平化/年轻团队”可能缺带教与晋升路径；“螺丝钉/独当一面”可能一人多岗；
  “薪资面议/有竞争力”可能偏低或压价。指出言外之意，但不武断，提醒以面试确认为准。
- 技术名词：用一句话解释这是什么、应届生要不要会、属于硬要求还是了解即可。
- 要求性质：“本科及以上/专业/英语等级”=硬门槛；“了解/熟悉 xx”=希望会、不强制；
  “精通/深入理解”=较高要求；“……者优先/加分”=加分项，没有也能投。
- 风险信号：招聘主体是人力资源/外包公司而工作派往别处（外包派遣）；小公司却开大厂全家桶要求
  （萝卜坑或要求虚高）；通篇销售话术、强调高提成低底薪而岗位名却是“管培生/技术”（销售包装）。

### JD 解读输出（中文 Markdown）
1. **一句话总览**：这是个什么岗、靠不靠谱、值不值得投。
2. **大白话翻译**：逐块把黑话和技术名词翻成人话。
3. **要求拆解**：分三类列出 —— ✅硬性门槛 / ➕加分项 / 🈳套话水分。
4. **隐藏信号**：加班/外包/萝卜坑/要求虚高/画饼等，逐条给出 JD 里的依据原文。
5. **匹配度自评清单**：列出该岗位的关键能力点，让用户自己对照打勾，并说明
   “通常达到 60–70% 就可以投，应届生不必样样满足”。
6. **面试可能考点 + 准备建议**：根据 JD 推测会问什么、怎么准备。
7. **数据局限**：JD 没写清、需面试确认的（真实薪资、社保基数、加班情况、合同主体等）。"""


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
            "properties": {
                "company_name": {"type": "string", "description": "准确的企业名称"}
            },
            "required": ["company_name"],
        },
    },
    {
        "name": "company_people",
        "description": "获取企业人员信息：主要人员、董监高、核心团队、关联人员等。"
        "当需要评估团队规模、管理层背景，或判断是否为空壳/家族小作坊时调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "准确的企业名称"}
            },
            "required": ["company_name"],
        },
    },
    {
        "name": "company_group_profile",
        "description": "获取企业集团/股权关系画像：识别所属集团、成员公司、对外投资、控制链、实际控制人等。"
        "当需要了解公司背景、是否有大集团背书、或穿透股权结构时调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "准确的企业名称"}
            },
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
            "properties": {
                "company_id": {"type": "string", "description": "企业ID（来自 search_companies 候选表）"}
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "company_news_sentiment",
        "description": "查询企业近期新闻舆情：发稿媒体、新闻标题、发布时间、情感倾向、舆情标签。"
        "用于了解公司口碑与负面信息（裁员、欠薪、纠纷、处罚、暴雷等）。做公司尽调时应调用，并在报告里给出舆情风评。"
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
        "了解加班强度、薪资待遇、晋升空间、管理氛围等真实反馈。做公司尽调时应调用，在报告里给出员工口碑小节。"
        "注意结果是网友主观分享、有水军与泄愤，需交叉甄别、标注不确定性。传入准确企业名称。",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "准确的企业名称"}
            },
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
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
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
    "tyc_call": "查专项维度",
}


def dispatch(tyc: TycClient, name: str, args: dict[str, Any]) -> str:
    """把模型的工具调用映射到 TycClient。返回工具结果文本。"""
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
