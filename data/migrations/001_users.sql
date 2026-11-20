-- 001: app users (plan §4.2, spec R1-R4b).
-- One email = one user. A user is linked to at most one driver and a driver to at most one user
-- (UNIQUE on driver_id gives R4a). Links are permanent: the app has no unlink statement (R4b).

CREATE TABLE app_users (
    user_id     TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,      -- stored trimmed and lower-cased (R2)
    driver_id   TEXT UNIQUE REFERENCES drivers(driver_id),  -- NULL until linked
    created_at  TEXT NOT NULL,
    linked_at   TEXT
);
