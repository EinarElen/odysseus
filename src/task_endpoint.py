"""Shared resolver for background-task AI endpoints."""

from src.endpoint_resolver import (
    resolve_chat_fallback_candidates,
    resolve_endpoint,
    resolve_utility_fallback_candidates,
)
from src.llm_core import llm_call_async
from src.interactive_gate import wait_for_interactive_quiet
from src.model_context import estimate_tokens
from src.usage_observability import RunContext, RunOutcome, SpanContext, SpanOutcome, UsageObservation, usage_store
from src.subscription_usage import provider_auth_id_for_endpoint
from src.llm_core import _detect_provider
import time


def resolve_task_endpoint(fallback_url=None, fallback_model=None, fallback_headers=None, owner=None):
    """Return (endpoint_url, model, headers) for background tasks.

    Reads task_endpoint_id / task_model from admin settings.
    Falls back to the provided values when the setting is empty or the
    endpoint cannot be resolved.
    """
    return resolve_endpoint("task", fallback_url, fallback_model, fallback_headers, owner=owner)


def resolve_task_candidates(
    fallback_url=None,
    fallback_model=None,
    fallback_headers=None,
    owner=None,
):
    """Return ordered background-task LLM candidates.

    Order:
    1. configured Background Tasks endpoint/model, or caller fallback
    2. Utility endpoint/model
    3. Default endpoint/model
    4. Utility fallback chain
    5. Default fallback chain
    """
    candidates = []

    def _append(url, model, headers):
        if not url or not model:
            return
        key = (url, model)
        if any((u, m) == key for u, m, _ in candidates):
            return
        candidates.append((url, model, headers or {}))

    _append(*resolve_task_endpoint(fallback_url, fallback_model, fallback_headers, owner=owner))
    _append(*resolve_endpoint("utility", owner=owner))
    _append(*resolve_endpoint("default", owner=owner))
    for url, model, headers in resolve_utility_fallback_candidates(owner=owner):
        _append(url, model, headers)
    for url, model, headers in resolve_chat_fallback_candidates(owner=owner):
        _append(url, model, headers)

    return candidates


async def task_llm_call_async(
    messages,
    *,
    fallback_url=None,
    fallback_model=None,
    fallback_headers=None,
    owner=None,
    usage_kind="task",
    task_id=None,
    session_id=None,
    **kwargs,
):
    """Call the shared background-task LLM candidate chain."""
    candidates = resolve_task_candidates(
        fallback_url=fallback_url,
        fallback_model=fallback_model,
        fallback_headers=fallback_headers,
        owner=owner,
    )
    if not candidates:
        raise RuntimeError("No LLM endpoint available for background task")
    await wait_for_interactive_quiet("background task LLM")
    kwargs.setdefault("workload", "background")
    run = usage_store.begin_run(RunContext(
        owner=owner or "local", kind=usage_kind, source_surface="scheduler",
        task_id=task_id, session_id=session_id,
    ))
    turn = run.begin_span(SpanContext(kind="turn", name=f"{usage_kind}.operation"))
    started = time.monotonic()
    last_error = None
    for index, (url, model, headers) in enumerate(candidates):
        attempt = run.begin_span(SpanContext(
            kind="model", name="model.generate", parent_span_id=turn.id,
            requested_model=candidates[0][1], actual_model=model,
            provider=_detect_provider(url),
            endpoint_id=provider_auth_id_for_endpoint(owner or "local", url),
            attributes={"attempt": index + 1},
        ))
        attempt_started = time.monotonic()
        try:
            result = await llm_call_async(url, model, messages, headers=headers, **kwargs)
            attempt.record_usage(UsageObservation(
                source="estimated", input_tokens=estimate_tokens(messages),
                output_tokens=max(len(result or "") // 4, 0),
            ))
            attempt.finish(SpanOutcome(duration_ms=round((time.monotonic() - attempt_started) * 1000)))
            duration = round((time.monotonic() - started) * 1000)
            turn.finish(SpanOutcome(duration_ms=duration))
            run.finish(RunOutcome(duration_ms=duration))
            return result
        except Exception as exc:
            last_error = exc
            attempt.finish(SpanOutcome(
                status="failed", outcome_code=type(exc).__name__,
                duration_ms=round((time.monotonic() - attempt_started) * 1000),
            ))
    duration = round((time.monotonic() - started) * 1000)
    turn.finish(SpanOutcome(status="failed", duration_ms=duration))
    run.finish(RunOutcome(status="failed", error_code=type(last_error).__name__ if last_error else "no_candidates", duration_ms=duration))
    if last_error:
        raise last_error
    raise RuntimeError("All background-task LLM candidates failed")
