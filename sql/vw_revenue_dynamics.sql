-- ============================================================
-- vw_revenue_dynamics
-- Sales vs CS coverage by Business Unit and quarter
-- 
-- Sales new revenue = Solutions only (Category = 'Solutions')
--                     Direct only (Is_Channel = FALSE)
-- Churn = ATR_Value of lost renewals (not ACV)
-- Coverage = Solutions Direct / Churn ATR
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_revenue_dynamics` AS

WITH by_quarter AS (
  SELECT
    BU,
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,

    -- Renewal health (all renewals regardless of channel)
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Won)                  AS renewal_won_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Lost)                 AS renewal_lost_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed)                             AS renewal_closed_count,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Won
                      THEN ACV END), 0)                                        AS renewal_won_acv,
    -- Churn uses ATR_Value (annual value at risk), not ACV
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0
                      THEN ATR_Value END), 0)                                  AS renewal_lost_acv,

    -- Sales new revenue: Solutions + Direct only (apples to apples vs software renewals)
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS sales_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Net New'
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion'
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Migration'
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS migration_won_acv,

    -- Channel sales (Solutions only, for separate display)
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Solutions' AND Is_Channel = TRUE AND Is_Won
                      THEN ACV END), 0)                                        AS channel_won_acv,

    -- Services revenue (excluded from coverage calc, shown separately)
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Services' AND Is_Won
                      THEN ACV END), 0)                                        AS services_won_acv,

    -- Open pipeline: Solutions Direct only
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Open
                      THEN ACV END), 0)                                        AS sales_open_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Open
                      THEN ACV END), 0)                                        AS renewal_open_acv

  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY BU, FiscalQuarter, FiscalYear
),

fy AS (
  SELECT
    BU,
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Won)                  AS renewal_won_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed AND Is_Lost)                 AS renewal_lost_count,
    COUNTIF(Sales_Motion = 'Renewal' AND IsClosed)                             AS renewal_closed_count,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Won
                      THEN ACV END), 0)                                        AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Lost AND ATR_Value > 0
                      THEN ATR_Value END), 0)                                  AS renewal_lost_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS sales_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Net New'
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Expansion'
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Migration'
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Won
                      THEN ACV END), 0)                                        AS migration_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Solutions' AND Is_Channel = TRUE AND Is_Won
                      THEN ACV END), 0)                                        AS channel_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Services' AND Is_Won
                      THEN ACV END), 0)                                        AS services_won_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion IN ('Net New','Expansion','Migration')
                       AND Category = 'Solutions' AND Is_Channel = FALSE AND Is_Open
                      THEN ACV END), 0)                                        AS sales_open_acv,
    COALESCE(SUM(CASE WHEN Sales_Motion = 'Renewal' AND Is_Open
                      THEN ACV END), 0)                                        AS renewal_open_acv
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY BU, FiscalYear
)

SELECT
  BU                                                                            AS bu,
  fiscal_quarter,
  fiscal_year,
  renewal_won_count,
  renewal_lost_count,
  renewal_closed_count,
  renewal_won_acv,
  renewal_lost_acv,
  sales_won_acv,        -- Solutions Direct only
  net_new_won_acv,
  expansion_won_acv,
  migration_won_acv,
  channel_won_acv,      -- Solutions Channel (reference)
  services_won_acv,     -- Services (excluded from coverage)
  sales_open_acv,
  renewal_open_acv,

  -- Win rate
  SAFE_DIVIDE(renewal_won_count, renewal_closed_count) * 100                   AS renewal_win_rate_pct,

  -- Coverage: Solutions Direct sales vs churn ATR
  -- This is the apples-to-apples metric: recurring software revenue vs recurring software lost
  SAFE_DIVIDE(sales_won_acv, NULLIF(renewal_lost_acv, 0)) * 100                AS sales_coverage_pct,

  -- Total solutions coverage including channel (for reference)
  SAFE_DIVIDE(sales_won_acv + channel_won_acv, NULLIF(renewal_lost_acv, 0)) * 100 AS solutions_total_coverage_pct

FROM (SELECT * FROM by_quarter UNION ALL SELECT * FROM fy)
ORDER BY fiscal_quarter, bu
;