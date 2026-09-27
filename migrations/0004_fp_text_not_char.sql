-- CHAR(64) was meant for the SHA-256 fallback fingerprint (always exactly
-- 64 hex chars), but job_fingerprint() also returns the platform's own id
-- verbatim when one exists (short strings like "demo-392989" or a real
-- platform's job id). Postgres right-pads CHAR(n) values to the full
-- length on storage, and asyncpg returns that padding as-is (psql's
-- display and SQL length() hide it, which is why this wasn't obvious) — so
-- every short fp ends up ~53 trailing spaces longer than it looks. That
-- broke Telegram's inline button callback_data (64-byte limit) with
-- "Button_data_invalid" the moment a job had a short platform id.
--
-- Existing rows (all demo/test data at this point) are truncated rather
-- than trimmed in place, since trimming the padding in `jobs.fp` while
-- `dispatches.job_fp` / `return_watches.*_fp` still hold the padded value
-- would violate the foreign keys before both sides are updated together.

ALTER TABLE dispatches DROP CONSTRAINT IF EXISTS dispatches_job_fp_fkey;
ALTER TABLE return_watches DROP CONSTRAINT IF EXISTS return_watches_outbound_fp_fkey;
ALTER TABLE return_watches DROP CONSTRAINT IF EXISTS return_watches_matched_fp_fkey;

TRUNCATE dispatches, return_watches, jobs;

ALTER TABLE jobs ALTER COLUMN fp TYPE TEXT;
ALTER TABLE dispatches ALTER COLUMN job_fp TYPE TEXT;
ALTER TABLE return_watches ALTER COLUMN outbound_fp TYPE TEXT;
ALTER TABLE return_watches ALTER COLUMN matched_fp TYPE TEXT;

ALTER TABLE dispatches
    ADD CONSTRAINT dispatches_job_fp_fkey FOREIGN KEY (job_fp) REFERENCES jobs(fp) ON DELETE CASCADE;
ALTER TABLE return_watches
    ADD CONSTRAINT return_watches_outbound_fp_fkey FOREIGN KEY (outbound_fp) REFERENCES jobs(fp) ON DELETE CASCADE;
ALTER TABLE return_watches
    ADD CONSTRAINT return_watches_matched_fp_fkey FOREIGN KEY (matched_fp) REFERENCES jobs(fp);
