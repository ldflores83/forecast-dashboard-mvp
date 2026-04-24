-- ============================================================
-- vw_waterfall
-- ARR movement components by quarter + FY
-- Logic:
--   - Renewals WON are NOT ARR movement (retained ARR already
--     exists in Starting ARR — renewals protect, not grow ARR)
--   - Only Net New, Expansion, Migration add new ARR
--   - Churn = ATR_Value of Closed-Lost renewals (clean filter)
--   - Substage filter matches SF renewal dashboard logic
-- Starting ARR = Tableau snapshot February 2026
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_waterfall` AS

WITH base_filter AS (
  SELECT *
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
),

movements AS (
  SELECT
    FiscalQuarter  AS fiscal_quarter,
    FiscalYear     AS fiscal_year,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Net New'   AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0)               AS net_new,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0)               AS expansion,
    GREATEST(COALESCE(SUM(CASE WHEN Sales_Motion = 'Migration' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0), 0)  AS migration,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0 THEN ATR_Value END), 0) AS churn_arr,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Won THEN ACV END), 0)                 AS renewal_won_acv,
    COUNTIF(Sales_Motion = 'Renewal' AND Is_Won)                                                  AS renewal_won_count,
    COUNTIF(Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0)                              AS renewal_lost_count
  FROM base_filter
  GROUP BY FiscalQuarter, FiscalYear
),

fy AS (
  SELECT
    0              AS fiscal_quarter,
    FiscalYear     AS fiscal_year,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Net New'   AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0)               AS net_new,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0)               AS expansion,
    GREATEST(COALESCE(SUM(CASE WHEN Sales_Motion = 'Migration' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0), 0)  AS migration,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0 THEN ATR_Value END), 0) AS churn_arr,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Won THEN ACV END), 0)                 AS renewal_won_acv,
    COUNTIF(Sales_Motion = 'Renewal' AND Is_Won)                                                  AS renewal_won_count,
    COUNTIF(Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0)                              AS renewal_lost_count
  FROM base_filter
  GROUP BY FiscalYear
)

SELECT
  fiscal_quarter,
  fiscal_year,
  436700000.0                                                   AS starting_arr,
  net_new,
  expansion,
  migration,
  -churn_arr                                                    AS churn,
  renewal_won_acv,
  renewal_won_count,
  renewal_lost_count,
  436700000.0 + net_new + expansion + migration - churn_arr    AS ending_arr,
  net_new + expansion + migration - churn_arr                   AS net_growth
FROM (SELECT * FROM movements UNION ALL SELECT * FROM fy)
ORDER BY fiscal_quarter
;