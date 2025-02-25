CREATE TABLE personal_messages (
    id INT NOT NULL PRIMARY KEY, -- Differs from message_id as it includes responses
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    role TEXT NOT NULL,
    -- Perhaps use a "source" enum to differentiate between user input and bot output?
    -- Will let us use injecting system messages.
    tokens_used INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    INDEX ON (user_id)
);
