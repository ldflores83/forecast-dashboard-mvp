-- ============================================================
-- vw_waterfall
-- ARR movement components by quarter + FY
-- Logic:
--   - Renewals WON are NOT ARR movement (retained ARR already
--     exists in Starting ARR — renewals protect, not grow ARR)
--   - Only Net New, Expansion, Migration add new ARR
--   - Churn = ATR_Value of Closed-Lost renewals (clean filter)
--   - Substage filter matches SF renewal dashboard logic
--   - Net New / Expansion / Migration use split_solutions_acv
--     when available (COALESCE fallback to ACV_USD)
--   - Renewals and Cloud Conversion always use ACV_USD / ATR_Value_USD
-- Starting ARR = Tableau snapshot February 2026
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_waterfall` AS

WITH base_filter AS (
  SELECT *
  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
    AND UPPER(Name) NOT LIKE '%REBILL%'
    AND UPPER(Name) NOT LIKE '%RE-INVOICE%'
    AND UPPER(Name) NOT LIKE '%REINVOICE%'
    AND UPPER(Name) NOT LIKE '%RE INVOICE%'
    AND Type != 'Admin $0'
),

movements AS (
  SELECT
    o.FiscalQuarter  AS fiscal_quarter,
    o.FiscalYear     AS fiscal_year,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Net New'
                       AND o.Category = 'Solutions'
                       AND o.Is_Channel = FALSE
                       AND o.Is_Won
                      THEN s.split_solutions_acv END), 0) AS net_new,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions'
                       AND o.Is_Channel = FALSE
                       AND o.Is_Won
                      THEN s.split_solutions_acv END), 0) AS expansion,
    GREATEST(COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Migration'
                               AND o.Category = 'Solutions'
                               AND o.Is_Channel = FALSE
                               AND o.Is_Won
                              THEN s.split_solutions_acv END), 0), 0) AS migration,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal'
                       AND o.Substage = 'Closed - Conversion'
                       AND o.Is_Won = TRUE
                       THEN o.ATR_Value_USD END), 0)                            AS cloud_conversion,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal'
                       AND o.Is_Lost
                       AND o.ATR_Value_USD > 0
                       THEN o.ATR_Value_USD END), 0)                            AS churn_arr,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Won
                       THEN o.ACV_USD END), 0)                                  AS renewal_won_acv,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.Is_Won)                            AS renewal_won_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.Is_Lost AND o.ATR_Value_USD > 0)   AS renewal_lost_count
  FROM base_filter o
  LEFT JOIN `forecast-dashboard-mvp.forecast_data.opportunity_splits` s
    ON o.Id = s.opportunity_id
    AND s.split_type = 'Solutions Revenue'
  GROUP BY o.FiscalQuarter, o.FiscalYear
),

fy AS (
  SELECT
    0                AS fiscal_quarter,
    o.FiscalYear     AS fiscal_year,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Net New'
                       AND o.Category = 'Solutions'
                       AND o.Is_Channel = FALSE
                       AND o.Is_Won
                      THEN s.split_solutions_acv END), 0) AS net_new,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions'
                       AND o.Is_Channel = FALSE
                       AND o.Is_Won
                      THEN s.split_solutions_acv END), 0) AS expansion,
    GREATEST(COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Migration'
                               AND o.Category = 'Solutions'
                               AND o.Is_Channel = FALSE
                               AND o.Is_Won
                              THEN s.split_solutions_acv END), 0), 0) AS migration,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal'
                       AND o.Substage = 'Closed - Conversion'
                       AND o.Is_Won = TRUE
                       THEN o.ATR_Value_USD END), 0)                            AS cloud_conversion,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal'
                       AND o.Is_Lost
                       AND o.ATR_Value_USD > 0
                       THEN o.ATR_Value_USD END), 0)                            AS churn_arr,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Won
                       THEN o.ACV_USD END), 0)                                  AS renewal_won_acv,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.Is_Won)                            AS renewal_won_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.Is_Lost AND o.ATR_Value_USD > 0)   AS renewal_lost_count
  FROM base_filter o
  LEFT JOIN `forecast-dashboard-mvp.forecast_data.opportunity_splits` s
    ON o.Id = s.opportunity_id
    AND s.split_type = 'Solutions Revenue'
  GROUP BY o.FiscalYear
)

SELECT
  fiscal_quarter,
  fiscal_year,
  436700000.0                                                    AS starting_arr,  -- UPDATE THIS: Starting ARR snapshot from Tableau, update each fiscal year
  net_new,
  expansion,
  migration,
  cloud_conversion,
  -churn_arr                                                     AS churn,
  renewal_won_acv,
  renewal_won_count,
  renewal_lost_count,
  436700000.0 + net_new + expansion + migration + cloud_conversion - churn_arr AS ending_arr,  -- UPDATE THIS: Starting ARR snapshot from Tableau, update each fiscal year
  net_new + expansion + migration + cloud_conversion - churn_arr                AS net_growth,
  net_new + expansion + migration                                                AS sales_new_arr,
  SAFE_DIVIDE(net_new + expansion + migration, NULLIF(churn_arr, 0)) * 100      AS sales_coverage_pct
FROM (SELECT * FROM movements UNION ALL SELECT * FROM fy)
ORDER BY fiscal_quarter
;
