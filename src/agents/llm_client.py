from __future__ import annotations

import asyncio
import logging
from typing import Optional

import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """
    AsyncOpenAI 包装器。
    双信号量控制：
      1. per-task 信号量（task_semaphore）：限制单任务并发 LLM 调用数
      2. global 信号量（_global_semaphore）：限制全局 API 调用总数
    获取顺序固定（先 per-task 再 global），避免死锁。
    """

    def __init__(self, global_semaphore: asyncio.Semaphore) -> None:
        from src.config import settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_api_base,
            api_key=settings.llm_api_key,
        )
        self._global_semaphore = global_semaphore
        self._settings = settings

    async def call(
        self,
        system_prompt: str,
        user_message: str,
        task_semaphore: asyncio.Semaphore,
        model: Optional[str] = None,
    ) -> str:
        """
        发起 LLM 调用，返回模型输出文本。
        先获取 task_semaphore，再获取 global_semaphore（顺序固定防死锁）。
        失败时指数退避重试，最多 settings.llm_max_retries 次。
        """
        settings = self._settings
        target_model = model or settings.llm_model_id
        last_exception: Optional[Exception] = None

        async with task_semaphore:
            async with self._global_semaphore:
                for attempt in range(settings.llm_max_retries + 1):
                    try:
                        logger.info(
                            f"[LLM] 调用 {target_model}（系统 {len(system_prompt)} 字 + 用户 {len(user_message)} 字）"
                            f"  attempt={attempt + 1}/{settings.llm_max_retries + 1}"
                        )
                        response = await asyncio.wait_for(
                            self._client.chat.completions.create(
                                model=target_model,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_message},
                                ],
                                temperature=settings.llm_temperature,
                                max_tokens=settings.llm_max_tokens,
                            ),
                            timeout=settings.llm_timeout_seconds,
                        )
                        msg = response.choices[0].message
                        content = msg.content or ""
                        # GLM-5 thinking mode: reasoning_content is the CoT trace,
                        # content is the final answer. Log reasoning length if present.
                        reasoning = getattr(msg, "reasoning_content", None)
                        if reasoning:
                            logger.debug(
                                f"[LLM] 思维链 {len(reasoning)} 字，最终输出 {len(content)} 字"
                            )
                        if not content and reasoning:
                            # Fallback: if content is empty but reasoning exists,
                            # this usually means max_tokens was exhausted by reasoning.
                            logger.error(
                                "[LLM] ✗ content 为空但 reasoning_content 存在，max_tokens 可能不足（GLM 思考模式）"
                            )
                        logger.info(f"[LLM] ✓ 调用成功，返回 {len(content)} 字")
                        return content
                    except asyncio.TimeoutError as e:
                        last_exception = e
                        logger.warning(
                            f"[LLM] ⚠ 第 {attempt + 1}/{settings.llm_max_retries + 1} 次调用超时"
                        )
                    except openai.APIStatusError as e:
                        last_exception = e
                        # 4xx 错误不重试（除 429）
                        if e.status_code and 400 <= e.status_code < 500 and e.status_code != 429:
                            logger.error(f"[LLM] ✗ 不可重试错误: {e.status_code} {e.message}")
                            raise
                        logger.warning(
                            f"[LLM] ⚠ API 错误 {e.status_code}（第 {attempt + 1} 次）: {e.message}"
                        )
                    except openai.APIError as e:
                        last_exception = e
                        logger.warning(
                            f"[LLM] ⚠ API 错误（第 {attempt + 1} 次）: {e}"
                        )

                    # 指数退避（2^attempt 秒，最多 16 秒）
                    if attempt < settings.llm_max_retries:
                        wait = min(2 ** attempt, 16)
                        logger.info(f"[LLM] {wait}s 后重试...")
                        await asyncio.sleep(wait)

        raise RuntimeError(
            f"LLM call failed after {settings.llm_max_retries + 1} attempts"
        ) from last_exception
