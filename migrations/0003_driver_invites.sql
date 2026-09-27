-- Lets the manager onboard a new driver from inside Telegram (/add_driver)
-- instead of needing the driver's chat_id up front: a one-time code is
-- turned into a t.me/<bot>?start=<code> deep link, and /start consumes it.
CREATE TABLE IF NOT EXISTS driver_invites (
    code       TEXT PRIMARY KEY,
    driver_id  INT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);
