import type { TraceEvent } from "../api";

export function TracePanel({ events }: { events: TraceEvent[] }) {
  return <section className="panel trace-panel">
    <div className="panel-title"><span>执行轨迹</span><small>{events.length} events</small></div>
    {events.length === 0 ? <p className="empty">提交规划后，这里会展示 Node、Tool、Handoff 与降级事件。</p> :
      <ol className="timeline">{events.map(event => <li key={event.event_id}>
        <span className={`event-dot ${event.status}`} />
        <div><strong>{event.event_type}</strong><small>#{event.sequence} · {event.node ?? event.operation ?? "run"}{event.duration_ms != null ? ` · ${event.duration_ms}ms` : ""}</small></div>
      </li>)}</ol>}
  </section>;
}
