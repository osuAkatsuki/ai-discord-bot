ALTER TABLE threads
    ADD COLUMN cloud_execution BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN execution_session_id TEXT,
    ADD COLUMN execution_cost_usd NUMERIC(18,10) NOT NULL DEFAULT 0;
