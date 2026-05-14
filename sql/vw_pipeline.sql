-- ============================================================
-- vw_pipeline
-- Open pipeline by stage, by BU, by motion
-- Includes win rate and avg deal size per BU
-- BU rows enriched with account intent signals from
-- vw_accounts_enriched (q_score, q_trend, q_condition,
-- account_at_risk, target_account_status, whitespace_gross_potential)
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_pipeline` AS

-- ── Pipeline by stage ──────────────────────────────────────
WITH stage_pipeline AS (
  SELECT
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    StageName       AS stage_name,

    -- Stage group for color coding in frontend
    CASE
      WHEN StageName IN ('Development','Sales Ready','Qualifying','Stalled') THEN 'early'
      WHEN StageName IN ('Solution Exploration','Evaluation & Alignment',
                         'Proposal & Negotiation','Awaiting Signature')      THEN 'active'
      WHEN StageName IN ('Renewal Pending','Renewal Validation',
                         'Renewal Negotiation','Renewal Confirmed')          THEN 'renewal'
      ELSE 'other'
    END             AS stage_group,

    COALESCE(SUM(ACV_USD), 0) AS open_acv,
    COUNT(*)              AS open_count

  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE Is_Open = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalQuarter, FiscalYear, StageName
),

stage_fy AS (
  SELECT
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    StageName       AS stage_name,
    CASE
      WHEN StageName IN ('Development','Sales Ready','Qualifying','Stalled') THEN 'early'
      WHEN StageName IN ('Solution Exploration','Evaluation & Alignment',
                         'Proposal & Negotiation','Awaiting Signature')      THEN 'active'
      WHEN StageName IN ('Renewal Pending','Renewal Validation',
                         'Renewal Negotiation','Renewal Confirmed')          THEN 'renewal'
      ELSE 'other'
    END             AS stage_group,
    COALESCE(SUM(ACV_USD), 0) AS open_acv,
    COUNT(*)              AS open_count
  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE Is_Open = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalYear, StageName
),

-- ── BU summary ────────────────────────────────────────────
bu_summary AS (
  SELECT
    o.BU,
    o.FiscalQuarter   AS fiscal_quarter,
    o.FiscalYear      AS fiscal_year,

    -- Open pipeline
    COALESCE(SUM(CASE WHEN o.Is_Open THEN o.ACV_USD END), 0)              AS open_acv,
    COUNTIF(o.Is_Open)                                                    AS open_count,

    -- By motion (open)
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Net New'   AND o.Category = 'Solutions'   THEN o.ACV_USD END), 0) AS open_net_new_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Expansion' AND o.Category = 'Solutions' THEN o.ACV_USD END), 0) AS open_expansion_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Migration' AND o.Category = 'Solutions' THEN o.ACV_USD END), 0) AS open_migration_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Renewal'   THEN o.ACV_USD END), 0) AS open_renewal_acv,

    -- Win rate (all time for the period)
    COUNTIF(o.IsClosed AND o.Is_Won)                                      AS won_count,
    COUNTIF(o.IsClosed)                                                   AS closed_count,
    COALESCE(SUM(CASE WHEN o.Is_Won THEN o.ACV_USD END), 0)               AS won_acv,

    -- Avg deal (won only)
    SAFE_DIVIDE(
      SUM(CASE WHEN o.Is_Won THEN o.ACV_USD END),
      NULLIF(COUNTIF(o.Is_Won), 0)
    )                                                                     AS avg_deal_won,

    -- Account intent signals (aggregated across open opps in this BU/quarter)
    AVG(CASE WHEN o.Is_Open THEN acc.q_score END)                         AS q_score,
    MAX(CASE WHEN o.Is_Open THEN acc.q_trend END)                         AS q_trend,
    MAX(CASE WHEN o.Is_Open THEN acc.q_condition END)                     AS q_condition,
    LOGICAL_OR(CASE WHEN o.Is_Open THEN acc.at_risk END)                  AS account_at_risk,
    MAX(CASE WHEN o.Is_Open THEN acc.target_account_status END)           AS target_account_status,
    COALESCE(SUM(CASE WHEN o.Is_Open THEN acc.whitespace_gross_potential END), 0) AS whitespace_gross_potential

  FROM `forecast-dashboard-mvp.forecast_data.opportunities` o
  LEFT JOIN `forecast-dashboard-mvp.forecast_data.vw_accounts_enriched` acc
    ON o.AccountId = acc.account_id
  WHERE o.BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND o.Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND o.Name NOT LIKE '%Amendment%'
    AND o.Name NOT LIKE '%zzz%'
  GROUP BY o.BU, o.FiscalQuarter, o.FiscalYear
),

bu_fy AS (
  SELECT
    o.BU,
    0               AS fiscal_quarter,
    o.FiscalYear    AS fiscal_year,
    COALESCE(SUM(CASE WHEN o.Is_Open THEN o.ACV_USD END), 0)              AS open_acv,
    COUNTIF(o.Is_Open)                                                    AS open_count,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Net New'   AND o.Category = 'Solutions'   THEN o.ACV_USD END), 0) AS open_net_new_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Expansion' AND o.Category = 'Solutions' THEN o.ACV_USD END), 0) AS open_expansion_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Migration' AND o.Category = 'Solutions' THEN o.ACV_USD END), 0) AS open_migration_acv,
    COALESCE(SUM(CASE WHEN o.Is_Open AND o.Sales_Motion = 'Renewal'   THEN o.ACV_USD END), 0) AS open_renewal_acv,
    COUNTIF(o.IsClosed AND o.Is_Won)                                      AS won_count,
    COUNTIF(o.IsClosed)                                                   AS closed_count,
    COALESCE(SUM(CASE WHEN o.Is_Won THEN o.ACV_USD END), 0)               AS won_acv,
    SAFE_DIVIDE(
      SUM(CASE WHEN o.Is_Won THEN o.ACV_USD END),
      NULLIF(COUNTIF(o.Is_Won), 0)
    )                                                                     AS avg_deal_won,
    AVG(CASE WHEN o.Is_Open THEN acc.q_score END)                         AS q_score,
    MAX(CASE WHEN o.Is_Open THEN acc.q_trend END)                         AS q_trend,
    MAX(CASE WHEN o.Is_Open THEN acc.q_condition END)                     AS q_condition,
    LOGICAL_OR(CASE WHEN o.Is_Open THEN acc.at_risk END)                  AS account_at_risk,
    MAX(CASE WHEN o.Is_Open THEN acc.target_account_status END)           AS target_account_status,
    COALESCE(SUM(CASE WHEN o.Is_Open THEN acc.whitespace_gross_potential END), 0) AS whitespace_gross_potential
  FROM `forecast-dashboard-mvp.forecast_data.opportunities` o
  LEFT JOIN `forecast-dashboard-mvp.forecast_data.vw_accounts_enriched` acc
    ON o.AccountId = acc.account_id
  WHERE o.BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND o.Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND o.Name NOT LIKE '%Amendment%'
    AND o.Name NOT LIKE '%zzz%'
  GROUP BY o.BU, o.FiscalYear
)

-- ── Output ────────────────────────────────────────────────
SELECT
  'stage'                      AS record_type,
  fiscal_quarter,
  fiscal_year,
  stage_name                   AS dimension,
  stage_group                  AS dimension_group,
  open_acv,
  open_count                   AS count,
  NULL                         AS won_acv,
  NULL                         AS won_count,
  NULL                         AS closed_count,
  NULL                         AS avg_deal_won,
  NULL                         AS win_rate_pct,
  NULL                         AS open_net_new_acv,
  NULL                         AS open_expansion_acv,
  NULL                         AS open_migration_acv,
  NULL                         AS open_renewal_acv,
  CAST(NULL AS FLOAT64)        AS q_score,
  CAST(NULL AS STRING)         AS q_trend,
  CAST(NULL AS STRING)         AS q_condition,
  CAST(NULL AS BOOL)           AS account_at_risk,
  CAST(NULL AS STRING)         AS target_account_status,
  CAST(NULL AS FLOAT64)        AS whitespace_gross_potential
FROM (SELECT * FROM stage_pipeline UNION ALL SELECT * FROM stage_fy)

UNION ALL

SELECT
  'bu'                         AS record_type,
  fiscal_quarter,
  fiscal_year,
  BU                           AS dimension,
  NULL                         AS dimension_group,
  open_acv,
  open_count                   AS count,
  won_acv,
  won_count,
  closed_count,
  avg_deal_won,
  SAFE_DIVIDE(won_count, NULLIF(closed_count, 0)) * 100 AS win_rate_pct,
  open_net_new_acv,
  open_expansion_acv,
  open_migration_acv,
  open_renewal_acv,
  q_score,
  q_trend,
  q_condition,
  account_at_risk,
  target_account_status,
  whitespace_gross_potential
FROM (SELECT * FROM bu_summary UNION ALL SELECT * FROM bu_fy)

ORDER BY fiscal_quarter, record_type, dimension
;
