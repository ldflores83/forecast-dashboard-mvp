-- ============================================================
-- vw_hero_metrics
-- Hero KPIs: renewal win rate, sales coverage of churn,
-- open expansion pipeline, lost renewals (churn)
-- Granularity: one row per fiscal_quarter + one row for FY (quarter = 0)
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_hero_metrics` AS

WITH base AS (
  SELECT
    FiscalQuarter                                         AS fiscal_quarter,
    FiscalYear                                            AS fiscal_year,

    -- Renewal counts
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Won)     AS renewal_won_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Lost)    AS renewal_lost_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed)                AS renewal_closed_count,

    -- Renewal ACV
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Won  THEN ACV END), 0) AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0 THEN ATR_Value END), 0) AS renewal_lost_acv,

    -- Sales new revenue (Net New + Expansion + Migration won)
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration') AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS sales_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Net New'   AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Migration' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS migration_won_acv,

    -- Open expansion pipeline
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Open THEN ACV END), 0) AS expansion_open_acv,
    COUNTIF(Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Open)                                  AS expansion_open_count,

    -- Totals
    COALESCE(SUM(CASE WHEN Is_Won  THEN ACV END), 0) AS total_won_acv,
    COALESCE(SUM(CASE WHEN Is_Lost THEN ACV END), 0) AS total_lost_acv,
    COALESCE(SUM(CASE WHEN Is_Open THEN ACV END), 0) AS total_open_acv,
    COUNT(*) AS total_opps,
    COUNTIF(Is_Won)  AS won_opps,
    COUNTIF(Is_Lost) AS lost_opps,
    COUNTIF(Is_Open) AS open_opps

  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY FiscalQuarter, FiscalYear
),

fy AS (
  -- Full year rollup (fiscal_quarter = 0 signals FY)
  SELECT
    0                                                             AS fiscal_quarter,
    FiscalYear                                                    AS fiscal_year,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Won)     AS renewal_won_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Lost)    AS renewal_lost_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed)                AS renewal_closed_count,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Won  THEN ACV END), 0) AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0 THEN ATR_Value END), 0) AS renewal_lost_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration') AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS sales_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Net New'   AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Migration' AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won THEN ACV END), 0) AS migration_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Open THEN ACV END), 0) AS expansion_open_acv,
    COUNTIF(Sales_Motion = 'Expansion' AND Category = 'Solutions' AND Is_Open)                                  AS expansion_open_count,
    COALESCE(SUM(CASE WHEN Is_Won  THEN ACV END), 0) AS total_won_acv,
    COALESCE(SUM(CASE WHEN Is_Lost THEN ACV END), 0) AS total_lost_acv,
    COALESCE(SUM(CASE WHEN Is_Open THEN ACV END), 0) AS total_open_acv,
    COUNT(*) AS total_opps,
    COUNTIF(Is_Won)  AS won_opps,
    COUNTIF(Is_Lost) AS lost_opps,
    COUNTIF(Is_Open) AS open_opps
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY FiscalYear
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