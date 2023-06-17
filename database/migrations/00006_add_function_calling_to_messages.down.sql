ALTER TABLE thread_messages DROP COLUMN function_args;
ALTER TABLE thread_messages DROP COLUMN function_name;
ALTER TABLE thread_messages ALTER COLUMN content SET NOT NULL;
