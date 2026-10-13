ALTER TABLE threads ADD COLUMN reasoning_effort TEXT;
ALTER TABLE threads ADD COLUMN web_search BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE thread_messages ADD COLUMN content_parts JSONB;

-- One row per provider call, including intermediate tool rounds.
-- No thread FK: /query, /summarize and /image also create requests.
CREATE TABLE ai_requests (
    request_id BIGSERIAL PRIMARY KEY,
    thread_id BIGINT NOT NULL,
    requester_id BIGINT NOT NULL,
    model TEXT NOT NULL,
    usage JSONB,
    estimated_cost_usd NUMERIC(18, 10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ai_requests_thread_id_idx ON ai_requests (thread_id);
CREATE INDEX ai_requests_created_at_idx ON ai_requests (created_at);
