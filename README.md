# JobAssistant · 求职助手（天眼查 + 大模型）

帮应届生在投递/入职前看清「公司」和「岗位」两件事：

- 🏢 **公司尽调**：输入公司名，借助 [天眼查 AI MCP](https://ai.tianyancha.com/guide.md) 的工商/经营数据，
  由大模型从求职者视角判断 **企业规模、是否正规、是否外包/劳务派遣公司**，并提醒面试签约要确认的点。
- 📋 **JD 解读**：粘贴一段招聘 JD（或**直接发招聘截图**，本地 OCR 自动识别），把黑话和技术名词翻成大白话，
  拆解 **✅硬性门槛 / ➕加分项 / 🈳套话水分**，识别 **加班、外包、萝卜坑、要求虚高、画饼** 等隐藏信号，
  并给出匹配自评清单和面试准备建议。

同一个输入框**自动识别**：短公司名走尽调，长 JD 走解读；「贴 JD 顺便查下这家公司」则两者结合做交叉验证
（JD 解读默认不联网，仅在你要求查公司时才调用天眼查）。

支持 **DeepSeek**（默认）或 **Claude**，提供命令行和**手机端网页**两种用法，可一键 Docker 部署。

## 目录结构

```
TianYanCha/
├─ app/
│  ├─ tyc_client.py   天眼查 MCP 客户端（数据层，纯标准库零依赖）
│  ├─ diligence.py    核心：统一 system prompt（尽调 + JD 解读 + 自动路由）+ 工具 + 分发
│  ├─ cli.py          命令行入口（DeepSeek / Claude）
│  ├─ ocr.py          本地 OCR：招聘截图 → 文字（RapidOCR，离线）
│  └─ server.py       Web 后端（FastAPI）
├─ web/index.html     手机端聊天前端
├─ .env               API Key（已 .gitignore，不入库）
├─ requirements.txt
├─ Dockerfile  .dockerignore
└─ README.md
```

> 数据层与模型层解耦：天眼查 MCP 鉴权头是 `Authorization: <原始key>`（**不带 Bearer**），
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

## 用法二：手机端网页（可部署）

```bash
python -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

- 电脑访问 `http://127.0.0.1:8000`
- 手机在同一 WiFi 下访问 `http://<电脑局域网IP>:8000`（如 `http://192.168.1.5:8000`）

用法：
- **查公司**：输入公司名 → 实时显示查询进度（每步转圈 spinner、完成打勾 ✓）→ 逐字流式输出尽调报告。
- **读 JD**：粘贴一段招聘 JD → 逐字输出大白话翻译、要求拆解、隐藏信号、面试准备。
  也可点欢迎页的「📋 解读一段招聘 JD 示例」一键体验。
- **发截图**：点 📷 选图，或在电脑端**拖拽图片进页面**、截图后 **Ctrl+V 粘贴**。图片以缩略图附件出现在输入框上方
  （后台自动 OCR，识别完角标显示 ✓）；发送时图片识别内容一并交给模型解读 —— 你的消息只显示图片，不堆文字。
  首次识别因加载 OCR 模型稍慢，之后很快。
- **两者结合**：贴 JD 时加一句「顺便查下这家公司」，会先解读 JD 再用天眼查数据交叉验证。

> 网页端回答为**流式逐字输出**；阅读时向上滚动会暂停自动跟随，并出现「↓ 回到底部」按钮，点了才继续跟随。

## 用法三：在自己的代码里调数据层

```python
from app.tyc_client import TycClient
with TycClient() as tyc:
    print(tyc.basic_profile("北京金堤科技有限公司"))   # 工商基础画像
    print(tyc.search_companies("小米"))                # 搜索候选
```

## Docker 部署

```bash
docker build -t tyc-chat .
docker run -p 8000:8000 \
  -e DEEPSEEK_API_KEY=sk-... \
  -e TYC_API_KEY=cac2d077-... \
  -e APP_ACCESS_CODE=你的访问码 \
  tyc-chat
```

部署到任意能跑容器/Python 的平台（自己的服务器、Railway、Render、Fly.io 等），
配 HTTPS 域名后手机即可访问，可「添加到主屏幕」当独立 App 用。

### ⚠️ 上线前务必注意

- **加访问码**：设环境变量 `APP_ACCESS_CODE`，前端会要求输入访问码，防止公开链接被陌生人刷。
  天眼查和 DeepSeek 都按调用量计费，裸奔的公开链接可能产生意外费用。
- **密钥只在后端**：key 仅存于服务端，前端不暴露。**不要把 `.env` 打进镜像或提交仓库**
  （已在 `.gitignore` / `.dockerignore` 排除）。
- **会话存储**：会话历史目前在单实例内存里（个人使用足够）；多实例/重启保留需接 Redis 等。
- **合规**：数据来自天眼查，仅供个人求职参考，勿用于商业批量抓取或对外提供数据服务。

## Web 接口

- `POST /api/chat` `{session_id, message}` → 流式 NDJSON（`tool` 进度 / `answer` 回答 / `error` / `done`）
- `POST /api/reset` `{session_id}` → 清空该会话
- `GET /api/config` → `{requireAccessCode}`（前端探测是否需要访问码）
