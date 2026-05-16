"""Chat handler — turns user message into LLM response per PROJECT_SPEC.md §16.4.

Phase 7 MVP: synchronous single-shot call (no streaming) returning the
full assistant message. Streaming token-by-token via Anthropic's stream
API is a Phase 9 enhancement.
"""

from __future__ import annotations

from ...common.llm_client import LLMClient
from ...common.schemas import ChatMessage

_SYSTEM_PROMPT = (
    "你是港股 IPO 基石轮分析助手。回答必须基于上文的 snapshot / agent_outputs 数据，"
    "禁止编造事实，禁止无引用的断言。"
)


async def reply(
    llm: LLMClient,
    *,
    history: list[ChatMessage],
    user_message: str,
    model: str = "claude-sonnet-4",
) -> str:
    """Generate the assistant reply text given prior message history."""
    msgs = []
    for m in history:
        if m.role.value in {"user", "assistant"}:
            msgs.append({"role": m.role.value, "content": m.content})
    msgs.append({"role": "user", "content": user_message})
    resp = await llm.acomplete(
        model=model,
        messages=msgs,
        system=_SYSTEM_PROMPT,
        max_tokens=1500,
        temperature=0.4,
        agent_role="chat",
    )
    return resp.text


__all__ = ("reply",)
