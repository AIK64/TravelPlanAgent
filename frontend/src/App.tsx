import { FormEvent, useMemo, useState } from "react";
import { AgentResponse, getTrace, planFromText, resumePlan, TraceEvent } from "./api";
import { MapPanel, MapPoint } from "./components/MapPanel";
import { PreferencePanel } from "./components/PreferencePanel";
import { TracePanel } from "./components/TracePanel";

const example = "我和父母 2026 年 10 月 2 日上午到杭州东站，4 日晚离开，住西湖东侧。预算 1500 元，想看自然和人文景点，节奏轻松，少走路，灵隐寺必须去。";

function collectPoints(value: unknown): MapPoint[] {
  const result: MapPoint[] = [];
  const walk = (node: unknown) => {
    if (!node || typeof node !== "object") return;
    const item = node as Record<string, unknown>;
    const coordinate = item.coordinate as Record<string, unknown> | undefined;
    if (coordinate && typeof coordinate.longitude === "number" && typeof coordinate.latitude === "number") result.push({ name: String(item.name ?? "POI"), longitude: coordinate.longitude, latitude: coordinate.latitude });
    Object.values(item).forEach(child => Array.isArray(child) ? child.forEach(walk) : walk(child));
  };
  walk(value); return result.filter((point, index, all) => all.findIndex(other => other.name === point.name && other.longitude === point.longitude) === index);
}

export default function App() {
  const [query, setQuery] = useState(example);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<AgentResponse | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const points = useMemo(() => collectPoints(result), [result]);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const { data, response } = await planFromText(query);
      const id = response.headers.get("X-Agent-Run-Id");
      setResult(data); setThreadId(data.thread_id ?? null); setRunId(id);
      if (id) setEvents((await getTrace(id)).data.events);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function clarify() {
    if (!threadId) return; setBusy(true); setError(null);
    try {
      if (!result?.interrupt) return;
      const { data, response } = await resumePlan(
        threadId, result.interrupt.id, answer,
      );
      const id = response.headers.get("X-Agent-Run-Id"); setResult(data); setRunId(id);
      if (id) setEvents((await getTrace(id)).data.events);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  return <main>
    <header><div className="brand-mark">TA</div><div><p>AGENT ENGINEERING STUDIO</p><h1>Travel Agent</h1></div><span className="status"><i /> graph online</span></header>
    <div className="hero"><div><span className="eyebrow">Plan → Tool Use → Validate → Replan</span><h2>把旅行约束，变成可验证的计划。</h2><p>偏好记忆、工具事实、Critic 与局部重规划都留在可观察轨迹里。</p></div><div className="hero-stat"><strong>{events.length}</strong><span>TRACE EVENTS</span></div></div>
    <div className="workspace">
      <div className="left-column">
        <section className="panel composer"><div className="panel-title"><span>旅行需求</span><small>自然语言</small></div>
          <form onSubmit={submit}><textarea value={query} onChange={e => setQuery(e.target.value)} /><div className="composer-actions"><span>Memory 会在 Graph 内按相关性裁剪</span><button disabled={busy}>{busy ? "Agent 运行中…" : "生成旅行计划 →"}</button></div></form>
          {error && <p className="error">{error}</p>}
          {result?.interrupt && <div className="clarify"><strong>需要你补充</strong><p>{(result.interrupt.payload.questions ?? []).join("；")}</p><div><input value={answer} onChange={e => setAnswer(e.target.value)} /><button onClick={() => void clarify()}>继续运行</button></div></div>}
        </section>
        <section className="panel plan-panel"><div className="panel-title"><span>候选与决策</span><small>{result?.status ?? "等待输入"}</small></div>
          {!result ? <p className="empty">候选日程、硬约束验证、软评分与推荐理由会出现在这里。</p> : <pre>{JSON.stringify(result.planning ?? { message: result.message, issues: result.issues }, null, 2)}</pre>}
        </section>
        <PreferencePanel />
      </div>
      <div className="right-column"><MapPanel points={points} /><TracePanel events={events} />{runId && <div className="run-chip">run_id <code>{runId}</code></div>}</div>
    </div>
  </main>;
}
