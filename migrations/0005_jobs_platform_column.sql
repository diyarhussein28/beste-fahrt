-- Needed now that the collector polls several platforms at once — القسم 12
-- extension: the manager wants every account he has, not just one, watched
-- concurrently. Without this, two platforms reusing the same numbering
-- scheme for platform_id could theoretically collide (job_fingerprint()
-- now namespaces by platform precisely to avoid that), and there'd be no
-- way to tell which platform a job actually came from.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_jobs_platform ON jobs (platform);

-- Job.required_license existed on the Pydantic model since the return-trip
-- work but was never actually persisted — upsert_job() silently dropped it
-- and get_job() always reconstructed it as NULL. Invisible until now
-- because demo_parser never set it; movacarpro_parser.py does (its
-- Zusatzqualifikation/Rotes-Nummernschild/Anhänger tags), so it needs a
-- real column instead of only surviving inside a single process's memory.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS required_license TEXT;
