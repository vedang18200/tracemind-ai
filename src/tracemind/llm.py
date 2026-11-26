# llm.py
import json
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from tracemind.config import settings

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    async def structured(self, *, system: str, user: str, schema: type[T]) -> T: ...


class FakeLLM:
    """Returns canned objects keyed by schema name. Deterministic, free."""

    def __init__(self, responses: dict[str, BaseModel | list[BaseModel]]):
        self.responses = {
            k: list(v) if isinstance(v, list) else v for k, v in responses.items()
        }
        self.calls: list[dict] = []

    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls.append({"schema": schema.__name__, "system": system, "user": user})
        r = self.responses.get(schema.__name__)
        if r is None:
            raise KeyError(f"FakeLLM has no response for {schema.__name__}")
        if isinstance(r, list):
            if not r:
                raise RuntimeError(f"FakeLLM exhausted responses for {schema.__name__}")
            r = r.pop(0)
        assert isinstance(r, schema), (
            f"Fixture is {type(r).__name__}, expected {schema.__name__}"
        )
        return r


def _coerce(data, input_schema: dict):
    """Repair a known weak-model quirk: lists/objects returned as JSON strings.
    Only touches fields the schema says are array/object. Validation still runs after."""
    if not isinstance(data, dict):
        return data
    props = input_schema.get("properties", {})
    decoder = json.JSONDecoder()
    out = {}
    for k, v in data.items():
        if isinstance(v, str) and props.get(k, {}).get("type") in ("array", "object"):
            s = v.lstrip()
            if s[:1] in ("[", "{"):
                try:
                    v, _ = decoder.raw_decode(s)    # first JSON value, ignores trailing junk
                except ValueError:
                    pass
        out[k] = v
    return out


class AnthropicLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=api_key or settings.llm_api_key)
        self.model = model or settings.llm_model
        self.usage = {"input": 0, "output": 0}

    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        tool = {
            "name": "respond",
            "description": "Return the structured result.",
            "input_schema": schema.model_json_schema(),
        }
        messages = [{"role": "user", "content": user}]
        last_err: Exception | None = None

        for _ in range(settings.llm_validation_retries + 1):
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=settings.llm_max_tokens,
                system=system,
                messages=messages,
                tools=[tool],
                tool_choice={"type": "tool", "name": "respond"},
            )
            u = getattr(resp, "usage", None)
            if u is not None:
                self.usage["input"] += u.input_tokens
                self.usage["output"] += u.output_tokens
            if resp.stop_reason == "max_tokens":
                raise RuntimeError(
                    f"output truncated for {schema.__name__} (max_tokens={settings.llm_max_tokens})"
                )
            block = next((b for b in resp.content if b.type == "tool_use"), None)
            if block is None:
                last_err = RuntimeError("model returned no tool call")
                continue
            try:
                return schema.model_validate(_coerce(block.input, tool["input_schema"]))
            except ValidationError as e:
                last_err = e
                messages = [{"role": "user", "content": (
                    f"{user}\n\nYour previous output failed validation:\n{e}\nReturn a corrected result."
                )}]

        raise RuntimeError(f"structured output failed for {schema.__name__}: {last_err}")


def get_llm(strong: bool = False) -> LLM:
    if settings.llm_provider == "anthropic":
        model = (settings.llm_model_strong if strong else "") or settings.llm_model
        return AnthropicLLM(model=model)
    raise ValueError(f"unknown llm_provider: {settings.llm_provider}")