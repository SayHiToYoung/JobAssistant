FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

EXPOSE 8000

# 密钥通过环境变量注入（不要把 .env 打进镜像）：
#   DEEPSEEK_API_KEY、TYC_API_KEY，可选 APP_ACCESS_CODE
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
