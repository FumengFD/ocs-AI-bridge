# OCS-Reasonix Bridge
# MCP 服务器 + HTTP API，连接 OCS 自动答题和 Reasonix AI
#
# 运行模式:
#   HTTP-MCP 双模式: python server.py --port 8865
#   MCP stdio 模式:  python server.py --mcp-stdio

import json
import os
import sys
import re
import time
import asyncio
import argparse
from typing import Optional
from collections import OrderedDict

# Windows: 强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError

from mcp.server.fastmcp import FastMCP

# ── 请求日志 ───────────────────────────────────────────────────

log_lock = asyncio.Lock()

def log_request(method: str, path: str, body: str = "", status: int = 200):
    """记录请求日志到 stderr"""
    ts = time.strftime("%H:%M:%S")
    body_preview = body[:200] + "..." if len(body) > 200 else body
    print(f"[{ts}] {method} {path} -> {status} | {body_preview}", file=sys.stderr, flush=True)

# ── 配置 ───────────────────────────────────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8865"))
ANSWER_TIMEOUT = int(os.getenv("ANSWER_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))          # AI 调用最大重试次数
CACHE_SIZE = int(os.getenv("CACHE_SIZE", "500"))           # 本地缓存题目数

# ── 统计 ───────────────────────────────────────────────────────

stats = {
    "total_requests": 0,
    "cache_hits": 0,
    "ai_success": 0,
    "ai_errors": 0,
    "start_time": time.time(),
    "last_error": None,
    "last_error_time": None,
}

# ── 本地答案缓存 ───────────────────────────────────────────────

class AnswerCache:
    """LRU 缓存，相同题目不重复调用 AI"""

    def __init__(self, maxsize: int = 500):
        self._cache: OrderedDict[str, list[str]] = OrderedDict()
        self.maxsize = maxsize

    def _key(self, question: str, qtype: str) -> str:
        return f"{qtype}:{question.strip()}"

    def get(self, question: str, qtype: str) -> Optional[list[str]]:
        key = self._key(question, qtype)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, question: str, qtype: str, answers: list[str]):
        key = self._key(question, qtype)
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            self._cache[key] = answers
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()

    def __len__(self):
        return len(self._cache)


answer_cache = AnswerCache(maxsize=CACHE_SIZE)

# ── AI 客户端 ───────────────────────────────────────────────────

ai_client: Optional[AsyncOpenAI] = None

def get_ai_client() -> AsyncOpenAI:
    global ai_client
    if ai_client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY 环境变量未设置！请在系统环境变量中设置。")
        ai_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=ANSWER_TIMEOUT,
            max_retries=0,  # 我们自己控制重试
        )
    return ai_client

# ── AI 答题逻辑 ─────────────────────────────────────────────────

QUESTION_PROMPT = """你是一个专业的网课答题助手。请根据题目和选项，给出正确答案。

规则：
1. 单选题(single): 只返回一个正确答案的字母编号，如 "A"，不要解释。
2. 多选题(multiple): 返回所有正确答案的字母编号，用字母连接，如 "ABD"，不要解释。
3. 判断题(judgement): 只返回 "正确" 或 "错误"。
4. 填空题(completion): 返回填空内容，多个空用 "##" 分隔，如 "答案1##答案2"。不要加任何前缀。

格式要求：只输出答案本身，不要加任何前缀、解释、或额外文字。"""


def build_user_prompt(question: str, options: list[str], qtype: str) -> str:
    """构建发给 AI 的提示"""
    labels = [chr(65 + i) for i in range(len(options))] if options else []

    if options and qtype in ("single", "multiple"):
        options_text = "\n".join([f"{labels[i]}. {options[i]}" for i in range(len(options))])
        type_hint = {
            "single": "单选题，请只返回一个正确选项的字母。",
            "multiple": "多选题，请返回所有正确选项的字母（连续，如 ABD）。",
        }.get(qtype, "单选题，请只返回一个正确选项的字母。")
        return f"题目：{question}\n\n选项：\n{options_text}\n\n{type_hint}"
    elif qtype == "judgement":
        return f"题目：{question}\n\n这是一道判断题，请只返回'正确'或'错误'。"
    elif qtype == "completion":
        return f"题目：{question}\n\n这是一道填空题，请返回填空内容，多个空用 ## 分隔。"
    else:
        return f"题目：{question}\n\n请直接给出答案。"


async def ai_answer_with_retry(question: str, options: list[str], qtype: str) -> list[str]:
    """调用 DeepSeek API 获取答案，带重试"""
    client = get_ai_client()
    user_prompt = build_user_prompt(question, options, qtype)

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": QUESTION_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,
                    max_tokens=512,
                ),
                timeout=ANSWER_TIMEOUT
            )
            raw_answer = response.choices[0].message.content.strip()
            return parse_ai_answer(raw_answer, qtype, len(options) if options else 0)

        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = (attempt + 1) * 2  # 2s, 4s 退避
                await asyncio.sleep(wait)
            else:
                raise RuntimeError(f"API 连接失败（重试{MAX_RETRIES}次后）: {str(e)}")

        except APIError as e:
            last_error = e
            raise RuntimeError(f"API 错误: {str(e)}")

        except asyncio.TimeoutError:
            last_error = "timeout"
            if attempt < MAX_RETRIES:
                await asyncio.sleep(3)
            else:
                raise RuntimeError(f"AI 答题超时（{ANSWER_TIMEOUT}s，重试{MAX_RETRIES}次后）")

    raise RuntimeError(f"未知错误: {str(last_error)}")


def parse_ai_answer(raw: str, qtype: str, option_count: int) -> list[str]:
    """解析 AI 返回的原始答案"""
    raw = raw.strip()

    if qtype == "single":
        match = re.search(r'[A-Za-z]', raw)
        return [match.group().upper()] if match else [raw]

    elif qtype == "multiple":
        letters = re.findall(r'[A-Za-z]', raw)
        return [''.join(l.upper() for l in letters)] if letters else [raw]

    elif qtype == "judgement":
        if any(w in raw for w in ['正确', '对', '是', 'T', 't', 'True', 'true', '1', '√']):
            return ['正确']
        elif any(w in raw for w in ['错误', '错', '否', 'F', 'f', 'False', 'false', '0', '×', 'X']):
            return ['错误']
        return [raw]

    elif qtype == "completion":
        parts = re.split(r'##|\n', raw)
        return [p.strip() for p in parts if p.strip()]

    return [raw]


async def get_answer(question: str, options: list[str], qtype: str) -> tuple[list[str], bool]:
    """获取答案，优先走缓存，未命中则调 AI。返回 (answers, from_cache)"""
    # 1. 查本地缓存
    cached = answer_cache.get(question, qtype)
    if cached is not None:
        stats["total_requests"] += 1
        stats["cache_hits"] += 1
        return cached, True

    # 2. 调 AI
    stats["total_requests"] += 1
    try:
        answers = await ai_answer_with_retry(question, options, qtype)
        stats["ai_success"] += 1
        # 写入缓存
        answer_cache.set(question, qtype, answers)
        return answers, False
    except Exception as e:
        stats["ai_errors"] += 1
        stats["last_error"] = str(e)
        stats["last_error_time"] = time.time()
        raise


# ── FastMCP 服务器 ─────────────────────────────────────────────

mcp = FastMCP(
    name="ocs-reasonix-bridge",
    instructions="OCS-Reasonix 桥接服务器 —— 连接 OCS 网课助手的自动答题功能和 Reasonix AI。",
)


# ── MCP Tools ──────────────────────────────────────────────────

@mcp.tool()
async def search_answer(
    question: str,
    options: Optional[list[str]] = None,
    type: str = "single"
) -> str:
    """搜索网课题目的答案。支持单选题(single)、多选题(multiple)、判断题(judgement)、填空题(completion)。"""
    opts = options or []
    qtype = type or "single"
    try:
        answers, from_cache = await get_answer(question, opts, qtype)
        return json.dumps({
            "success": True,
            "question": question,
            "answers": answers,
            "type": qtype,
            "from_cache": from_cache
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "question": question,
            "error": str(e)
        }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_bridge_status() -> str:
    """获取桥接服务器状态和统计信息。"""
    uptime = time.time() - stats["start_time"]
    total = stats["total_requests"]
    hit_rate = (stats["cache_hits"] / total * 100) if total > 0 else 0
    return json.dumps({
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "api_key_configured": bool(DEEPSEEK_API_KEY),
        "port": BRIDGE_PORT,
        "uptime_seconds": round(uptime),
        "stats": {
            "total_requests": total,
            "cache_hits": stats["cache_hits"],
            "cache_size": len(answer_cache),
            "cache_hit_rate": f"{hit_rate:.1f}%",
            "ai_success": stats["ai_success"],
            "ai_errors": stats["ai_errors"],
        },
        "last_error": stats["last_error"],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def clear_cache() -> str:
    """清空本地答案缓存。"""
    count = len(answer_cache)
    answer_cache.clear()
    return json.dumps({"success": True, "cleared": count}, ensure_ascii=False)


# ── 自定义 HTTP 路由 (OCS 接口) ──────────────────────────────

@mcp.custom_route("/health", ["GET"])
async def health(request):
    """健康检查 — OCS 可用此端点判断桥接是否在线"""
    from starlette.responses import JSONResponse
    ai_ok = bool(DEEPSEEK_API_KEY)
    uptime = time.time() - stats["start_time"]
    return JSONResponse({
        "status": "ok" if ai_ok else "no_api_key",
        "model": DEEPSEEK_MODEL,
        "uptime_seconds": round(uptime),
        "cache_entries": len(answer_cache),
    })


@mcp.custom_route("/stats", ["GET"])
async def stats_endpoint(request):
    """统计端点"""
    from starlette.responses import JSONResponse
    total = stats["total_requests"]
    hit_rate = (stats["cache_hits"] / total * 100) if total > 0 else 0
    return JSONResponse({
        "uptime_seconds": round(time.time() - stats["start_time"]),
        "total_requests": total,
        "cache_hits": stats["cache_hits"],
        "cache_size": len(answer_cache),
        "cache_hit_rate": f"{hit_rate:.1f}%",
        "ai_success": stats["ai_success"],
        "ai_errors": stats["ai_errors"],
        "last_error": stats["last_error"],
    })


@mcp.custom_route("/adapter-service/search", ["POST", "OPTIONS"])
async def adapter_search(request):
    """TikuAdapter 兼容接口 —— OCS 网课助手直接调用"""
    from starlette.responses import JSONResponse

    # CORS 预检
    if request.method == "OPTIONS":
        return JSONResponse(None, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        })

    try:
        body = await request.json()
    except Exception:
        log_request("POST", "/adapter-service/search", "[parse error]", 400)
        return JSONResponse({"code": -1, "msg": "请求体 JSON 解析失败"}, status_code=400)

    question = body.get("question", "")
    options = body.get("options")
    qtype_raw = body.get("type", "single")

    # OCS TikuAdapter 发送数字类型: 0=single, 1=multiple, 3=completion, 4=judgement
    TYPE_MAP = {"0": "single", "1": "multiple", "3": "completion", "4": "judgement"}
    qtype = TYPE_MAP.get(str(qtype_raw), str(qtype_raw) if qtype_raw else "single")

    if not question:
        return JSONResponse({"code": -1, "msg": "question 不能为空"}, status_code=400)

    if isinstance(options, str):
        options = options.split("\n")
    if not options:
        options = []

    try:
        answers, from_cache = await get_answer(question, options, qtype)
        log_request("POST", "/adapter-service/search", question[:60], 200)
        return JSONResponse({
            "code": 1,
            "question": question,
            "answer": {"allAnswer": [answers]},
            "msg": "success",
            "from_cache": from_cache,
        }, headers={"Access-Control-Allow-Origin": "*"})
    except RuntimeError as e:
        log_request("POST", "/adapter-service/search", str(e)[:80], 500)
        return JSONResponse({
            "code": -1,
            "question": question,
            "answer": {"allAnswer": []},
            "msg": str(e)
        }, headers={"Access-Control-Allow-Origin": "*"})
    except asyncio.TimeoutError:
        log_request("POST", "/adapter-service/search", "timeout", 504)
        return JSONResponse({
            "code": -1,
            "question": question,
            "answer": {"allAnswer": []},
            "msg": "AI 答题超时，请重试"
        }, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        log_request("POST", "/adapter-service/search", str(e)[:80], 500)
        return JSONResponse({
            "code": -1,
            "question": question,
            "answer": {"allAnswer": []},
            "msg": f"AI 错误: {str(e)}"
        }, headers={"Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/api/search", ["POST"])
async def simple_search(request):
    """简化接口 —— 直接返回答案列表"""
    from starlette.responses import JSONResponse
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "请求体 JSON 解析失败"}, status_code=400)

    question = body.get("question", "")
    options = body.get("options")
    qtype_raw = body.get("type", "single")

    # OCS 可能发送数字类型: 0=single, 1=multiple, 3=completion, 4=judgement
    TYPE_MAP = {"0": "single", "1": "multiple", "3": "completion", "4": "judgement"}
    qtype = TYPE_MAP.get(str(qtype_raw), str(qtype_raw) if qtype_raw else "single")

    if not question:
        return JSONResponse({"success": False, "error": "question 不能为空"}, status_code=400)

    if isinstance(options, str):
        options = options.split("\n")
    if not options:
        options = []

    try:
        answers, from_cache = await get_answer(question, options, qtype)
        return JSONResponse({
            "success": True,
            "question": question,
            "answers": answers,
            "type": qtype,
            "from_cache": from_cache,
        })
    except Exception as e:
        return JSONResponse({
            "success": False,
            "question": question,
            "answers": [],
            "error": str(e)
        }, status_code=500)


# ── 入口 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OCS-Reasonix Bridge")
    parser.add_argument("--port", type=int, default=BRIDGE_PORT,
                        help=f"HTTP 端口 (默认: {BRIDGE_PORT})")
    parser.add_argument("--mcp-stdio", action="store_true",
                        help="使用 MCP stdio 传输（供 Reasonix 本地子进程方式调用）")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("WARNING: DEEPSEEK_API_KEY not set!", file=sys.stderr)
        print("   Please set DEEPSEEK_API_KEY environment variable", file=sys.stderr)
        print("   Or create .env file with: DEEPSEEK_API_KEY=sk-xxx", file=sys.stderr)

    if args.mcp_stdio:
        print(f"OCS-Reasonix Bridge (MCP stdio)", file=sys.stderr)
        mcp.run(transport="stdio")
    else:
        print(f"OCS-Reasonix Bridge started")
        print(f"   HTTP API:   http://localhost:{args.port}")
        print(f"   Health:     http://localhost:{args.port}/health")
        print(f"   Stats:      http://localhost:{args.port}/stats")
        print(f"   OCS 端点:   http://localhost:{args.port}/adapter-service/search")
        print(f"   MCP 端点:   http://localhost:{args.port}/mcp")
        print(f"   Model:      {DEEPSEEK_MODEL}")
        print(f"   Retries:    {MAX_RETRIES}  |  Timeout: {ANSWER_TIMEOUT}s  |  Cache: {CACHE_SIZE}")

        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
