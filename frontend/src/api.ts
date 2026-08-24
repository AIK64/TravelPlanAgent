export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export type AgentResponse = {
  status: string;
  thread_id?: string;
  session_id?: string;
  interrupt?: { id: string; payload: { questions?: string[] } };
  selected_plan?: unknown;
  candidates?: unknown[];
  [key: string]: unknown;
};

export type TraceEvent = {
  event_id: string;
  sequence: number;
  event_type: string;
  status: string;
  node?: string;
  operation?: string;
  duration_ms?: number;
  attributes: Record<string, string | number | boolean | null>;
};

const identityHeaders = {
  "X-Tenant-Id": import.meta.env.VITE_DEV_TENANT_ID ?? "local",
  "X-User-Id": import.meta.env.VITE_DEV_USER_ID ?? "demo",
};

async function request<T>(path: string, init?: RequestInit): Promise<{ data: T; response: Response }> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...identityHeaders, ...init?.headers },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.detail?.message ?? data?.message ?? `HTTP ${response.status}`);
  return { data, response };
}

export async function planFromText(query: string) {
  return request<AgentResponse>("/api/v1/plans/from-text", {
    method: "POST",
    body: JSON.stringify({ text: query }),
  });
}

export async function resumePlan(threadId: string, interruptId: string, answer: string) {
  return request<AgentResponse>(`/api/v1/plans/from-text/${threadId}/resume`, {
    method: "POST",
    body: JSON.stringify({
      interrupt_id: interruptId,
      request_id: crypto.randomUUID(),
      answer,
    }),
  });
}

export async function getTrace(runId: string, after = 0) {
  return request<{ events: TraceEvent[] }>(`/api/v1/runs/${runId}/trace?after_sequence=${after}&limit=500`);
}

export async function getPreferences() {
  return request<{ items: Preference[]; personalization: { enabled: boolean; revision: number } }>("/api/v1/preferences");
}

export type Preference = {
  memory_id: string;
  category: string;
  value: unknown;
  confidence: number;
  confirmation_status: string;
  revision: number;
  revoked_at?: string;
};

export async function createPreference(category: string, value: unknown) {
  return request<Preference>("/api/v1/preferences", {
    method: "POST",
    body: JSON.stringify({ category, value, scope: "global" }),
  });
}

export async function deletePreference(memoryId: string) {
  return request<void>(`/api/v1/preferences/${memoryId}`, { method: "DELETE" });
}

export async function setPersonalization(enabled: boolean, revision: number) {
  return request(`/api/v1/profile/personalization`, {
    method: "PATCH",
    body: JSON.stringify({ enabled, expected_revision: revision }),
  });
}
