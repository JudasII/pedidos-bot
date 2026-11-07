CREATE TABLE orders (
    id text PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    conversation_id text,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
