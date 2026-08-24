CREATE TABLE IF NOT EXISTS plan_sessions (
    session_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    revision integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_owner ON plan_sessions(tenant_id,user_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    status text NOT NULL,
    kind text NOT NULL,
    thread_id text,
    session_id text,
    request_id text,
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_owner ON agent_runs(tenant_id,user_id,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_thread ON agent_runs(thread_id,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session ON agent_runs(session_id,started_at DESC);

CREATE TABLE IF NOT EXISTS trace_events (
    run_id text NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    sequence integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(run_id,sequence)
);

CREATE TABLE IF NOT EXISTS preference_memories (
    memory_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    content_hash text NOT NULL,
    revision integer NOT NULL,
    updated_at timestamptz NOT NULL,
    revoked_at timestamptz,
    payload jsonb NOT NULL,
    UNIQUE(tenant_id,user_id,content_hash)
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON preference_memories(tenant_id,user_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    status text NOT NULL,
    updated_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS personalization_settings (
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    revision integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(tenant_id,user_id)
);
