from types import SimpleNamespace as NS

import pytest

from tracemind.llm import AnthropicLLM
from tracemind.schemas import PageExtraction


async def test_truncated_output_raises_clearly():
    async def fake_create(**kwargs):
        return NS(stop_reason="max_tokens", content=[NS(type="tool_use", input={})])

    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm.client = NS(messages=NS(create=fake_create))
    llm.model = "test"
    with pytest.raises(RuntimeError, match="truncated"):
        await llm.structured(system="s", user="u", schema=PageExtraction)