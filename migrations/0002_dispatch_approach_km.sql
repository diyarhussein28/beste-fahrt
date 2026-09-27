-- Needed for KPIs in القسم 15 (الكم الفارغ لكل رحلة، الإيراد لكل كم):
-- without recording the approach distance at dispatch time, those averages
-- would need re-deriving driver location history after the fact.
ALTER TABLE dispatches ADD COLUMN IF NOT EXISTS approach_km NUMERIC(7, 1);
