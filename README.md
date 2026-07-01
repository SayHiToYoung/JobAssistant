# JobAssistant · 求职助手（天眼查 + 联网搜索 + 大模型）

帮应届生在投递/入职前，从多个角度看清一家公司和一个岗位。一个手机端聊天界面，输入框上方切换 **5 个功能板块**，数据来自天眼查、联网搜索（博查）和公众号爆料语料库，由大模型（默认 DeepSeek）从求职者视角输出。

## 五大功能板块

- 🏢 **公司尽调** — 输入公司名，借助 [天眼查 AI MCP](https://ai.tianyancha.com/guide.md) 的工商/经营数据，判断
  **企业规模、是否正规、是否外包/劳务派遣**，并提醒面试签约要确认的点。
- 📋 **JD 解读** — 粘贴招聘 JD（或**直接发招聘截图**，本地 OCR 自动识别），把黑话和技术名词翻成大白话，
  拆解 **✅硬性门槛 / ➕加分项 / 🈳套话水分**，识别 **加班、外包、萝卜坑、要求虚高、画饼** 等隐藏信号。
- 💬 **口碑风评** — 输入公司名，联网搜索全网员工口碑与新闻舆情（牛客/知乎/脉脉等），**附可点击的真实来源链接**。
- 📈 **企业动态** — 输入公司名，联网搜索财报/融资/政策/对外形象，**Perplexity 式来源卡片** + 概述 + 关键细节。
- 🕵️ **内部爆料** — 输入公司名，从「员工吐槽/企业内幕」类公众号的**本地语料库**里检索该公司文章，汇总内部舆情 +
  来源卡片；也可**直接贴公众号文章链接**，即时分析并自动入库。

支持 **DeepSeek**（默认）或 **Claude**，提供命令行和**手机端网页**两种用法。

> 开发者视角的架构说明与开发计划见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 目录结构

```
TianYanCha/
├─ app/
│  ├─ cli.py           命令行入口（尽调/自检）
│  ├─ server.py        Web 后端（FastAPI）：板块路由 + 流式输出
│  ├─ diligence.py     核心：各板块 system prompt + 工具集 + 分发/进度
│  ├─ tyc_client.py    天眼查 MCP 客户端（数据层，纯标准库零依赖）
│  ├─ websearch.py     博查 AI 搜索封装（口碑风评 / 企业动态）
│  ├─ wechat.py        公众号文章抓取解析 + 合集(专辑)翻页扒取
│  ├─ corpus.py        内部爆料语料库：存储/去重/检索 + album/refresh CLI
│  └─ ocr.py           招聘截图 → 文字（RapidOCR，离线）
├─ web/index.html      手机端聊天前端（5 个功能板块）
├─ albums.txt          爆料公众号「合集清单」（配置，入 git）
├─ data/               运行时数据（不入 git、部署不覆盖）
│  └─ wechat_corpus.json   爆料文章语料库
├─ deploy/jobassistant.service   systemd 服务模板
├─ .github/workflows/deploy.yml  CI/CD：push main 自动部署到 ECS
├─ .env                API Key（已 .gitignore，不入库）
├─ requirements.txt
├─ README.md
└─ DEVELOPMENT.md      架构与开发计划
```

> **数据层与模型层解耦**：天眼查 MCP 鉴权头是 `Authorization: <原始key>`（**不带 Bearer**），
> 各家 MCP 连接器会自动加 `Bearer` 前缀导致鉴权失败，因此用 `TycClient` 拿数据、把能力包装成
> 模型的**本地工具（tool use）**，Authorization 头完全可控。

## 准备

```bash
pip install -r requirements.txt
```

`.env`（项目根目录）填写：

```ini
TYC_API_KEY=cac2d077-...        # 天眼查 key
DEEPSEEK_API_KEY=sk-...         # DeepSeek key（默认用这个）
BOCHA_API_KEY=sk-...            # 博查搜索 key（口碑风评 / 企业动态需要）
# ANTHROPIC_API_KEY=sk-ant-...  # 如果改用 Claude
# APP_ACCESS_CODE=你的访问码      # 部署上线时强烈建议设置（见下）
```

## 用法一：命令行尽调

```bash
python -m app.cli --selftest                  # 自检（连天眼查、校验接线，不消耗模型额度）
python -m app.cli 北京金堤科技有限公司           # 直接尽调（默认 DeepSeek）
python -m app.cli                             # 交互问答
python -m app.cli --provider claude 小米科技    # 改用 Claude
```

## 用法二：手机端网页

```bash
python -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

- 电脑访问 `http://127.0.0.1:8000`
- 手机在同一 WiFi 下访问 `http://<电脑局域网IP>:8000`

用法要点：

- **切板块**：输入框上方 5 个 tab，按需选择（公司尽调 / JD 解读 / 口碑风评 / 企业动态 / 内部爆料）。
- **流式输出**：回答逐字推送，工具查询实时显示进度（转圈 spinner / 完成打勾 ✓）；口碑、动态、爆料板块会先给来源卡片。
- **发截图**（JD 板块）：点 📷 选图，或电脑端**拖拽图片进页面**、截图后 **Ctrl+V 粘贴**。图片以缩略图附件出现，
  后台自动 OCR，发送时识别内容一并交给模型 —— 你的消息只显示图片、不堆文字。
- **贴链接**（内部爆料）：直接粘贴公众号文章链接，即时抓取正文分析并入库。

> 阅读时向上滚动会暂停自动跟随，并出现「↓ 回到底部」按钮，点了才继续跟随。

## 用法三：在代码里调数据层

```python
from app.tyc_client import TycClient
with TycClient() as tyc:
    print(tyc.basic_profile("北京金堤科技有限公司"))   # 工商基础画像
    print(tyc.search_companies("小米"))                # 搜索候选
```

## 内部爆料语料库维护

爆料板块的数据是一个本地语料库（`data/wechat_corpus.json`），通过公众号「合集」批量灌入、增量追新：

```bash
python -m app.corpus album <合集链接>    # 扒一个合集的全部文章入库，并记进 albums.txt
python -m app.corpus refresh            # 重扒 albums.txt 里所有合集，去重只加新文
python -m app.corpus stats              # 查看库中篇数
```

- `albums.txt`（合集清单）入 git、随部署同步；`data/`（正文库）是运行时数据，不入 git、部署不覆盖。
- 线上首次部署后跑一次 `refresh` 即可据清单重建全库；挂 crontab 每日 `refresh` 即可自动追新（**替代 RSS**，
  只用微信公开接口、不依赖第三方）。

## 部署上线（阿里云 ECS + systemd + CI/CD）

- 服务以 systemd 常驻（模板见 [deploy/jobassistant.service](deploy/jobassistant.service)）。
- CI/CD：push `main` → GitHub Actions 语法检查 → rsync 同步代码到 ECS → 自动重启服务
  （见 [.github/workflows/deploy.yml](.github/workflows/deploy.yml)）。`.env` 与 `data/` 被 rsync 排除，不受部署影响。
- 配 HTTPS 域名后手机即可访问，可「添加到主屏幕」当独立 App。

### ⚠️ 上线前务必注意

- **加访问码**：设 `APP_ACCESS_CODE`，前端会要求输入访问码，防止公开链接被陌生人刷。
  天眼查、DeepSeek、博查均按调用量计费，裸奔的公开链接可能产生意外费用。
- **密钥只在后端**：key 仅存于服务端 `.env`，前端不暴露，**不要提交仓库**（已 `.gitignore`）。
- **会话存储**：会话历史目前在单实例内存里（个人使用足够）；多实例/重启保留需接 Redis 等。
- **合规**：数据仅供个人求职参考；爆料内容为单方主观分享、需交叉验证，勿用于商业批量抓取或对外数据服务。

## Web 接口

- `POST /api/chat` `{session_id, message, mode}` → 流式 NDJSON 事件：
  `tool`（查询进度）/ `sources`（来源卡片）/ `delta`（逐字）/ `reset` / `error` / `done`
- `POST /api/ocr`（图片上传）→ `{text}`
- `POST /api/reset` `{session_id}` → 清空该会话
- `GET /api/config` → `{requireAccessCode}`（前端探测是否需要访问码）
