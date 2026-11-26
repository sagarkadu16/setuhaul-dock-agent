-- Tables owned by the app (the seed schema is owned by the client and left as-is).

-- Login identity: one email per driver. Linked by driver_id, resolved from phone at load time.
CREATE TABLE IF NOT EXISTS app_users (
    email       TEXT PRIMARY KEY COLLATE NOCASE,
    driver_id   TEXT NOT NULL UNIQUE REFERENCES drivers(driver_id),
    cognito_sub TEXT UNIQUE,
    created_at  TEXT NOT NULL
);


-- A driver's request for a contested slot. Waits until decide_after, then is ranked.
-- Times here are SYSTEM time in UTC (real elapsed time), not scenario time.
CREATE TABLE IF NOT EXISTS slot_requests (
    request_seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id          TEXT NOT NULL REFERENCES appointment_slots(slot_id),
    shipment_id      TEXT NOT NULL REFERENCES shipments(shipment_id),
    driver_id        TEXT NOT NULL REFERENCES drivers(driver_id),
    requested_at     TEXT NOT NULL,
    decide_after     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'WON', 'LOST', 'FAILED')),
    appointment_id   TEXT REFERENCES appointments(appointment_id),
    offered_slot_id  TEXT REFERENCES appointment_slots(slot_id),
    decision_note    TEXT,
    decided_at       TEXT
);

-- One open request per shipment: a driver can't queue for two slots at once.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_request_per_shipment
    ON slot_requests(shipment_id) WHERE status = 'PENDING';

-- Fast lookup of "pending requests for this slot".
CREATE INDEX IF NOT EXISTS ix_slot_requests_slot_status
    ON slot_requests(slot_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS ux_chat_message_external_id
    ON chat_messages(thread_id, external_message_id)
    WHERE external_message_id IS NOT NULL;
