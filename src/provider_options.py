"""Provider-specific request option metadata and sanitization.

The UI consumes the same schema that the backend uses to validate options, so
provider-specific controls can be added without hard-coding each screen.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Dict, List

_CODEX_REASONING_VALUES = ("auto", "minimal", "low", "medium", "high", "xhigh")
_CODEX_SERVICE_TIERS = ("standard", "fast")


def _codex_fast_supported(model: str) -> bool:
    mid = (model or "").lower()
    return mid.startswith("gpt-5.5") or mid == "gpt-5.4" or mid.startswith("gpt-5.4-")


def _codex_options_schema(model: str = "") -> List[Dict[str, Any]]:
    schema: List[Dict[str, Any]] = [
        {
            "key": "reasoning_effort",
            "label": "Effort",
            "type": "select",
            "default": "auto",
            "options": [
                {"value": "auto", "label": "Auto"},
                {"value": "minimal", "label": "Minimal"},
                {"value": "low", "label": "Low"},
                {"value": "medium", "label": "Medium"},
                {"value": "high", "label": "High"},
                {"value": "xhigh", "label": "X-High"},
            ],
            "request": {"field": "reasoning.effort", "omit_value": "auto"},
        }
    ]
    if not model or _codex_fast_supported(model):
        schema.append(
            {
                "key": "service_tier",
                "label": "Tier",
                "type": "select",
                "default": "standard",
                "options": [
                    {"value": "standard", "label": "Standard"},
                    {"value": "fast", "label": "Fast"},
                ],
                "request": {"field": "service_tier", "omit_value": "standard"},
            }
        )
    return schema


def provider_options_schema(base_url: str, model: str = "") -> List[Dict[str, Any]]:
    from src.chatgpt_subscription import is_chatgpt_subscription_base

    if is_chatgpt_subscription_base(base_url or ""):
        return _codex_options_schema(model)
    return []


_HARNESS_MODES = {"observe", "bridged"}
_HARNESS_THINKING_LEVELS = {"minimal", "low", "medium", "high", "xhigh"}
_HARNESS_VERBOSITY = {"quiet", "normal", "debug"}
_SAFE_CONFIG_TEXT = re.compile(r"^[A-Za-z0-9._:/@+\- ]+$")


def _known_harness_ids() -> set[str]:
    try:
        from src.harness import list_harness_ids
        return set(list_harness_ids())
    except Exception:
        return {"pi"}


def _clean_text(value: Any, *, max_len: int = 256, allow_path: bool = False) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_len:
        return ""
    if allow_path:
        return text
    return text if _SAFE_CONFIG_TEXT.match(text) else ""


def _sanitize_harness_options(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    harness_id = _clean_text(raw.get("id"), max_len=64)
    if harness_id not in _known_harness_ids():
        return {}

    out: Dict[str, Any] = {"id": harness_id}
    mode = _clean_text(raw.get("mode"), max_len=32)
    if mode in _HARNESS_MODES:
        out["mode"] = mode

    workspace = _clean_text(raw.get("workspace"), max_len=2048, allow_path=True)
    if workspace:
        out["workspace"] = workspace

    for key in ("agent_dir", "agentDir", "session_dir", "sessionDir", "session_file", "sessionFile"):
        value = _clean_text(raw.get(key), max_len=2048, allow_path=True)
        if value:
            out[key] = value

    for key in ("model_provider", "provider", "model", "model_id", "requested_session_id", "session_id"):
        value = _clean_text(raw.get(key), max_len=256)
        if value:
            out[key] = value

    resume_mode = _clean_text(raw.get("resume_mode") or raw.get("resumeMode"), max_len=32)
    if resume_mode in {"create", "continue", "open"}:
        out["resume_mode"] = resume_mode

    thinking_level = _clean_text(raw.get("thinking_level"), max_len=32)
    if thinking_level in _HARNESS_THINKING_LEVELS:
        out["thinking_level"] = thinking_level

    verbosity = _clean_text(raw.get("verbosity"), max_len=32)
    if verbosity in _HARNESS_VERBOSITY:
        out["verbosity"] = verbosity

    for key in ("provide_odysseus_tools", "accept_harness_tools", "disable_native_tools", "resume", "persist", "in_memory", "inMemory", "new_session"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]

    no_tools = raw.get("no_tools")
    if isinstance(no_tools, bool):
        out["no_tools"] = no_tools
    else:
        no_tools_text = _clean_text(no_tools, max_len=64)
        if no_tools_text:
            out["no_tools"] = no_tools_text

    for key in ("tools", "exclude_tools"):
        values = raw.get(key)
        if isinstance(values, list):
            clean_values = [
                _clean_text(item, max_len=128)
                for item in values
            ]
            clean_values = [item for item in clean_values if item]
            if clean_values:
                out[key] = clean_values

    return out


def sanitize_provider_options(base_url: str, model: str, options: Any) -> Dict[str, Any]:
    if not isinstance(options, dict):
        return {}
    schema = provider_options_schema(base_url, model)
    allowed = {item.get("key"): item for item in schema if item.get("key")}
    out: Dict[str, Any] = {}
    harness = _sanitize_harness_options(options.get("harness"))
    if harness:
        out["harness"] = harness
    for key, raw in options.items():
        if str(key) == "harness":
            continue
        item = allowed.get(str(key))
        if not item:
            continue
        value = str(raw or "").strip()
        valid = {str(opt.get("value")) for opt in item.get("options") or []}
        default = str(item.get("default") or "")
        if value not in valid:
            value = default
        if value:
            out[str(key)] = value
    return out


def options_for_schema(schema: List[Dict[str, Any]], options: Dict[str, Any] | None) -> Dict[str, str]:
    raw = options if isinstance(options, dict) else {}
    out: Dict[str, str] = {}
    for item in schema or []:
        key = str(item.get("key") or "")
        if not key:
            continue
        default = str(item.get("default") or "")
        value = str(raw.get(key, default) or default)
        valid = {str(opt.get("value")) for opt in item.get("options") or []}
        out[key] = value if value in valid else default
    return out


def endpoint_options_schema(base_url: str, models: List[str] | None = None) -> List[Dict[str, Any]]:
    """Return a union schema for an endpoint's available models."""
    models = models or [""]
    by_key: Dict[str, Dict[str, Any]] = {}
    for model in models:
        for item in provider_options_schema(base_url, model):
            key = item.get("key")
            if key and key not in by_key:
                by_key[key] = deepcopy(item)
    return list(by_key.values())
