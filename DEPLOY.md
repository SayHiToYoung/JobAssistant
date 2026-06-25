# 部署到阿里云 ECS（Ubuntu + systemd）

把项目部署成常驻服务，开机自启、崩溃自动重启，手机/电脑通过公网访问。

> 建议 **2G 内存以上**（本地 OCR 模型占内存，1G 可能在识别时内存不足）。

---

## 1. SSH 登录 ECS，安装系统依赖

```bash
apt update
apt install -y python3 python3-venv python3-pip git libgl1 libglib2.0-0
```

> `libgl1`、`libglib2.0-0` 是 OCR（RapidOCR/opencv）所需的系统库，**不装会报 `libGL.so.1` 错误**。
> 若 `libgl1` 提示找不到（老版本 Ubuntu），改用 `libgl1-mesa-glx`。

## 2. 拉取代码

```bash
cd /opt
git clone https://github.com/SayHiToYoung/JobAssistant.git
cd JobAssistant
```

## 3. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

> 会下载 rapidocr/onnxruntime，约几分钟。

## 4. 配置密钥（.env 没有上传仓库，需在服务器创建）

```bash
cp .env.example .env
nano .env     # 或 vim .env
```

填入：

```ini
TYC_API_KEY=你的天眼查key
DEEPSEEK_API_KEY=你的DeepSeek key
APP_ACCESS_CODE=自己设一个访问码    # ⚠️ 公网部署必须设，否则会被陌生人刷额度
```

保存退出（nano 是 Ctrl+O 回车、Ctrl+X）。

## 5. 先手动测试一次

```bash
.venv/bin/python -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

看到 `Uvicorn running on http://0.0.0.0:8000` 就说明能正常启动（此时还访问不了，安全组没放行）。按 `Ctrl+C` 停掉。

## 6. 阿里云安全组放行端口（关键，否则公网访问不了）

阿里云控制台 → 该 ECS 实例 → **安全组** → 配置规则 → **入方向** → 手动添加：

| 端口范围 | 授权对象 | 策略 |
|---|---|---|
| `8000/8000` | `0.0.0.0/0` | 允许 |

## 7. 配置 systemd（开机自启 + 崩溃自动重启）

```bash
cp deploy/jobassistant.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jobassistant
systemctl status jobassistant     # 显示 active (running) 即成功
```

## 8. 访问

浏览器打开 **http://<你的ECS公网IP>:8000**，输入第 4 步设的访问码即可使用。
手机浏览器同样能访问，可「添加到主屏幕」当 App 用。

---

## 日常运维

```bash
journalctl -u jobassistant -f                 # 实时看日志
systemctl restart jobassistant                # 重启
cd /opt/JobAssistant && git pull && systemctl restart jobassistant   # 更新到最新代码
```

## 进阶（可选）：用 80 端口 / 域名 / HTTPS

装 Nginx 反向代理到 `127.0.0.1:8000`，绑定域名并用 Let's Encrypt 配 HTTPS，就能用 `https://你的域名` 访问、不带端口号。需要时告诉我，我给你 Nginx 配置和证书步骤。
