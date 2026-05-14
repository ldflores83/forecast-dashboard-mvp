-- ============================================================
-- vw_accounts_enriched
-- Account universe (customer_base + active_pipeline) enriched
-- with aggregated opportunity metrics joined from opportunities.
-- One row per account_id.
-- ============================================================
CREATE OR REPLACE VIEW `forecast-dashboard-mvp.forecast_data.vw_accounts_enriched` AS

SELECT
    a.account_id,
    a.name,
    a.type,
    a.site_type,
    a.status,
    a.region,
    a.owner_region,
    a.erp_customer_base,
    a.sc_customer_base,
    a.erp_customer_tier                                                  AS customer_tier,
    a.primary_vertical,
    a.primary_sub_vertical,
    a.annual_revenue,
    a.global_hq_name,
    a.global_hq_id,
    a.q_score,
    a.q_trend,
    a.q_meetings_booked,
    a.q_condition,
    a.q_visitor_count,
    a.days_since_last_activity_qualified,
    a.at_risk,
    a.recurring_rev_customer_base,
    a.has_open_opportunity,
    a.open_opportunities_count,
    a.target_account_status,
    a.billing_latitude,
    a.billing_longitude,
    a.whitespace_gross_potential,
    a.universe_reason,

    -- Aggregated from opportunities
    COUNT(o.Id)                                                          AS total_opps_ever,
    COUNTIF(o.Is_Open = TRUE)                                           AS open_opps_count,
    COUNTIF(o.Is_Won = TRUE)                                            AS won_opps_count,
    COUNTIF(o.Is_Lost = TRUE)                                           AS lost_opps_count,
    COALESCE(SUM(CASE WHEN o.Is_Open  THEN o.ACV_USD END), 0)          AS open_pipeline_acv,
    COALESCE(SUM(CASE WHEN o.Is_Won   THEN o.ACV_USD END), 0)          AS won_acv_total,
    COALESCE(SUM(CASE WHEN o.Is_Lost  THEN o.ACV_USD END), 0)          AS lost_acv_total,
    MAX(o.Last_Activity_Date)                                           AS last_opp_activity,
    MAX(CASE WHEN o.Is_Open THEN o.CloseDate END)                       AS next_close_date,
    SAFE_DIVIDE(
        COUNTIF(o.Is_Won = TRUE),
        NULLIF(COUNTIF(o.Is_Won = TRUE) + COUNTIF(o.Is_Lost = TRUE), 0)
    ) * 100                                                             AS account_win_rate_pct

FROM `forecast-dashboard-mvp.forecast_data.accounts` a
LEFT JOIN `forecast-dashboard-mvp.forecast_data.opportunities` o
    ON o.AccountId = a.account_id
GROUP BY
    a.account_id, a.name, a.type, a.site_type, a.status, a.region,
    a.owner_region, a.erp_customer_base, a.sc_customer_base, a.erp_customer_tier,
    a.primary_vertical, a.primary_sub_vertical, a.annual_revenue,
    a.global_hq_name, a.global_hq_id, a.q_score, a.q_trend,
    a.q_meetings_booked, a.q_condition, a.q_visitor_count,
    a.days_since_last_activity_qualified, a.at_risk, a.recurring_rev_customer_base,
    a.has_open_opportunity, a.open_opportunities_count, a.target_account_status,
    a.billing_latitude, a.billing_longitude, a.whitespace_gross_potential,
    a.universe_reason
;
