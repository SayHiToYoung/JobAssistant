"""应届生求职尽调助手。

分层：
- tyc_client  数据层：天眼查 MCP 客户端
- diligence   尽调核心：system prompt + 工具定义 + 分发（与具体大模型无关）
- cli         命令行入口（Claude / DeepSeek）
- server      Web 后端（FastAPI，配合 web/index.html）
"""
