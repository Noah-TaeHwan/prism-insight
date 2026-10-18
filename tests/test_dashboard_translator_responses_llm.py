"""2026-10-01~10-18: dashboard translation sent Chat Completions max_tokens, which
gpt-6 rejects (400), so every English dashboard field kept the Korean original.
It now uses the Responses LLM, and an empty answer keeps the original text.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from cores.llm.openai_responses_llm import OpenAIResponsesLLM  # noqa: E402
from translation_utils import DashboardTranslator  # noqa: E402


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    async def generate_str(self, message, request_params=None):
        return self.reply


class FakeAgent:
    def __init__(self, reply):
        self.reply, self.attached = reply, []

    async def attach_llm(self, llm_class):
        self.attached.append(llm_class)
        return FakeLLM(self.reply)


@pytest.mark.parametrize("reply,expected", [("Sold at stop.", "Sold at stop."), ("  ", "손절 매도")])
def test_translate_text_uses_responses_llm_and_keeps_original_on_empty(reply, expected):
    translator = DashboardTranslator()
    translator.translation_agent = FakeAgent(reply)

    assert asyncio.run(translator.translate_text("손절 매도")) == expected
    assert translator.translation_agent.attached == [OpenAIResponsesLLM]


def test_translate_batch_uses_responses_llm():
    translator = DashboardTranslator()
    translator.translation_agent = FakeAgent("[1] Leading sector\n\n[2] Stop hit")

    assert asyncio.run(translator.translate_batch(["주도 업종", "손절"])) == ["Leading sector", "Stop hit"]
    assert translator.translation_agent.attached == [OpenAIResponsesLLM]
