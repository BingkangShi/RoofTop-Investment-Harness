"""Model routing without copying secrets into the project.

The existing external JSON remains the credential store.  This module exposes
only capability/status metadata and supports OpenAI-compatible chat endpoints
plus the OpenAI Responses API route used for Codex-class research agents.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(os.environ.get("ROOFTOP_MODEL_CONFIG", PROJECT_ROOT / "configure_list.json"))
ROLE_MODELS = {
    "test_llm": "deepseek-v4-flash",
    "document_mllm": "qwen3.8-max",
    "research_codex": os.environ.get("ROOFTOP_CODEX_MODEL", ""),
}

CHAT_MODEL_SPECS = (
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "DeepSeek", "默认 · 快速研究"),
    ("deepseek-chat", "DeepSeek Chat", "DeepSeek", "通用分析"),
    ("qwen3-235b-a22b", "Qwen3 235B A22B", "DashScope", "长文本研究"),
    ("qwen3.8-max", "Qwen 3.8 Max", "DashScope", "文本/多模态模型"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash", "Google", "快速长上下文"),
    ("gpt-4o", "GPT-4o", "OpenAI", "通用多模态"),
    ("grok-4-0709", "Grok 4", "xAI", "通用研究"),
)


@dataclass(frozen=True)
class ModelRoute:
    role: str
    model: str
    base_url: str
    api_style: str
    configured: bool


def _items(path: Path = DEFAULT_CONFIG) -> list[dict]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def route_status(path: Path = DEFAULT_CONFIG) -> list[dict]:
    items = {item.get("model"): item for item in _items(path)}
    routes = []
    for role, model in ROLE_MODELS.items():
        item = items.get(model, {}) if model else {}
        routes.append(ModelRoute(
            role=role,
            model=model or "需要设置 ROOFTOP_CODEX_MODEL",
            base_url=item.get("base_url", "https://api.openai.com/v1" if role == "research_codex" else ""),
            api_style="responses" if role == "research_codex" else "openai_compatible_chat",
            configured=bool(model and item and (item.get("api_key") or item.get("key"))),
        ).__dict__)
    return routes


def credential_for(model: str, path: Path = DEFAULT_CONFIG) -> dict:
    """Return a credential only to an in-process caller; never serialize it."""
    for item in _items(path):
        if item.get("model") == model:
            return dict(item)
    raise KeyError(f"model route not found: {model}")


def chat_model_options(path: Path = DEFAULT_CONFIG) -> list[dict]:
    configured = {item.get("model"): item for item in _items(path)}
    return [
        {"id": model, "label": label, "provider": provider, "description": description,
         "configured": bool(configured.get(model, {}).get("api_key") or configured.get(model, {}).get("key")),
         "default": model == "deepseek-v4-flash"}
        for model, label, provider, description in CHAT_MODEL_SPECS
    ]


def require_chat_model(model: str, path: Path = DEFAULT_CONFIG) -> dict:
    allowed = {item[0] for item in CHAT_MODEL_SPECS}
    if model not in allowed:
        raise ValueError("unsupported Agent Chat model")
    credential = credential_for(model, path)
    if not (credential.get("api_key") or credential.get("key")):
        raise ValueError("selected Agent Chat model is not configured")
    return credential
