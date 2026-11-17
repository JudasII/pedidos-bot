-- Delivery asks, for each due message, whether its chat has an older one still pending.
CREATE INDEX outbox_pending_by_chat ON outbox (chat_ref, id)
    WHERE sent_at IS NULL AND failed_at IS NULL;
