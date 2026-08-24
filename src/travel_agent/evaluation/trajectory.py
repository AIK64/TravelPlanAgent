from __future__ import annotations

from travel_agent.execution.models import TraceEvent, TraceEventType


def trace_is_complete(events: tuple[TraceEvent, ...]) -> bool:
    if not events:
        return False
    if sum(event.event_type is TraceEventType.RUN_STARTED for event in events) != 1:
        return False
    terminal = {
        TraceEventType.RUN_COMPLETED,
        TraceEventType.RUN_INTERRUPTED,
        TraceEventType.RUN_FAILED,
        TraceEventType.RUN_REPLAYED,
    }
    if sum(event.event_type in terminal for event in events) != 1:
        return False
    sequences = [event.sequence for event in events]
    if sequences != list(range(1, len(events) + 1)):
        return False
    started = {
        event.event_id: event
        for event in events
        if event.event_type is TraceEventType.NODE_STARTED
    }
    terminal_parents = {
        event.parent_event_id
        for event in events
        if event.event_type
        in {
            TraceEventType.NODE_COMPLETED,
            TraceEventType.NODE_FAILED,
            TraceEventType.INTERRUPT_CREATED,
        }
    }
    return set(started) <= terminal_parents


def contains_ordered_events(
    events: tuple[TraceEvent, ...], expected: tuple[TraceEventType, ...]
) -> bool:
    cursor = 0
    for event in events:
        if cursor < len(expected) and event.event_type is expected[cursor]:
            cursor += 1
    return cursor == len(expected)
