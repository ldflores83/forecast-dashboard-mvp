-- ============================================================
-- vw_opportunity_splits
-- One clean Solutions Revenue split per opportunity.
-- split_solutions_acv = Total_Bookings_Net__c * split_pct / 100
-- computed in sf_export_dashboard.py at fetch time.
-- Picks highest split_solutions_acv when duplicates exist.
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_opportunity_splits` AS
SELECT
  opportunity_id,
  split_owner_name,
  split_solutions_acv,
  split_pct,
  fiscal_year,
  close_date,
  ROW_NUMBER() OVER (
    PARTITION BY opportunity_id
    ORDER BY split_solutions_acv DESC
  ) AS rn
FROM `forecast-dashboard-mvp.forecast_data.opportunity_splits`
WHERE split_type = 'Solutions Revenue'
  AND split_solutions_acv > 0
QUALIFY rn = 1
;
