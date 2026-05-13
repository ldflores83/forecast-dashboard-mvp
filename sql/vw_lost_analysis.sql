-- ============================================================
-- vw_lost_analysis
-- Lost deals: totals, by BU, by motion, top loss reasons
-- Also includes won/open by motion for Sales Motion section
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_lost_analysis` AS

-- ── Lost totals by quarter ────────────────────────────────
WITH lost_totals AS (
  SELECT
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    COALESCE(SUM(ACV_USD), 0)   AS total_lost_acv,
    COUNT(*)                AS total_lost_count,
    SAFE_DIVIDE(SUM(ACV_USD), COUNT(*)) AS avg_deal_lost
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE Is_Lost = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalQuarter, FiscalYear
),

lost_totals_fy AS (
  SELECT
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    COALESCE(SUM(ACV_USD), 0)   AS total_lost_acv,
    COUNT(*)                AS total_lost_count,
    SAFE_DIVIDE(SUM(ACV_USD), COUNT(*)) AS avg_deal_lost
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE Is_Lost = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalYear
),

-- ── Lost by BU ────────────────────────────────────────────
lost_by_bu AS (
  SELECT
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    BU,
    COALESCE(SUM(ACV_USD), 0) AS lost_acv,
    COUNT(*)              AS lost_count
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE Is_Lost = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalQuarter, FiscalYear, BU
),

lost_by_bu_fy AS (
  SELECT
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    BU,
    COALESCE(SUM(ACV_USD), 0) AS lost_acv,
    COUNT(*)              AS lost_count
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE Is_Lost = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalYear, BU
),

-- ── Top loss reasons ──────────────────────────────────────
-- Ranked within each quarter to support top-N in API
loss_reasons AS (
  SELECT
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    COALESCE(Loss_Reason, 'Unknown') AS loss_reason,
    COUNT(*)        AS reason_count,
    COALESCE(SUM(ACV_USD), 0) AS reason_acv,
    ROW_NUMBER() OVER (
      PARTITION BY FiscalQuarter, FiscalYear
      ORDER BY COUNT(*) DESC
    )               AS reason_rank
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE Is_Lost = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalQuarter, FiscalYear, Loss_Reason
),

loss_reasons_fy AS (
  SELECT
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    COALESCE(Loss_Reason, 'Unknown') AS loss_reason,
    COUNT(*)        AS reason_count,
    COALESCE(SUM(ACV_USD), 0) AS reason_acv,
    ROW_NUMBER() OVER (
      PARTITION BY FiscalYear
      ORDER BY COUNT(*) DESC
    )               AS reason_rank
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE Is_Lost = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
  GROUP BY FiscalYear, Loss_Reason
),

-- ── By motion (won + lost + open) ─────────────────────────
by_motion AS (
  SELECT
    FiscalQuarter   AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    Sales_Motion    AS motion,
    COALESCE(SUM(CASE WHEN Is_Won  THEN ACV_USD END), 0) AS won_acv,
    COALESCE(SUM(CASE WHEN Is_Lost THEN ACV_USD END), 0) AS lost_acv,
    COALESCE(SUM(CASE WHEN Is_Open THEN ACV_USD END), 0) AS open_acv,
    COUNTIF(Is_Won)     AS won_count,
    COUNTIF(Is_Lost)    AS lost_count,
    COUNTIF(Is_Open)    AS open_count,
    COUNTIF(IsClosed)   AS closed_count,
    SAFE_DIVIDE(COUNTIF(Is_Won), NULLIF(COUNTIF(IsClosed), 0)) * 100 AS win_rate_pct,
    SAFE_DIVIDE(SUM(CASE WHEN Is_Won THEN ACV_USD END), NULLIF(COUNTIF(Is_Won), 0)) AS avg_deal_won
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
    AND (Sales_Motion = 'Renewal' OR (Sales_Motion IN ('Net New', 'Expansion', 'Migration') AND Category = 'Solutions'))
  GROUP BY FiscalQuarter, FiscalYear, Sales_Motion
),

by_motion_fy AS (
  SELECT
    0               AS fiscal_quarter,
    FiscalYear      AS fiscal_year,
    Sales_Motion    AS motion,
    COALESCE(SUM(CASE WHEN Is_Won  THEN ACV_USD END), 0) AS won_acv,
    COALESCE(SUM(CASE WHEN Is_Lost THEN ACV_USD END), 0) AS lost_acv,
    COALESCE(SUM(CASE WHEN Is_Open THEN ACV_USD END), 0) AS open_acv,
    COUNTIF(Is_Won)     AS won_count,
    COUNTIF(Is_Lost)    AS lost_count,
    COUNTIF(Is_Open)    AS open_count,
    COUNTIF(IsClosed)   AS closed_count,
    SAFE_DIVIDE(COUNTIF(Is_Won), NULLIF(COUNTIF(IsClosed), 0)) * 100 AS win_rate_pct,
    SAFE_DIVIDE(SUM(CASE WHEN Is_Won THEN ACV_USD END), NULLIF(COUNTIF(Is_Won), 0)) AS avg_deal_won
  FROM `forecast-dashboard-mvp.forecast_data.opportunities_fy2027`
  WHERE BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
    AND (Sales_Motion = 'Renewal' OR (Sales_Motion IN ('Net New', 'Expansion', 'Migration') AND Category = 'Solutions'))
  GROUP BY FiscalYear, Sales_Motion
)

-- ── Unified output ────────────────────────────────────────
-- record_type tells the API which section this row belongs to

SELECT 'lost_total' AS record_type,
  lt.fiscal_quarter, lt.fiscal_year,
  total_lost_acv, total_lost_count, avg_deal_lost,
  NULL AS bu, NULL AS lost_acv_bu, NULL AS lost_count_bu,
  NULL AS loss_reason, NULL AS reason_count, NULL AS reason_acv, NULL AS reason_rank,
  NULL AS motion, NULL AS won_acv, NULL AS lost_acv_motion, NULL AS open_acv,
  NULL AS won_count, NULL AS lost_count_motion, NULL AS open_count,
  NULL AS closed_count, NULL AS win_rate_pct, NULL AS avg_deal_won
FROM (SELECT * FROM lost_totals UNION ALL SELECT * FROM lost_totals_fy) lt

UNION ALL

SELECT 'lost_by_bu' AS record_type,
  lbu.fiscal_quarter, lbu.fiscal_year,
  NULL, NULL, NULL,
  BU AS bu, lost_acv AS lost_acv_bu, lost_count AS lost_count_bu,
  NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL,
  NULL, NULL, NULL,
  NULL, NULL, NULL
FROM (SELECT * FROM lost_by_bu UNION ALL SELECT * FROM lost_by_bu_fy) lbu

UNION ALL

SELECT 'loss_reason' AS record_type,
  lr.fiscal_quarter, lr.fiscal_year,
  NULL, NULL, NULL, NULL, NULL, NULL,
  loss_reason, reason_count, reason_acv, reason_rank,
  NULL, NULL, NULL, NULL,
  NULL, NULL, NULL,
  NULL, NULL, NULL
FROM (SELECT * FROM loss_reasons UNION ALL SELECT * FROM loss_reasons_fy) lr

UNION ALL

SELECT 'by_motion' AS record_type,
  bm.fiscal_quarter, bm.fiscal_year,
  NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL,
  motion, won_acv, lost_acv AS lost_acv_motion, open_acv,
  won_count, lost_count AS lost_count_motion, open_count,
  closed_count, win_rate_pct, avg_deal_won
FROM (SELECT * FROM by_motion UNION ALL SELECT * FROM by_motion_fy) bm

ORDER BY fiscal_quarter, record_type
;