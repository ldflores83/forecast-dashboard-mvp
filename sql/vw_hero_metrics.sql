-- ============================================================
-- vw_hero_metrics
-- Hero KPIs: renewal win rate, sales coverage of churn,
-- open expansion pipeline, lost renewals (churn)
-- Granularity: one row per fiscal_quarter + one row for FY (quarter = 0)
-- Sales metrics use split_solutions_acv (no ACV_USD fallback)
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_hero_metrics` AS

WITH base AS (
  SELECT
    o.FiscalQuarter                                         AS fiscal_quarter,
    o.FiscalYear                                            AS fiscal_year,

    -- Renewal counts
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Won)     AS renewal_won_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Lost)    AS renewal_lost_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed)                  AS renewal_closed_count,

    -- Renewal ACV
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Won
                      THEN o.ACV_USD END), 0)                           AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Lost AND o.ATR_Value_USD > 0
                      THEN o.ATR_Value_USD END), 0)                     AS renewal_lost_acv,

    -- Sales new revenue: split_solutions_acv (no fallback)
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS sales_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Net New'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Migration'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS migration_won_acv,

    -- Open expansion pipeline (ACV_USD — split attribution not needed)
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions' AND o.Is_Open
                      THEN o.ACV_USD END), 0)                           AS expansion_open_acv,
    COUNTIF(o.Sales_Motion = 'Expansion' AND o.Category = 'Solutions' AND o.Is_Open) AS expansion_open_count,

    -- Totals
    COALESCE(SUM(CASE WHEN o.Is_Won  THEN o.ACV_USD END), 0)           AS total_won_acv,
    COALESCE(SUM(CASE WHEN o.Is_Lost THEN o.ACV_USD END), 0)           AS total_lost_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open THEN o.ACV_USD END), 0)           AS total_open_acv,
    COUNT(*)                                                            AS total_opps,
    COUNTIF(o.Is_Won)                                                   AS won_opps,
    COUNTIF(o.Is_Lost)                                                  AS lost_opps,
    COUNTIF(o.Is_Open)                                                  AS open_opps

  FROM `forecast-dashboard-mvp.forecast_data.opportunities` o
  LEFT JOIN `forecast-dashboard-mvp.forecast_data.opportunity_splits` s
    ON o.Id = s.opportunity_id
    AND s.split_type = 'Solutions Revenue'
  WHERE o.BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND o.Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND o.Name NOT LIKE '%Amendment%'
    AND o.Name NOT LIKE '%zzz%'
    AND UPPER(o.Name) NOT LIKE '%REBILL%'
    AND UPPER(o.Name) NOT LIKE '%RE-INVOICE%'
    AND UPPER(o.Name) NOT LIKE '%REINVOICE%'
    AND UPPER(o.Name) NOT LIKE '%RE INVOICE%'
    AND o.Type != 'Admin $0'
  GROUP BY o.FiscalQuarter, o.FiscalYear
),

fy AS (
  SELECT
    0                                                             AS fiscal_quarter,
    o.FiscalYear                                                  AS fiscal_year,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Won)     AS renewal_won_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Lost)    AS renewal_lost_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed)                  AS renewal_closed_count,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Won
                      THEN o.ACV_USD END), 0)                           AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Lost AND o.ATR_Value_USD > 0
                      THEN o.ATR_Value_USD END), 0)                     AS renewal_lost_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS sales_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Net New'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Migration'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)               AS migration_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions' AND o.Is_Open
                      THEN o.ACV_USD END), 0)                           AS expansion_open_acv,
    COUNTIF(o.Sales_Motion = 'Expansion' AND o.Category = 'Solutions' AND o.Is_Open) AS expansion_open_count,
    COALESCE(SUM(CASE WHEN o.Is_Won  THEN o.ACV_USD END), 0)           AS total_won_acv,
    COALESCE(SUM(CASE WHEN o.Is_Lost THEN o.ACV_USD END), 0)           AS total_lost_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open THEN o.ACV_USD END), 0)           AS total_open_acv,
    COUNT(*)                                                            AS total_opps,
    COUNTIF(o.Is_Won)                                                   AS won_opps,
    COUNTIF(o.Is_Lost)                                                  AS lost_opps,
    COUNTIF(o.Is_Open)                                                  AS open_opps
  FROM `forecast-dashboard-mvp.forecast_data.opportunities` o
  LEFT JOIN `forecast-dashboard-mvp.forecast_data.opportunity_splits` s
    ON o.Id = s.opportunity_id
    AND s.split_type = 'Solutions Revenue'
  WHERE o.BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND o.Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND o.Name NOT LIKE '%Amendment%'
    AND o.Name NOT LIKE '%zzz%'
    AND UPPER(o.Name) NOT LIKE '%REBILL%'
    AND UPPER(o.Name) NOT LIKE '%RE-INVOICE%'
    AND UPPER(o.Name) NOT LIKE '%REINVOICE%'
    AND UPPER(o.Name) NOT LIKE '%RE INVOICE%'
    AND o.Type != 'Admin $0'
  GROUP BY o.FiscalYear
)

SELECT
  fiscal_quarter,
  fiscal_year,
  renewal_won_count,
  renewal_lost_count,
  renewal_closed_count,
  renewal_won_acv,
  renewal_lost_acv,
  sales_won_acv,
  net_new_won_acv,
  expansion_won_acv,
  GREATEST(migration_won_acv, 0)                                            AS migration_won_acv,
  expansion_open_acv,
  expansion_open_count,
  total_won_acv,
  total_lost_acv,
  total_open_acv,
  total_opps,
  won_opps,
  lost_opps,
  open_opps,

  -- Derived metrics
  SAFE_DIVIDE(renewal_won_count, renewal_closed_count) * 100                AS renewal_win_rate_pct,
  SAFE_DIVIDE(sales_won_acv, NULLIF(renewal_lost_acv, 0)) * 100             AS sales_coverage_pct,
  SAFE_DIVIDE(net_new_won_acv, NULLIF(renewal_lost_acv, 0)) * 100           AS net_new_coverage_pct

FROM base

UNION ALL

SELECT
  fiscal_quarter,
  fiscal_year,
  renewal_won_count,
  renewal_lost_count,
  renewal_closed_count,
  renewal_won_acv,
  renewal_lost_acv,
  sales_won_acv,
  net_new_won_acv,
  expansion_won_acv,
  GREATEST(migration_won_acv, 0) AS migration_won_acv,
  expansion_open_acv,
  expansion_open_count,
  total_won_acv,
  total_lost_acv,
  total_open_acv,
  total_opps,
  won_opps,
  lost_opps,
  open_opps,
  SAFE_DIVIDE(renewal_won_count, renewal_closed_count) * 100  AS renewal_win_rate_pct,
  SAFE_DIVIDE(sales_won_acv, NULLIF(renewal_lost_acv, 0)) * 100 AS sales_coverage_pct,
  SAFE_DIVIDE(net_new_won_acv, NULLIF(renewal_lost_acv, 0)) * 100 AS net_new_coverage_pct
FROM fy

ORDER BY fiscal_quarter
;
