-- ============================================================
-- vw_pipeline
-- Open pipeline by stage, by BU, by motion
-- Includes win rate and avg deal size per BU
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
    BU,
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,

    -- Open pipeline
    COALESCE(SUM(CASE WHEN Is_Open THEN ACV_USD END), 0)              AS open_acv,
    COUNTIF(Is_Open)                                               AS open_count,

    -- By motion (open)
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Net New'   AND Category = 'Solutions'   THEN ACV_USD END), 0) AS open_net_new_acv,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Expansion' AND Category = 'Solutions' THEN ACV_USD END), 0) AS open_expansion_acv,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Migration' AND Category = 'Solutions' THEN ACV_USD END), 0) AS open_migration_acv,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Renewal'   THEN ACV_USD END), 0) AS open_renewal_acv,

    -- Win rate (all time for the period)
    COUNTIF(IsClosed AND Is_Won)                                   AS won_count,
    COUNTIF(IsClosed)                                              AS closed_count,
    COALESCE(SUM(CASE WHEN Is_Won THEN ACV_USD END), 0)               AS won_acv,

    -- Avg deal (won only)
    SAFE_DIVIDE(
      SUM(CASE WHEN Is_Won THEN ACV_USD END),
      NULLIF(COUNTIF(Is_Won), 0)
    )                                                              AS avg_deal_won

  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY BU, FiscalQuarter, FiscalYear
),

bu_fy AS (
  SELECT
    BU,
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    COALESCE(SUM(CASE WHEN Is_Open THEN ACV_USD END), 0)              AS open_acv,
    COUNTIF(Is_Open)                                               AS open_count,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Net New'   AND Category = 'Solutions'   THEN ACV_USD END), 0) AS open_net_new_acv,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Expansion' AND Category = 'Solutions' THEN ACV_USD END), 0) AS open_expansion_acv,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Migration' AND Category = 'Solutions' THEN ACV_USD END), 0) AS open_migration_acv,
    COALESCE(SUM(CASE WHEN Is_Open AND Sales_Motion = 'Renewal'   THEN ACV_USD END), 0) AS open_renewal_acv,
    COUNTIF(IsClosed AND Is_Won)                                   AS won_count,
    COUNTIF(IsClosed)                                              AS closed_count,
    COALESCE(SUM(CASE WHEN Is_Won THEN ACV_USD END), 0)               AS won_acv,
    SAFE_DIVIDE(
      SUM(CASE WHEN Is_Won THEN ACV_USD END),
      NULLIF(COUNTIF(Is_Won), 0)
    )                                                              AS avg_deal_won
  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY BU, FiscalYear
)

-- ── Output ────────────────────────────────────────────────
SELECT
  'stage'           AS record_type,
  fiscal_quarter,
  fiscal_year,
  stage_name        AS dimension,
  stage_group       AS dimension_group,
  open_acv,
  open_count        AS count,
  NULL              AS won_acv,
  NULL              AS won_count,
  NULL              AS closed_count,
  NULL              AS avg_deal_won,
  NULL              AS win_rate_pct,
  NULL              AS open_net_new_acv,
  NULL              AS open_expansion_acv,
  NULL              AS open_migration_acv,
  NULL              AS open_renewal_acv
FROM (SELECT * FROM stage_pipeline UNION ALL SELECT * FROM stage_fy)

UNION ALL

SELECT
  'bu'              AS record_type,
  fiscal_quarter,
  fiscal_year,
  BU                AS dimension,
  NULL              AS dimension_group,
  open_acv,
  open_count        AS count,
  won_acv,
  won_count,
  closed_count,
  avg_deal_won,
  SAFE_DIVIDE(won_count, NULLIF(closed_count, 0)) * 100 AS win_rate_pct,
  open_net_new_acv,
  open_expansion_acv,
  open_migration_acv,
  open_renewal_acv
FROM (SELECT * FROM bu_summary UNION ALL SELECT * FROM bu_fy)

ORDER BY fiscal_quarter, record_type, dimension
;