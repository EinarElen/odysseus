from src.harness.context import apply_reconciled_context_to_prompt, reconcile_prompt_for_harness


def test_harness_context_reconciliation_is_stable():
    messages = [
        {"role": "system", "content": "System rules"},
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Reply"},
        {"role": "user", "content": "Now"},
    ]

    first = reconcile_prompt_for_harness(messages=messages, current_message="Now", harness_id="example")
    second = reconcile_prompt_for_harness(messages=messages, current_message="Now", harness_id="example")

    assert first["fingerprint"] == second["fingerprint"]
    assert first["serialized"] == second["serialized"]


def test_harness_context_reconciliation_excludes_current_user_turn():
    data = reconcile_prompt_for_harness(
        messages=[
            {"role": "system", "content": "System rules"},
            {"role": "user", "content": "Current request"},
        ],
        current_message="Current request",
        harness_id="example",
    )

    assert data["messages"] == [{"role": "system", "content": "System rules"}]


def test_harness_context_reconciliation_changes_only_when_context_changes():
    base = [
        {"role": "system", "content": "System rules"},
        {"role": "user", "content": "Now"},
    ]
    changed = [
        {"role": "system", "content": "Different system rules"},
        {"role": "user", "content": "Now"},
    ]

    first = reconcile_prompt_for_harness(messages=base, current_message="Now", harness_id="example")
    same = reconcile_prompt_for_harness(messages=base, current_message="Now", harness_id="example")
    second = reconcile_prompt_for_harness(messages=changed, current_message="Now", harness_id="example")

    assert first["fingerprint"] == same["fingerprint"]
    assert first["fingerprint"] != second["fingerprint"]


def test_harness_context_is_applied_without_mutating_empty_context_prompts():
    assert apply_reconciled_context_to_prompt(prompt="Hello", reconciliation={"messages": []}) == "Hello"

    data = reconcile_prompt_for_harness(
        messages=[{"role": "system", "content": "System rules"}, {"role": "user", "content": "Hello"}],
        current_message="Hello",
        harness_id="example",
    )
    prompt = apply_reconciled_context_to_prompt(prompt="Hello", reconciliation=data)

    assert prompt.startswith("<odysseus_context")
    assert prompt.endswith("\n\nHello")
