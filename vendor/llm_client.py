"""LLM 客户端。

复制自 page-know-how/llm/client.py，剥离 verbose_logger 依赖（``log_llm_call`` /
``log_llm_error`` / ``is_session_active``），保留主链路与 retry 行为。

调用风格与主仓一致：

.. code-block:: python

    from case_refinery.vendor.llm_client import chat
    content = chat(
        messages="你好",
        vendor="servyou",
        model="deepseek-v3.2-1163259bcc6c",
        system="你是财税领域专家",
    )

返回 ``response["choices"][0]["message"]["content"]``；带 reasoning_content 的模型
会把推理过程以 ``<think>…</think>`` 前缀并入 content，保持与主仓下游一致。
"""

from __future__ import annotations

import json
import logging

import requests

from .utils_helpers import retry

logger = logging.getLogger(__name__)


@retry(max_retries=3, sleep_seconds=5.0)
def chat(
    messages: str,
    vendor: str = "qwen3.5-122b-a10b",
    model: str = "Qwen3.5-122B-A10B",
    system: str | None = None,
    enable_thinking: bool = False,
) -> str:
    """调用自有模型服务。

    与主仓行为一致：
    - vendor="qwen3.5-122b-a10b" (默认)：直连 Qwen3.5-122B-A10B 自部署服务
    - vendor="qwen3.6-35b-a3b"：mlp Qwen3.6-35B-A3B
    - vendor="qwen3.5-27b"：mlp Qwen3.5-27B
    - vendor="deepseek-v4-flash" / "deepseek-v4-pro"：mudgate DeepSeek
    - vendor="servyou"：mudgate servyou（默认承载 deepseek-v3.2 私有部署）
    - 其他：走 mudgate 网关
    """

    messages_payload: list[dict] = []
    if system:
        messages_payload.append({"role": "system", "content": system})
    messages_payload.append({"role": "user", "content": messages})

    if vendor == "qwen3.5-122b-a10b":
        url = "http://211.137.21.19:17860/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "Qwen3.5-122B-A10B",
            "messages": messages_payload,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
    elif vendor == "qwen3.6-35b-a3b":
        url = "http://mlp.paas.dc.servyou-it.com/qwen3.6-35b-a3b/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "Qwen/Qwen3.6-35B-A3B",
            "messages": messages_payload,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
    elif vendor == "qwen3.5-27b":
        url = "http://mlp.paas.dc.servyou-it.com/qwen3.5-27b/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "Qwen/Qwen3.5-27B",
            "messages": messages_payload,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
    elif vendor in ("deepseek-v4-flash", "deepseek-v4-pro"):
        used_model = vendor
        url = "http://mlp.paas.dc.servyou-it.com/mudgate/api/llm/deepseek/v1/chat/completions"
        app_id = "sk-0609aa6d08de4413a72e14b3fb8fbab1"
        headers = {"Content-Type": "application/json", "Authorization": app_id}
        payload = {
            "appId": app_id,
            "model": used_model,
            "messages": messages_payload,
            "stream": False,
        }
        if enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
    else:
        # servyou / 其他 mudgate vendor
        url = (
            f"http://mlp.paas.dc.servyou-it.com/mudgate/api/llm/{vendor}"
            "/v1/chat/completions"
        )
        if vendor == "servyou":
            app_id = "sk-a57093c05ed94f37a7c845ff3fd688e2"
        else:
            app_id = "sk-0609aa6d08de4413a72e14b3fb8fbab1"
        headers = {"Content-Type": "application/json", "Authorization": app_id}
        payload = {
            "appId": app_id,
            "model": model,
            "messages": messages_payload,
            "stream": False,
        }
        if enable_thinking:
            # servyou 承载 deepseek-v3.2 私有部署，thinking 开关走顶层字段；
            # 其他 mudgate vendor 走 chat_template_kwargs。
            if vendor == "servyou":
                payload["enable_thinking"] = True
            else:
                payload["chat_template_kwargs"] = {"enable_thinking": True}

    logger.debug("LLM 请求 [%s/%s]: %s...", vendor, model, messages[:100])

    response = requests.post(
        url, data=json.dumps(payload), headers=headers, timeout=(30, 360)
    ).json()

    if "success" in response:
        err = response.get("errorContext", "未知错误")
        raise Exception(err)

    result = response["choices"][0]["message"]
    content = result["content"] if isinstance(result, dict) else str(result)

    # 部分 OpenAI 兼容服务（deepseek-reasoner / deepseek-v3.2 thinking 模式）
    # 把思考过程放到 message.reasoning_content；统一回注成 <think> 前缀。
    if isinstance(result, dict):
        reasoning = result.get("reasoning_content")
        if reasoning and isinstance(reasoning, str) and reasoning.strip():
            if "<think>" not in (content or ""):
                content = f"<think>{reasoning.strip()}</think>\n{content or ''}"

    logger.debug("LLM 响应: %s...", (content or "")[:100])
    return content or ""


if __name__ == "__main__":
    print(chat(messages="who are you", vendor="deepseek-v4-pro"))
