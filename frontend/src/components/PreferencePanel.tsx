import { FormEvent, useEffect, useState } from "react";
import { createPreference, deletePreference, getPreferences, Preference, setPersonalization } from "../api";

export function PreferencePanel() {
  const [items, setItems] = useState<Preference[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [revision, setRevision] = useState(0);
  const [category, setCategory] = useState("pace");
  const [value, setValue] = useState("relaxed");

  async function refresh() {
    const { data } = await getPreferences();
    setItems(data.items); setEnabled(data.personalization.enabled); setRevision(data.personalization.revision);
  }
  useEffect(() => { void refresh(); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); await createPreference(category, value); await refresh();
  }
  async function toggle() {
    const { data } = await setPersonalization(!enabled, revision) as { data: { enabled: boolean; revision: number } };
    setEnabled(data.enabled); setRevision(data.revision);
  }
  return <section className="panel memory-panel">
    <div className="panel-title"><span>Preference Memory</span><button className={`switch ${enabled ? "on" : ""}`} onClick={() => void toggle()}>{enabled ? "个性化开启" : "个性化关闭"}</button></div>
    <form className="memory-form" onSubmit={submit}>
      <select value={category} onChange={e => setCategory(e.target.value)}>
        <option value="pace">行程节奏</option><option value="preferred_categories">偏好类别</option><option value="avoided_categories">避开类别</option><option value="walking_tolerance">步行容忍度</option><option value="food_preferences">饮食偏好</option>
      </select>
      <input value={value} onChange={e => setValue(e.target.value)} aria-label="偏好值" />
      <button type="submit">保存显式偏好</button>
    </form>
    <div className="memory-list">{items.map(item => <article key={item.memory_id}>
      <div><strong>{item.category}</strong><span>{JSON.stringify(item.value)}</span></div>
      <small>{item.confirmation_status} · confidence {item.confidence.toFixed(2)} · rev {item.revision}</small>
      <button onClick={() => void deletePreference(item.memory_id).then(refresh)}>删除</button>
    </article>)}</div>
  </section>;
}
