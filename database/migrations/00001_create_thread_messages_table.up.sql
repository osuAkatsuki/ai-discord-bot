CREATE TABLE thread_messages (
    thread_message_id BIGSERIAL NOT NULL PRIMARY KEY,
    thread_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    role TEXT NOT NULL,
    tokens_used INT NOT NULL,
    -- TODO: could we allow & handle editing of past messages
    --       when they're updated? editable context could be pretty
    --       cool, but we can't guarantee 100% uptime to ensure we
    --       receive all message updates either.
    -- TODO: should we handle message deletion?
    --       same uptime concern as above
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
