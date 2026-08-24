from __future__ import annotations

from datetime import datetime, timezone

from travel_agent.evaluation.trajectory import trace_is_complete
from travel_agent.execution.models import TraceEvent, TraceEventType


def test_trace_completeness_accepts_node_completion_and_run_terminal():
    events = (
        _event(1, TraceEventType.RUN_STARTED, "r1"),
        _event(2, TraceEventType.NODE_STARTED, "n1"),
        _event(3, TraceEventType.NODE_COMPLETED, "n2", parent="n1"),
        _event(4, TraceEventType.RUN_COMPLETED, "r2"),
    )
    assert trace_is_complete(events)


def test_trace_completeness_rejects_orphan_node():
    events = (
        _event(1, TraceEventType.RUN_STARTED, "r1"),
        _event(2, TraceEventType.NODE_STARTED, "n1"),
        _event(3, TraceEventType.RUN_FAILED, "r2"),
    )
    assert not trace_is_complete(events)


def _event(sequence, event_type, event_id, parent=None):
    return TraceEvent(
        event_id=event_id,
        run_id="run",
        sequence=sequence,
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        monotonic_offset_ms=sequence,
        status="ok",
        parent_event_id=parent,
    )
