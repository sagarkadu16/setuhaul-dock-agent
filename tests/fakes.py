"""A scripted chat model, so agent tests run offline and free (plan §9)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


def say(text: str) -> AIMessage:
    return AIMessage(content=text)


def call(name: str, call_id: str | None = None, **args: Any) -> AIMessage:
    """An AI message asking for one tool call."""
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": call_id or f"call_{name}", "type": "tool_call"}
    ])


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted responses in order; an Exception in the script is raised instead.
    `seen` records the messages each call received."""

    script: list[Any]
    seen: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # tools are ignored; the script decides
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("scripted model ran out of responses")
        response = self.script.pop(0)
        if isinstance(response, Exception):
            raise response
        return ChatResult(generations=[ChatGeneration(message=response)])
