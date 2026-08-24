from __future__ import annotations

import pytest

from travel_agent.execution.budget import ExecutionLedger
from travel_agent.execution.errors import ExecutionBudgetExceeded
from travel_agent.execution.models import ExecutionBudget


def test_budget_counts_logical_calls_attempts_cache_and_unknown_tokens():
    ledger = ExecutionLedger(
        "run-budget",
        ExecutionBudget(
            max_graph_steps=10,
            terminal_step_reserve=2,
            max_tool_calls=2,
            max_provider_attempts=3,
            max_llm_calls=1,
            max_llm_attempts=2,
        ),
    )

    ledger.consume_graph_step()
    ledger.consume_tool_call()
    ledger.consume_provider_attempt()
    ledger.note_cache_hit()
    ledger.consume_llm_call(10)
    ledger.consume_llm_attempt()
    ledger.complete_llm(None, None)

    usage = ledger.snapshot()
    assert usage.graph_steps == 1
    assert usage.tool_calls == 1
    assert usage.provider_attempts == 1
    assert usage.cache_hits == 1
    assert usage.input_tokens is None
    assert usage.output_tokens is None


def test_budget_rejects_side_effect_before_exceeding_limit():
    ledger = ExecutionLedger(
        "run-limit",
        ExecutionBudget(
            max_tool_calls=1,
            max_provider_attempts=1,
        ),
    )
    ledger.consume_tool_call()

    with pytest.raises(ExecutionBudgetExceeded) as raised:
        ledger.consume_tool_call()

    assert raised.value.limit == "max_tool_calls"
    assert ledger.snapshot().tool_calls == 1


def test_terminal_steps_use_reserved_capacity():
    ledger = ExecutionLedger(
        "run-terminal",
        ExecutionBudget(max_graph_steps=4, terminal_step_reserve=2),
    )
    ledger.consume_graph_step()
    ledger.consume_graph_step()
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.consume_graph_step()
    ledger.consume_graph_step(terminal=True)
    ledger.consume_graph_step(terminal=True)
    assert ledger.snapshot().graph_steps == 4


def test_repeated_action_fingerprint_is_bounded_across_repair_loops():
    ledger = ExecutionLedger(
        "run-fingerprint",
        ExecutionBudget(max_repeated_fingerprint_count=2),
    )
    ledger.note_action_fingerprint("repair-a")
    ledger.note_action_fingerprint("repair-a")

    with pytest.raises(ExecutionBudgetExceeded) as raised:
        ledger.note_action_fingerprint("repair-a")

    assert raised.value.limit == "max_repeated_fingerprint_count"
    ledger.note_action_fingerprint("repair-b")
