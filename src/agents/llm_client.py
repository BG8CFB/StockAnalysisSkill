from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional, Union

import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# 多轮工具调用安全上限
_MAX_TOOL_TURNS = 10

# tool_executor 类型：同步或异步，接受 (tool_name, arguments) → str
ToolExecutor = Callable[[str, str], Union[str, Coroutine[Any, Any, str]]]


class LLMClient:
    """
    AsyncOpenAI 包装器。

    双信号量控制：
      1. per-task 信号量（task_semaphore）：限制单任务并发 LLM 调用数
      2. global 信号量（_global_semaphore）：限制全局 API 调用总数
    获取顺序固定（先 per-task 再 global），避免死锁。

    支持两种调用模式：
      1. 简单模式（tools=None）：单轮调用，返回 content。向后兼容。
      2. 工具模式（tools + tool_executor）：多轮调用，AI 可自主调用工具，
         循环直到 AI 不再调用工具，返回最终 content 作为报告。
    """

    def __init__(self, global_semaphore: asyncio.Semaphore) -> None:
        from src.config import settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_api_base,
            api_key=settings.llm_api_key,
        )
        self._global_semaphore = global_semaphore
        self._settings = settings

    # ---------------------------------------------------------------------- #
    # 内部：单次 API 调用（含信号量 + 重试）                                     #
    # ---------------------------------------------------------------------- #

    async def _api_call(
        self,
        messages: list[dict],
        task_semaphore: asyncio.Semaphore,
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
    ):
        """
        单次 LLM API 调用，含信号量获取和指数退避重试。

        返回 ChatCompletionMessage 对象（含 content / tool_calls / reasoning_content）。
        """
        settings = self._settings
        target_model = model or settings.llm_model_id
        last_exception: Optional[Exception] = None

        async with task_semaphore:
            async with self._global_semaphore:
                for attempt in range(settings.llm_max_retries + 1):
                    try:
                        kwargs = dict(
                            model=target_model,
                            messages=messages,
                            temperature=settings.llm_temperature,
                            max_tokens=settings.llm_max_tokens,
                        )
                        if tools:
                            kwargs["tools"] = tools

                        logger.info(
                            f"[LLM] 调用 {target_model}"
                            f"（{len(messages)} 条消息"
                            f"{f'，{len(tools)} 个工具' if tools else ''}）"
                            f"  attempt={attempt + 1}/{settings.llm_max_retries + 1}"
                        )
                        response = await asyncio.wait_for(
                            self._client.chat.completions.create(**kwargs),
                            timeout=settings.llm_timeout_seconds,
                        )
                        msg = response.choices[0].message
                        content = msg.content or ""

                        # GLM 思考模式：reasoning_content 是思维链，content 是最终输出
                        reasoning = getattr(msg, "reasoning_content", None)
                        if reasoning:
                            logger.debug(
                                f"[LLM] 思维链 {len(reasoning)} 字，最终输出 {len(content)} 字"
                            )
                        # content 为空且无工具调用时警告
                        if not content and reasoning and not getattr(msg, "tool_calls", None):
                            logger.error(
                                "[LLM] ✗ content 为空但 reasoning_content 存在，"
                                "max_tokens 可能不足（GLM 思考模式）"
                            )

                        logger.info(f"[LLM] ✓ 调用成功，返回 {len(content)} 字")
                        return msg

                    except asyncio.TimeoutError as e:
                        last_exception = e
                        logger.warning(
                            f"[LLM] ⚠ 第 {attempt + 1}/{settings.llm_max_retries + 1} 次调用超时"
                        )
                    except openai.APIStatusError as e:
                        last_exception = e
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

        error_detail = f": {last_exception}" if last_exception else ""
        raise RuntimeError(
            f"LLM call failed after {settings.llm_max_retries + 1} attempts{error_detail}"
        ) from last_exception

    # ---------------------------------------------------------------------- #
    # 公开接口                                                                  #
    # ---------------------------------------------------------------------- #

    async def call(
        self,
        system_prompt: str,
        user_message: str,
        task_semaphore: asyncio.Semaphore,
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ) -> str:
        """
        发起 LLM 调用，返回模型最终输出文本。

        简单模式（tools=None）：
          单轮调用，返回 content。向后兼容。

        工具模式（tools + tool_executor）：
          多轮调用循环。每轮：
            1. 发送 messages + tools 给 LLM
            2. LLM 返回 tool_calls → 执行工具 → 追加结果 → 下一轮
            3. LLM 返回 content（无 tool_calls）→ 返回 content 作为最终报告
          中间所有 tool_call / tool_result / reasoning_content 不进入报告。
          消息历史 append-only，天然缓存友好。
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        if not tools:
            # ── 简单路径（向后兼容）────────────────────────────────────────
            msg = await self._api_call(messages, task_semaphore, model)
            return msg.content or ""

        # ── 多轮工具调用路径 ──────────────────────────────────────────────
        last_msg = None
        for turn in range(_MAX_TOOL_TURNS):
            msg = await self._api_call(messages, task_semaphore, model, tools)
            last_msg = msg

            # 无工具调用 → 最终响应
            if not msg.tool_calls:
                content = msg.content or ""
                if turn > 0:
                    logger.info(
                        f"[LLM] 技能调用完成（{turn + 1} 轮），最终输出 {len(content)} 字"
                    )
                return content

            # 有工具调用 → 执行后继续
            logger.info(f"[LLM] Turn {turn + 1}: {len(msg.tool_calls)} 个技能调用")

            # 追加 assistant 消息（含 tool_calls）
            assistant_msg: dict = {"role": "assistant"}
            if msg.content:
                assistant_msg["content"] = msg.content
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
            messages.append(assistant_msg)

            # 执行每个工具调用
            for tc in msg.tool_calls:
                logger.info(f"[LLM]   → 调用技能: {tc.function.name}")
                try:
                    result = tool_executor(tc.function.name, tc.function.arguments)
                    if asyncio.iscoroutine(result):
                        result = await result
                except Exception as e:
                    logger.error(f"[LLM]   ✗ 技能执行失败: {tc.function.name} — {e}")
                    result = f"[技能执行失败: {e}]"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # 超过最大轮数
        logger.error(f"[LLM] ✗ 技能调用超过 {_MAX_TOOL_TURNS} 轮上限")
        content = last_msg.content if last_msg else ""
        return content or "[技能调用超过最大轮数限制]"
