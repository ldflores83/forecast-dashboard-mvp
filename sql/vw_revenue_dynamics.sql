-- ============================================================
-- vw_revenue_dynamics
-- Sales vs CS coverage by Business Unit and quarter
--
-- Sales new revenue = Solutions only (Category = 'Solutions')
--                     Direct only (Is_Channel = FALSE)
--                     Uses split_solutions_acv when available
-- Churn = ATR_Value of lost renewals (not ACV)
-- Coverage = Solutions Direct / Churn ATR
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_revenue_dynamics` AS

WITH by_quarter AS (
  SELECT
    o.BU,
    o.FiscalQuarter   AS fiscal_quarter,
    o.FiscalYear      AS fiscal_year,

    -- Renewal health (all renewals regardless of channel)
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Won)                  AS renewal_won_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Lost)                 AS renewal_lost_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed)                               AS renewal_closed_count,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Won
                      THEN o.ACV_USD END), 0)                                        AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Lost AND o.ATR_Value_USD > 0
                      THEN o.ATR_Value_USD END), 0)                                  AS renewal_lost_acv,

    -- Sales new revenue: split_solutions_acv (no fallback — deals without it are services/implementations)
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS sales_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Net New'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Migration'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS migration_won_acv,

    -- Channel and Services use ACV_USD (split attribution not applicable)
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = TRUE AND o.Is_Won
                      THEN o.ACV_USD END), 0)                                        AS channel_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Services' AND o.Is_Won
                      THEN o.ACV_USD END), 0)                                        AS services_won_acv,

    -- Open pipeline uses ACV_USD (split attribution not needed for pipeline)
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Open
                      THEN o.ACV_USD END), 0)                                        AS sales_open_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Open
                      THEN o.ACV_USD END), 0)                                        AS renewal_open_acv

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
  GROUP BY o.BU, o.FiscalQuarter, o.FiscalYear
),

fy AS (
  SELECT
    o.BU,
    0               AS fiscal_quarter,
    o.FiscalYear    AS fiscal_year,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Won)                  AS renewal_won_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed AND o.Is_Lost)                 AS renewal_lost_count,
    COUNTIF(o.Sales_Motion = 'Renewal' AND o.IsClosed)                               AS renewal_closed_count,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Won
                      THEN o.ACV_USD END), 0)                                        AS renewal_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Lost AND o.ATR_Value_USD > 0
                      THEN o.ATR_Value_USD END), 0)                                  AS renewal_lost_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS sales_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Net New'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS net_new_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Expansion'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS expansion_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Migration'
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Won
                      THEN s.split_solutions_acv END), 0)                            AS migration_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = TRUE AND o.Is_Won
                      THEN o.ACV_USD END), 0)                                        AS channel_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Services' AND o.Is_Won
                      THEN o.ACV_USD END), 0)                                        AS services_won_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion IN ('Net New','Expansion','Migration')
                       AND o.Category = 'Solutions' AND o.Is_Channel = FALSE AND o.Is_Open
                      THEN o.ACV_USD END), 0)                                        AS sales_open_acv,
    COALESCE(SUM(CASE WHEN o.Sales_Motion = 'Renewal' AND o.Is_Open
                      THEN o.ACV_USD END), 0)                                        AS renewal_open_acv
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
  GROUP BY o.BU, o.FiscalYear
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
  sales_won_acv,        -- Solutions Direct split_solutions_acv only
  net_new_won_acv,
  expansion_won_acv,
  migration_won_acv,
  channel_won_acv,      -- Solutions Channel ACV_USD (reference)
  services_won_acv,     -- Services ACV_USD (excluded from coverage)
  sales_open_acv,
  renewal_open_acv,

  -- Win rate
  SAFE_DIVIDE(renewal_won_count, renewal_closed_count) * 100                   AS renewal_win_rate_pct,

  -- Coverage: Solutions Direct split ACV vs churn ATR
  SAFE_DIVIDE(sales_won_acv, NULLIF(renewal_lost_acv, 0)) * 100                AS sales_coverage_pct,

  -- Total solutions coverage including channel (for reference)
  SAFE_DIVIDE(sales_won_acv + channel_won_acv, NULLIF(renewal_lost_acv, 0)) * 100 AS solutions_total_coverage_pct

FROM (SELECT * FROM by_quarter UNION ALL SELECT * FROM fy)
ORDER BY fiscal_quarter, bu
;
