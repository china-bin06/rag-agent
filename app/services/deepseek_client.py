import json
import time
from collections.abc import Awaitable, Callable

import httpx

from app.core.config import settings
from app.services.answer_draft import AnswerDraft


class DeepSeekError(RuntimeError):
    pass


class ModelJSONError(DeepSeekError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def completion_request(
    *,
    system_prompt: str,
    user_payload: dict,
    max_tokens: int | None = None,
    stream: bool = False,
) -> tuple[str, dict, dict]:
    api_key = settings.deepseek_api_key.get_secret_value().strip()
    if not api_key:
        raise DeepSeekError("未配置DEEPSEEK_API_KEY")

    output_limit = (
        max_tokens
        if max_tokens is not None
        else settings.deepseek_max_tokens
    )
    if output_limit < 1:
        raise ValueError("max_tokens必须大于0")

    url = (
        settings.deepseek_base_url.rstrip("/")
        + "/chat/completions"
    )

    body = {
        "model": settings.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    system_prompt
                    + "\n必须只输出一个合法的JSON对象。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    user_payload,
                    ensure_ascii=False,
                ),
            },
        ],
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": output_limit,
        "stream": stream,
    }
    return url, {"Authorization": f"Bearer {api_key}"}, body


def complete_json(
    *,
    system_prompt: str,
    user_payload: dict,
    max_tokens: int | None = None,
) -> dict:
    url, headers, body = completion_request(
        system_prompt=system_prompt, user_payload=user_payload, max_tokens=max_tokens,
    )
    started = time.perf_counter()

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                settings.deepseek_timeout_seconds,
                connect=10.0,
            ),
            follow_redirects=False,
        ) as client:
            response = client.post(
                url,
                headers=headers,
                json=body,
            )
    except httpx.TimeoutException:
        raise DeepSeekError("DeepSeek请求超时") from None
    except httpx.RequestError:
        raise DeepSeekError("DeepSeek网络请求失败") from None

    elapsed = time.perf_counter() - started

    if response.status_code != 200:
        # 不直接输出响应正文，避免日志中包含请求资料。
        raise DeepSeekError(
            f"DeepSeek返回HTTP {response.status_code}"
        )

    try:
        payload = response.json()
        choice = payload["choices"][0]
        finish_reason = choice["finish_reason"]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise DeepSeekError("DeepSeek响应结构异常") from None

    return completion_result(payload, content, finish_reason, elapsed)


def completion_result(payload: dict, content: str | None, finish_reason: str | None, elapsed: float) -> dict:
    if finish_reason != "stop":
        raise DeepSeekError(
            f"模型输出未正常完成：{finish_reason}"
        )

    if not isinstance(content, str) or not content.strip():
        raise ModelJSONError("模型返回了空内容", "empty_output")

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise ModelJSONError("模型返回的内容不是合法JSON", "invalid_json") from None

    if not isinstance(data, dict):
        raise ModelJSONError("模型输出必须是JSON对象", "object_required")

    return {
        "data": data,
        "request_id": payload.get("id"),
        "requested_model": settings.deepseek_model,
        "reported_model": payload.get("model"),
        "system_fingerprint": payload.get("system_fingerprint"),
        "usage": payload.get("usage"),
        "elapsed_seconds": elapsed,
        "finish_reason": finish_reason,
    }


async def stream_json(
    *,
    system_prompt: str,
    user_payload: dict,
    on_draft: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    url, headers, body = completion_request(
        system_prompt=system_prompt, user_payload=user_payload, stream=True,
    )
    started = time.perf_counter()
    fragments: list[str] = []
    draft = AnswerDraft() if on_draft is not None else None
    metadata: dict = {}
    finish_reason = None
    done = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.deepseek_timeout_seconds, connect=10.0),
            follow_redirects=False,
        ) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    raise DeepSeekError(f"DeepSeek返回HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        frame = json.loads(data)
                        if "error" in frame:
                            raise DeepSeekError("DeepSeek流式请求失败")
                        metadata.update({key: frame[key] for key in (
                            "id", "model", "system_fingerprint", "usage",
                        ) if frame.get(key) is not None})
                        choices = frame["choices"]
                        if not choices:
                            continue
                        choice = choices[0]
                        finish_reason = choice.get("finish_reason") or finish_reason
                        content = choice["delta"].get("content")
                        if content is not None and not isinstance(content, str):
                            raise TypeError
                    except (AttributeError, ValueError, KeyError, IndexError, TypeError):
                        raise DeepSeekError("DeepSeek流式响应结构异常") from None
                    if content:
                        fragments.append(content)
                        if draft is not None and (text := draft.feed(content)):
                            await on_draft(text)
    except httpx.TimeoutException:
        raise DeepSeekError("DeepSeek请求超时") from None
    except httpx.RequestError:
        raise DeepSeekError("DeepSeek网络请求失败") from None
    if not done:
        raise DeepSeekError("DeepSeek流式响应提前结束")
    return completion_result(metadata, "".join(fragments), finish_reason, time.perf_counter() - started)
