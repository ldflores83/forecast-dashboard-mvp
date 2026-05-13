-- ============================================================
-- vw_account_health
-- Combines open renewal opportunities with Jira ticket signals
-- to produce a per-account risk profile.
--
-- Key use cases:
--   1. AI Insights — feed Claude with account-level context
--   2. CS risk prioritization — which accounts need attention NOW
--   3. Pulzio Mode 2 — Mini CSP health scoring foundation
--
-- Join key: opportunities.AccountId = jira_tickets.salesforce_account_id
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_account_health` AS

WITH

-- ── OPEN RENEWALS (clean filter) ─────────────────────────────────────────────
open_renewals AS (
  SELECT
    AccountId                                     AS account_id,
    Account_Name                                  AS account_name,
    BU                                            AS bu,
    COALESCE(SUM(ATR_Value_USD), 0)               AS renewal_atr,
    COALESCE(SUM(ACV_USD), 0)                     AS renewal_acv,
    COUNT(*)                                      AS renewal_opp_count,
    MIN(PCED)                                     AS earliest_renewal_date,
    DATE_DIFF(PARSE_DATE('%Y-%m-%d', MIN(PCED)), CURRENT_DATE(), DAY) AS days_to_earliest_renewal,
    MAX(PCED)                                     AS latest_renewal_date

  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE Is_Open = TRUE
    AND Sales_Motion = 'Renewal'
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
    AND Name NOT LIKE '%Amendment%'
    AND Name NOT LIKE '%zzz%'
  GROUP BY AccountId, Account_Name, BU
),

-- ── TICKET SIGNALS ────────────────────────────────────────────────────────────
ticket_signals AS (
  SELECT
    salesforce_account_id                         AS account_id,

    -- Volume
    COUNT(*)                                      AS total_tickets,
    COUNTIF(issue_status_name NOT IN (
      'Resolved', 'Closed', 'Done', 'Cancelled'
    ))                                            AS open_tickets,

    -- Priority signals
    COUNTIF(priority_name = 'Priority-1'
      AND issue_status_name NOT IN ('Resolved','Closed','Done','Cancelled'))
                                                  AS p1_open,
    COUNTIF(priority_name IN ('Priority-1','Priority-2')
      AND issue_status_name NOT IN ('Resolved','Closed','Done','Cancelled'))
                                                  AS p1_p2_open,

    -- Escalation signals
    -- Only 'Yes Active' = truly escalated (not de-escalated or requested)
    COUNTIF(is_escalated = 'Yes Active'
      AND issue_status_name NOT IN ('Resolved','Closed','Done','Cancelled'))
                                                  AS escalated_open,

    -- Stale tickets open > 60 days (more reliable than escalation field)
    COUNTIF(
      DATE_DIFF(CURRENT_DATE(), DATE(created), DAY) > 60
      AND issue_status_name NOT IN ('Resolved','Closed','Done','Cancelled')
    )                                             AS stale_tickets_open,

    -- Age signals
    MAX(CASE
      WHEN issue_status_name NOT IN ('Resolved','Closed','Done','Cancelled')
      THEN DATE_DIFF(CURRENT_DATE(), DATE(created), DAY)
      ELSE NULL
    END)                                          AS oldest_open_ticket_days,

    AVG(CASE
      WHEN resolution_date IS NOT NULL
      THEN DATE_DIFF(DATE(resolution_date), DATE(created), DAY)
      ELSE NULL
    END)                                          AS avg_resolution_days,

    -- Recency
    DATE_DIFF(CURRENT_DATE(),
      DATE(MAX(created)), DAY)                    AS days_since_last_ticket,
    DATE_DIFF(CURRENT_DATE(), DATE(MAX(updated)), DAY)                AS days_since_last_update,

    -- Recent activity (last 30 days)
    COUNTIF(DATE_DIFF(CURRENT_DATE(), DATE(created), DAY) <= 30)
                                                  AS tickets_last_30_days,
    COUNTIF(DATE_DIFF(CURRENT_DATE(), DATE(created), DAY) <= 90)
                                                  AS tickets_last_90_days

  FROM `forecast-dashboard-mvp.forecast_data.jira_tickets`
  WHERE salesforce_account_id IS NOT NULL
    AND salesforce_account_id != ''
  GROUP BY salesforce_account_id
),

-- ── WON/LOST RENEWAL HISTORY (for win rate context) ──────────────────────────
renewal_history AS (
  SELECT
    AccountId                                     AS account_id,
    COUNTIF(Is_Won = TRUE)                        AS renewals_won_hist,
    COUNTIF(Is_Lost = TRUE)                       AS renewals_lost_hist,
    COALESCE(SUM(CASE WHEN Is_Lost = TRUE
      THEN ATR_Value_USD ELSE 0 END), 0)          AS historical_churn_acv
  FROM `forecast-dashboard-mvp.forecast_data.opportunities`
  WHERE Sales_Motion = 'Renewal'
    AND IsClosed = TRUE
    AND BU IN ('ERP BU', 'Supply Chain BU', 'Redzone BU')
    AND Substage NOT IN ('Combined', 'Credited', 'Closed-Duplicate', 'Junk')
  GROUP BY AccountId
)

-- ── FINAL JOIN + RISK SCORE ───────────────────────────────────────────────────
SELECT
  r.account_id,
  r.account_name,
  r.bu,

  -- Renewal context
  r.renewal_atr,
  r.renewal_acv,
  r.renewal_opp_count,
  r.earliest_renewal_date,
  r.days_to_earliest_renewal,

  -- Ticket signals (0 if no tickets)
  COALESCE(t.total_tickets, 0)          AS total_tickets,
  COALESCE(t.open_tickets, 0)           AS open_tickets,
  COALESCE(t.p1_open, 0)               AS p1_open,
  COALESCE(t.p1_p2_open, 0)            AS p1_p2_open,
  COALESCE(t.escalated_open, 0)        AS escalated_open,
  COALESCE(t.stale_tickets_open, 0)    AS stale_tickets_open,
  COALESCE(t.oldest_open_ticket_days, 0) AS oldest_open_ticket_days,
  COALESCE(t.avg_resolution_days, 0)   AS avg_resolution_days,
  COALESCE(t.days_since_last_ticket, 999) AS days_since_last_ticket,
  COALESCE(t.tickets_last_30_days, 0)  AS tickets_last_30_days,
  COALESCE(t.tickets_last_90_days, 0)  AS tickets_last_90_days,

  -- Historical renewal context
  COALESCE(h.renewals_won_hist, 0)     AS renewals_won_hist,
  COALESCE(h.renewals_lost_hist, 0)    AS renewals_lost_hist,
  COALESCE(h.historical_churn_acv, 0)  AS historical_churn_acv,

  -- Has ticket data flag
  CASE WHEN t.account_id IS NOT NULL THEN TRUE ELSE FALSE END AS has_ticket_data,

  -- ── RISK SCORE (0-100) ──────────────────────────────────────────────────────
  -- Higher = higher risk. Weights based on impact:
  --   Renewal urgency (30 pts max)
  --   Ticket severity (40 pts max)
  --   Historical churn (30 pts max)
  LEAST(100, GREATEST(0,

    -- Urgency: renewal in next 90 days
    CASE
      WHEN r.days_to_earliest_renewal <= 30  THEN 30
      WHEN r.days_to_earliest_renewal <= 60  THEN 20
      WHEN r.days_to_earliest_renewal <= 90  THEN 10
      ELSE 0
    END

    -- P1 tickets open
    + LEAST(20, COALESCE(t.p1_open, 0) * 7)

    -- Escalated tickets open
    + LEAST(10, COALESCE(t.escalated_open, 0) * 5)

    -- Very old open ticket (> 60 days)
    + CASE
        WHEN COALESCE(t.oldest_open_ticket_days, 0) > 90 THEN 10
        WHEN COALESCE(t.oldest_open_ticket_days, 0) > 60 THEN 5
        ELSE 0
      END

    -- Historical churn pattern
    + CASE
        WHEN COALESCE(h.renewals_lost_hist, 0) > 0 THEN 15
        ELSE 0
      END

    -- Large ATR at risk multiplier (> $500K)
    + CASE
        WHEN r.renewal_atr > 1000000 THEN 10
        WHEN r.renewal_atr > 500000  THEN 5
        ELSE 0
      END

  ))                                             AS risk_score,

  -- Risk tier
  CASE
    WHEN -- See score calculation above
      LEAST(100, GREATEST(0,
        CASE WHEN r.days_to_earliest_renewal <= 30 THEN 30
             WHEN r.days_to_earliest_renewal <= 60 THEN 20
             WHEN r.days_to_earliest_renewal <= 90 THEN 10
             ELSE 0 END
        + LEAST(20, COALESCE(t.p1_open, 0) * 7)
        + LEAST(15, COALESCE(t.escalated_open, 0) * 5)
        + LEAST(5, COALESCE(t.stale_tickets_open, 0) * 1)
        + CASE WHEN COALESCE(t.oldest_open_ticket_days, 0) > 90 THEN 10
               WHEN COALESCE(t.oldest_open_ticket_days, 0) > 60 THEN 5
               ELSE 0 END
        + CASE WHEN COALESCE(h.renewals_lost_hist, 0) > 0 THEN 15 ELSE 0 END
        + CASE WHEN r.renewal_atr > 1000000 THEN 10
               WHEN r.renewal_atr > 500000  THEN 5
               ELSE 0 END
      )) >= 60 THEN 'High'
    WHEN
      LEAST(100, GREATEST(0,
        CASE WHEN r.days_to_earliest_renewal <= 30 THEN 30
             WHEN r.days_to_earliest_renewal <= 60 THEN 20
             WHEN r.days_to_earliest_renewal <= 90 THEN 10
             ELSE 0 END
        + LEAST(20, COALESCE(t.p1_open, 0) * 7)
        + LEAST(15, COALESCE(t.escalated_open, 0) * 5)
        + LEAST(5, COALESCE(t.stale_tickets_open, 0) * 1)
        + CASE WHEN COALESCE(t.oldest_open_ticket_days, 0) > 90 THEN 10
               WHEN COALESCE(t.oldest_open_ticket_days, 0) > 60 THEN 5
               ELSE 0 END
        + CASE WHEN COALESCE(h.renewals_lost_hist, 0) > 0 THEN 15 ELSE 0 END
        + CASE WHEN r.renewal_atr > 1000000 THEN 10
               WHEN r.renewal_atr > 500000  THEN 5
               ELSE 0 END
      )) >= 30 THEN 'Medium'
    ELSE 'Low'
  END                                            AS risk_tier

FROM open_renewals r
LEFT JOIN ticket_signals t ON r.account_id = t.account_id
LEFT JOIN renewal_history h ON r.account_id = h.account_id

ORDER BY risk_score DESC, renewal_atr DESC
;