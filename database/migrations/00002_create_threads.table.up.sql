CREATE TABLE threads (
    thread_id BIGINT NOT NULL PRIMARY KEY, -- not autoincr because we don't create it
    initiator_user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
