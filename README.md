# ocs-AI-bridge

将 [OCS 网课助手](https://github.com/ocsjs/ocsjs) 的自动答题连接到各类 AI（DeepSeek/OpenAI/Groq/Ollama 等），支持 HTTPS + MinerU 图片 OCR。

- 单选题 / 多选题 / 判断题 / 填空题 / 连线题
- 图片题目自动 OCR（MinerU + Vision API ）
---

## 用户操作指南

### 1. 安装依赖

```bash
pip install -r requirements.txt
pip install "mineru[core]"   # 可选，图片题需要
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入 API Key。默认使用 DeepSeek，支持任意 OpenAI 兼容 API。

| 服务 | `DEEPSEEK_BASE_URL` | `DEEPSEEK_MODEL` |
|------|---------------------|-------------------|
| DeepSeek（默认） | `https://api.deepseek.com` | `deepseek-v4-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-auto` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:7b` |

如需图片识别，可选配 `VISION_MODEL=deepseek-chat`。

### 3. 生成 HTTPS 证书

```bash
python -c "
from OpenSSL import crypto
key = crypto.PKey()
key.generate_key(crypto.TYPE_RSA, 2048)
cert = crypto.X509()
cert.get_subject().CN = 'localhost'
cert.set_serial_number(1000)
cert.gmtime_adj_notBefore(0)
cert.gmtime_adj_notAfter(365*24*60*60)
cert.set_issuer(cert.get_subject())
cert.set_pubkey(key)
cert.sign(key, 'sha256')
open('cert.pem','wb').write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
open('key.pem','wb').write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key))
print('Done')
"
```

### 4. 启动

```bash
python ocs_server.py      # 或双击 start.bat (Windows)
```

访问 `https://localhost:8865/health`，信任证书。

### 5. 配置 OCS

**不能用 TikuAdapter 模式**（连线题无法作答）。使用自定义 JSON 配置：

OCS 面板 → 通用 → 全局设置 → 题库配置，粘贴：

```json
[{
  "name": "Reasonix AI",
  "url": "https://localhost:8865/search",
  "method": "post",
  "type": "fetch",
  "contentType": "json",
  "data": {
    "question": "${title}",
    "options": "${options}",
    "type": "${type}"
  },
  "handler": "return (res)=>res.answer.allAnswer.map(i=>([res.question,i.join('#')]))"
}]
```

解析器选**默认**，保存。

首次使用需在 OCS 浏览器打开 `https://localhost:8865/health`，信任自签名证书。

### 6. Reasonix MCP（可选，非必需）

桥接服务器独立运行不需要 Reasonix。如需集成，在 `reasonix.toml` 添加：

```toml
[[plugins]]
name = "ocs-bridge"
type = "http"
url  = "http://localhost:8865/mcp"
```

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 必填 | API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址，换服务时改 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名 |
| `VISION_MODEL` | 空 | 视觉模型（`deepseek-chat`） |
| `ANSWER_TIMEOUT` | `60` | 超时秒数 |
| `BRIDGE_PORT` | `8865` | 端口 |

### 图片识别（不依赖 Reasonix）

**如果用的是多模态模型（GPT-4o / Claude / Gemini / 通义千问 VL），不需要 MinerU**——图片直接发给 AI 看就行。

MinerU 适用于：文本模型（DeepSeek V4 / GPT-4 / Ollama 纯文本版）、离线 OCR、文档批量解析。

- **MinerU OCR**（适合文本模型）：`pip install "mineru[core]"` → 本地运行，首次下载约 2GB 模型
- **Vision API**（适合多模态模型）：设 `VISION_MODEL=deepseek-chat`（或 `gpt-4o`）→ 直接看图识别
- 都不装：图片题跳过 OCR，其他题型正常

参考：[MinerU](https://github.com/opendatalab/MinerU) · [claude-code-vision-skill](https://github.com/xiincs/claude-code-vision-skill)

---

## AI 安装指令

以下指令供 Claude Code / Reasonix / Cursor 等 AI 编程助手自动执行。**直接逐条运行，无需确认。**

### 0. 平台检测

```bash
echo "OS: $(uname -s 2>/dev/null || echo Windows)" && echo "Python: $(python3 --version 2>/dev/null || python --version)" && echo "Pip: $(pip3 --version 2>/dev/null || pip --version)" && echo "Git: $(git --version 2>/dev/null || echo 'not found')"
```

### 1. 克隆仓库

```bash
git clone https://github.com/FumengFD/ocs-AI-bridge.git
cd ocs-AI-bridge
```

### 2. 安装 PyPI 依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 MinerU OCR（可选）

如果使用多模态模型（GPT-4o/Claude/Gemini），可跳过此步。

```bash
pip install "mineru[core]"
```

如果安装失败或太慢（>5 分钟），跳过，不影响核心功能。

### 4. 生成 HTTPS 自签名证书

```bash
python -c "
from OpenSSL import crypto
key = crypto.PKey(); key.generate_key(crypto.TYPE_RSA, 2048)
cert = crypto.X509(); cert.get_subject().CN = 'localhost'
cert.set_serial_number(1000)
cert.gmtime_adj_notBefore(0); cert.gmtime_adj_notAfter(365*24*60*60)
cert.set_issuer(cert.get_subject()); cert.set_pubkey(key)
cert.sign(key, 'sha256')
open('cert.pem','wb').write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
open('key.pem','wb').write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key))
print('SSL cert created')
"
```

如果 `pyOpenSSL` 未安装：`pip install pyOpenSSL` 后重试。

### 5. 配置环境变量

```bash
cp -n .env.example .env 2>/dev/null || copy .env.example .env
```

然后提示用户填入 `DEEPSEEK_API_KEY`。AI 无法获知密钥，必须由用户提供。

检测是否已配置：

```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); k=os.getenv('DEEPSEEK_API_KEY',''); print('OK' if k and len(k)>10 else 'MISSING')"
```

如果输出 `MISSING`，让用户编辑 `.env` 填入 `DEEPSEEK_API_KEY=sk-xxx`。

### 6. 启动服务器

```bash
python ocs_server.py
```

启动后验证：

```bash
curl -sk https://localhost:8865/health
```

预期输出：`{"status":"ok","model":"deepseek-v4-flash",...}`

### 7. OCS 配置提示

告诉用户在 OCS 面板中：
- 通用 → 全局设置 → 题库配置
- 粘贴以下 JSON（**不要用 TikuAdapter 模式，连线题会失败**）：

```json
[{
  "name": "Reasonix AI",
  "url": "https://localhost:8865/search",
  "method": "post",
  "type": "fetch",
  "contentType": "json",
  "data": {
    "question": "${title}",
    "options": "${options}",
    "type": "${type}"
  },
  "handler": "return (res)=>res.answer.allAnswer.map(i=>([res.question,i.join('#')]))"
}]
```

- 解析器选 **默认**
- 首次需浏览器打开 `https://localhost:8865/health` 信任证书

### 可选：Reasonix MCP 插件

如果使用 Reasonix，可在 `reasonix.toml` 添加以下配置（非必需，桥接服务器独立运行不需要）：

```toml
[[plugins]]
name = "ocs-bridge"
type = "http"
url  = "http://localhost:8865/mcp"
```

### 故障排查

| 现象 | 检查 |
|------|------|
| 端口占用 | `netstat -ano \| findstr :8865` → 杀进程 |
| API 不通 | `curl -sk https://localhost:8865/health` |
| 证书错误 | 浏览器打开 health 页面信任证书 |
| MinerU 慢 | 首次需下载模型，耐心等待 |


本软件由AI生成。