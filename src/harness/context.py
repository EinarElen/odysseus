from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _compact_message(message: Dict[str, Any]) -> Dict[str, str]:
    role = str(message.get("role") or "user").strip() or "user"
    return {"role": role, "content": _message_text(message.get("content")).strip()}


def reconcile_prompt_for_harness(
    *,
    messages: List[Dict[str, Any]],
    current_message: str,
    harness_id: str,
) -> Dict[str, Any]:
    """Build a deterministic Odysseus context envelope for an external harness.

    The normal LLM path already receives `messages` directly. Harnesses can own
    their own conversation state, so Odysseus sends a compact, deterministic
    reconciliation envelope with the prompt. Keeping this isolated to harness
    sessions avoids perturbing regular provider cache keys.
    """

    current = str(current_message or "").strip()
    compacted = [_compact_message(m) for m in messages or [] if isinstance(m, dict)]
    compacted = [m for m in compacted if m["content"]]
    if compacted and compacted[-1]["role"] == "user" and compacted[-1]["content"].strip() == current:
        compacted = compacted[:-1]

    payload = {
        "harness": str(harness_id or "").strip(),
        "messages": compacted,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return {
        "fingerprint": fingerprint,
        "messages": compacted,
        "serialized": serialized,
    }


def apply_reconciled_context_to_prompt(
    *,
    prompt: str,
    reconciliation: Optional[Dict[str, Any]],
) -> str:
    messages = (reconciliation or {}).get("messages")
    if not messages:
        return prompt
    fingerprint = str((reconciliation or {}).get("fingerprint") or "")
    serialized = str((reconciliation or {}).get("serialized") or "")
    return (
        "<odysseus_context"
        + (f' fingerprint="{fingerprint}"' if fingerprint else "")
        + ">\n"
        + serialized
        + "\n</odysseus_context>\n\n"
        + str(prompt or "")
    )
