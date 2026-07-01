# 开发文档 · JobAssistant 求职助手

面向开发者，说明**当前架构**与**后续开发计划**。产品用法见 [README.md](README.md)。

---

## 一、项目定位

帮应届生在投递/入职前，从多个角度看清一家公司和一个岗位。围绕一个统一的手机端聊天界面，提供 5 个可切换的功能板块，数据来自天眼查、联网搜索（博查）和公众号爆料语料库，由大模型（默认 DeepSeek）从求职者视角输出。

## 二、技术栈

| 层 | 选型 |
|---|---|
| 大模型 | DeepSeek（`deepseek-chat`，OpenAI 兼容接口）；兼容 Claude |
| 后端 | FastAPI + Uvicorn，流式 NDJSON 响应 |
| 前端 | 原生 JS 单页（`web/index.html`），marked.js 渲染 markdown，移动优先 |
| 数据源 | 天眼查 AI（MCP）、博查 AI 搜索、微信公众号合集 |
| OCR | RapidOCR（onnxruntime，离线中文识别） |
| 部署 | 阿里云 ECS + systemd + GitHub Actions CI/CD（rsync 同步、自动重启） |

核心依赖尽量用**标准库**（天眼查客户端、公众号抓取、语料库均零第三方依赖），降低部署与维护成本。

## 三、目录结构

```
TianYanCha/
├─ app/
│  ├─ __init__.py
│  ├─ cli.py           命令行入口（尽调/自检，DeepSeek 或 Claude）
│  ├─ server.py        Web 后端（FastAPI）：板块路由 + 流式输出
│  ├─ diligence.py     核心：各板块 system prompt + 工具集 + 分发/进度
│  ├─ tyc_client.py    天眼查 MCP 客户端（数据层，纯标准库）
│  ├─ websearch.py     博查 AI 搜索封装（口碑风评 / 企业动态）
│  ├─ wechat.py        公众号文章抓取解析 + 合集(专辑)翻页扒取
│  ├─ corpus.py        内部爆料语料库：存储/去重/检索 + album/refresh CLI
│  └─ ocr.py           招聘截图 → 文字（RapidOCR，离线）
├─ web/index.html      手机端聊天前端（5 个功能板块）
├─ albums.txt          爆料公众号「合集清单」（配置，入 git，随部署同步）
├─ data/               运行时数据（不入 git、部署不覆盖）
│  └─ wechat_corpus.json   爆料文章语料库（正文缓存）
├─ deploy/jobassistant.service   systemd 服务模板
├─ .github/workflows/deploy.yml  CI/CD：push main → rsync 到 ECS → 重启
├─ .env / .env.example           密钥（.env 不入库）
├─ requirements.txt
├─ README.md          产品说明与用法
└─ DEVELOPMENT.md     本文档
```

## 四、模块职责

- **`server.py`** — 唯一对外服务。`/api/chat` 按 `mode` 分流到不同处理逻辑，统一以 NDJSON 事件流返回（`tool` 进度 / `sources` 来源卡片 / `delta` 逐字 / `reset` / `error` / `done`）。另有 `/api/ocr`、`/api/reset`、`/api/config`，以及静态前端挂载。
- **`diligence.py`** — 板块的「大脑」。为每个 mode 提供 `system_for_mode(mode)`（系统提示）与 `tools_for_mode(mode)`（可用工具子集），`dispatch()` 执行工具调用，`TOOL_LABEL` / `progress_target()` 负责把工具调用翻成前端可读的进度文案。
- **`tyc_client.py`** — 天眼查数据层。关键点：天眼查鉴权头是 `Authorization: <原始 key>`（**不带 Bearer**），通用 MCP 连接器会自动加 `Bearer` 导致失败，所以自建客户端拿数据、再把能力包装成模型的**本地工具（tool use）**。
- **`websearch.py`** — 博查搜索封装，返回结构化网页结果（标题/URL/来源/摘要/日期），供口碑风评与企业动态使用。
- **`wechat.py`** — 公众号能力：`fetch_article(url)` 抓取解析单篇正文；`fetch_album_articles(url)` 通过公开的 `getalbum` JSON 接口自动翻页，扒取一个合集下的全部文章。
- **`corpus.py`** — 爆料语料库：`add_urls` / `upsert` 入库去重，`search(company)` 按公司名（含简称、剥后缀）检索，CLI 提供 `album`（扒合集入库并记入 `albums.txt`）、`refresh`（重扒所有已记合集，增量追新）。

## 五、五大功能板块与请求流

前端输入框上方有 5 个 tab，发送时带上 `mode`，后端据此分流：

| 板块 | mode | 数据源 | 后端流程 |
|---|---|---|---|
| 🏢 公司尽调 | `company` | 天眼查 | agent 工具循环：模型调天眼查工具 → 综合成尽调报告 |
| 📋 JD 解读 | `jd` | 无（纯文本） | 不联网，直接让模型翻译黑话、拆解要求 |
| 💬 口碑风评 | `reviews` | 博查搜索 | agent 循环：搜员工口碑/新闻 → 附来源链接汇总 |
| 📈 企业动态 | `dynamics` | 博查搜索 | `_run_dynamics`：搜财报/政策 → 先发来源卡片 → 流式概述+细节 |
| 🕵️ 内部爆料 | `insider` | 公众号语料库 | `_run_insider`：输公司名→库里检索→汇总舆情+来源卡片；贴文章链接→抓取分析并自动入库 |

**通用流式协议**：所有板块共用同一套 NDJSON 事件，前端 `sendMsg` 统一解析（进度条、来源卡片、逐字渲染、智能滚动），新增板块只要复用这套事件即可，无需改前端渲染逻辑。

## 六、关键设计决策

1. **板块拆分**：早期把尽调/口碑/舆情揉在一份报告里，导致又长又杂。拆成独立 mode 后，每个板块 prompt 专注、输出精炼，用户按需选择。
2. **数据/模型解耦**：数据层（天眼查、博查、公众号）各自独立、可单测；模型层通过工具或上下文消费数据，换模型/换数据源互不影响。
3. **爆料库「配置与数据分离」**：`albums.txt`（合集清单）是配置，入 git 随部署走；`data/wechat_corpus.json`（正文库）是运行时数据，不入 git、部署 rsync 排除，避免每次部署覆盖线上积累。线上首次部署后跑一次 `refresh` 即可据清单重建全库。
4. **合集扒取替代 RSS**：作者文章都归入公众号「合集」，合集会随新文自动更新。因此「定时重扒合集（去重只增）」既能补历史存量、又能追新增量，且只用微信公开接口、不依赖第三方 RSS 服务。

## 七、部署与数据（简述）

- CI/CD：push `main` → GitHub Actions 语法检查 → rsync 同步代码到 ECS → 重启 systemd 服务。`.env` 与 `data/` 被 rsync 排除，不受部署影响。
- 语料库维护：`python -m app.corpus album <合集链接>` 新增合集；`python -m app.corpus refresh` 增量刷新（可挂 crontab 每日自动追新）。
- 上线注意：设 `APP_ACCESS_CODE` 防止公开链接被陌生人刷（天眼查/DeepSeek 均按量计费）。

---

## 八、后续开发计划

按优先级/投入排列，标注状态。

### 近期（打磨现有功能）

- [ ] **爆料检索精度**：当前为子串匹配，命中偏宽（如「高德」文章因正文提及「滴滴」被带出）。改进方向：标题命中加权、只在标题命中时视为主相关、引入公司别名/消歧词典。
- [ ] **语料库存储升级**：`wechat_corpus.json` 平铺存全文，篇数增多后加载/检索变慢。迁移到 SQLite + 全文索引（FTS5），检索更快、支持关键词高亮。
- [ ] **定时追新工程化**：把 crontab 升级为 systemd timer；`refresh` 失败时记录/告警，避免静默失效。
- [ ] **来源卡片体验**：企业动态/内部爆料的来源卡片补充发布时间排序、去重、失效链接标记。

### 中期（能力融合）

- [ ] **统一「公司舆情画像」**：把 4 个来源（天眼查新闻、博查口碑、博查动态、公众号爆料）在一次查询里聚合，给出多源交叉的综合结论与「可信度/一致性」提示，而非各板块割裂。
- [ ] **多爆料源**：`albums.txt` 支持多个公众号 / 分类合集，检索时标注来源号，覆盖更多城市与行业。
- [ ] **JD × 公司交叉验证**：JD 解读识别出公司后，自动拉尽调/爆料做「岗位描述 vs 真实情况」比对。
- [ ] **天眼查更多维度**：司法风险、被执行、股权穿透、在招岗位等，丰富尽调深度。

### 长期（平台化）

- [ ] **会话与结果持久化**：会话历史从单实例内存迁到 SQLite/Redis；板块结果做缓存，重复查询直接命中。
- [ ] **访问控制**：单一访问码 → 简单多用户 + 按用户的调用量限额，控制 API 成本。
- [ ] **前端增强**：历史记录、收藏、报告分享链接、板块结果对比。
- [ ] **可观测性**：结构化日志、错误上报、各 API（天眼查/DeepSeek/博查）用量与耗时监控。
- [ ] **Prompt 回归测试**：为各板块建小型评测集，改 prompt 时跑一遍防止效果回退（已多次因改 prompt 影响输出质量）。

### 稳定性与合规（持续）

- [ ] 公众号抓取的反爬容错：遇「环境异常」验证页时退避重试、限流，降低失败率。
- [ ] 域名 HTTPS 与备案，手机端可「添加到主屏幕」当独立 App。
- [ ] 明确数据用途边界：仅供个人求职参考，爆料内容为单方主观分享、需交叉验证，不作商业数据服务。
