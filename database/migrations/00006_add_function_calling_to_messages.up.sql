ALTER TABLE thread_messages ALTER COLUMN content DROP NOT NULL;
ALTER TABLE thread_messages ADD COLUMN function_name TEXT NULL;
ALTER TABLE thread_messages ADD COLUMN function_args JSONB NULL;
