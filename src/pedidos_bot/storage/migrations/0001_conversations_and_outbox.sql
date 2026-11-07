CREATE TABLE conversations (
    channel text NOT NULL,
    chat_ref text NOT NULL,
    state jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel, chat_ref)
);

CREATE TABLE inbound_messages (
    channel text NOT NULL,
    external_id text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel, external_id)
);

CREATE TABLE outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dedupe_key text NOT NULL UNIQUE,
    chat_ref text NOT NULL,
    message jsonb NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX outbox_pending ON outbox (next_attempt_at) WHERE sent_at IS NULL;
