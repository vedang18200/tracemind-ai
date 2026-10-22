# agents/common.py
from functools import wraps
from pathlib import Path

from tracemind.config import settings

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text()

def fail(state, stage: str, code: str, message: str) -> dict:
    return {
        "status": "failed",
        "current_stage": stage,
        "errors": [{"stage": stage, "code": code, "message": message}],
    }

def node(stage: str):
    """Enforces the step limit, converts crashes into failed state, bumps step_count."""
    def deco(fn):
        @wraps(fn)
        async def wrapper(state):
            if state["step_count"] >= settings.max_agent_steps:
                return fail(state, stage, "step_limit", "max_agent_steps exceeded")
            try:
                update = await fn(state)
            except Exception as e:
                return fail(state, stage, "node_exception", f"{type(e).__name__}: {e}")
            if update.get("status") == "failed":
                return update
            return {**update, "step_count": state["step_count"] + 1, "current_stage": stage}
        return wrapper
    return deco