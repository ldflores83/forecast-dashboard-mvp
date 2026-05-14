"""
shared/digest_utils.py
Core digest functions shared between api/main.py (Cloud Function)
and scripts/digest.py (local CLI).

All BQ functions accept a bigquery.Client as first arg so callers
can reuse their module-level client instead of opening a second connection.
"""

import json
import re
import urllib.request
from datetime import datetime, timezone

import anthropic

PROJECT = "forecast-dashboard-mvp"
DATASET = "forecast_data"
FY      = 2027


# ── DATA FETCHERS ─────────────────────────────────────────────────────────────

def get_hero_metrics(bq) -> dict:
    sql = f"""
        SELECT
            renewal_win_rate_pct,
            renewal_won_acv,
            renewal_lost_acv,
            sales_won_acv,
            net_new_won_acv,
            expansion_won_acv,
            expansion_open_acv,
            expansion_open_count,
            sales_coverage_pct,
            total_open_acv,
            open_opps
        FROM `{PROJECT}.{DATASET}.vw_hero_metrics`
        WHERE fiscal_quarter = 0
          AND fiscal_year = {FY}
        LIMIT 1
    """
    rows = list(bq.query(sql).result())
    if not rows:
        return {}
    r = rows[0]
    acv_fields = {
        "renewal_won_acv", "renewal_lost_acv", "sales_won_acv",
        "net_new_won_acv", "expansion_won_acv", "expansion_open_acv",
        "total_open_acv",
    }
    result = {}
    for key in r.keys():
        val = r[key]
        if key in acv_fields and val is not None:
            result[key] = round(val / 1e6, 1)
        elif val is not None:
            result[key] = val
        else:
            result[key] = None
    return result


def get_latest_signals(bq) -> dict:
    sql = f"""
        SELECT result_json, week_key, generated_at
        FROM `{PROJECT}.{DATASET}.signals_cache`
        ORDER BY generated_at DESC
        LIMIT 1
    """
    rows = list(bq.query(sql).result())
    if not rows:
        return {}
    r = rows[0]
    data = json.loads(r["result_json"])
    data["_week_key"]     = r["week_key"]
    data["_generated_at"] = str(r["generated_at"])
    return data


def get_latest_icp(bq) -> list:
    sql = f"""
        SELECT bu, icp_profile_json, win_rate, sample_size, week_key
        FROM `{PROJECT}.{DATASET}.icp_profiles`
        WHERE week_key = (SELECT MAX(week_key) FROM `{PROJECT}.{DATASET}.icp_profiles`)
    """
    rows = list(bq.query(sql).result())
    result = []
    for r in rows:
        profile = json.loads(r["icp_profile_json"]) if r["icp_profile_json"] else {}
        result.append({
            "bu":          r["bu"],
            "win_rate":    round(r["win_rate"] * 100, 1) if r["win_rate"] is not None else None,
            "sample_size": r["sample_size"],
            "week_key":    r["week_key"],
            "profile":     profile,
        })
    return result


def get_signals_headlines(bq) -> dict:
    sql = f"""
        SELECT pipeline_headline, renewal_headline, winloss_headline, week_key
        FROM `{PROJECT}.{DATASET}.signals_history`
        ORDER BY generated_at DESC
        LIMIT 1
    """
    rows = list(bq.query(sql).result())
    if not rows:
        return {}
    r = rows[0]
    return {
        "pipeline_headline": r["pipeline_headline"],
        "renewal_headline":  r["renewal_headline"],
        "winloss_headline":  r["winloss_headline"],
        "week_key":          r["week_key"],
    }


def get_regional_breakdown(bq) -> list:
    sql = f"""
        SELECT
            region,
            COUNT(*) AS account_count,
            COUNTIF(at_risk = TRUE) AS at_risk_count,
            ROUND(SUM(open_pipeline_acv)/1e6, 1) AS open_pipeline_acv_m,
            ROUND(AVG(q_score), 1) AS avg_q_score,
            ROUND(SUM(whitespace_gross_potential)/1e6, 1) AS whitespace_m,
            ROUND(AVG(account_win_rate_pct), 1) AS avg_win_rate_pct
        FROM `{PROJECT}.{DATASET}.vw_accounts_enriched`
        WHERE region IS NOT NULL
        GROUP BY region
        ORDER BY open_pipeline_acv_m DESC
    """
    rows = list(bq.query(sql).result())
    return [
        {
            "region":              r["region"],
            "account_count":       int(r["account_count"] or 0),
            "at_risk_count":       int(r["at_risk_count"] or 0),
            "open_pipeline_acv_m": float(r["open_pipeline_acv_m"] or 0.0),
            "avg_q_score":         float(r["avg_q_score"]) if r["avg_q_score"] is not None else 0.0,
            "whitespace_m":        float(r["whitespace_m"] or 0.0),
            "avg_win_rate_pct":    float(r["avg_win_rate_pct"]) if r["avg_win_rate_pct"] is not None else 0.0,
        }
        for r in rows
    ]


# ── DIGEST GENERATOR ─────────────────────────────────────────────────────────

def generate_digest(hero: dict, signals: dict, icp: list, headlines: dict,
                    regional: list | None = None) -> tuple:
    """Returns (digest_text, week_key)."""
    week_key = (
        headlines.get("week_key")
        or signals.get("_week_key")
        or datetime.now(timezone.utc).strftime("%Y-W%V")
    )

    icp_summary = ""
    for bu in icp[:3]:
        icp_summary += f"  {bu['bu']}: win rate {bu['win_rate']}% (n={bu['sample_size']})\n"

    regional_lines = ""
    if regional:
        for r in regional:
            regional_lines += (
                f"  Region: {r['region']} | Pipeline: ${r['open_pipeline_acv_m']}M"
                f" | At Risk: {r['at_risk_count']} accounts"
                f" | Avg Engagement Score: {r['avg_q_score']}"
                f" | Whitespace: ${r['whitespace_m']}M\n"
            )
    regional_section = f"REGIONAL BREAKDOWN:\n{regional_lines}" if regional_lines else ""

    prompt = f"""You are a Chief Revenue Officer writing a weekly revenue intelligence brief for the executive team.
Generate a concise digest in under 350 words. Use C-level tone — data-driven, direct, no fluff.

Plain text only. Do not use markdown syntax anywhere:
- no markdown headings, no #, no ##, no bold, no **, no horizontal rules, no ---
- no title line such as "Revenue Intelligence Brief" or "Week of..."
- no markdown bullets. Use short plain text lines instead.
- do not wrap anything in code fences.

Structure with exactly these five sections (use the emoji headers as shown):
\U0001f4ca HEADLINE NUMBERS
⚠️ KEY RISKS
✅ BRIGHT SPOTS
\U0001f30e REGIONAL PULSE
\U0001f3af RECOMMENDED ACTIONS

For REGIONAL PULSE, write 2-3 sentences on which regions have the most pipeline concentration, engagement, or risk. Be specific with numbers from the regional data.
For RECOMMENDED ACTIONS, each action must include an owner tag: (Owner: CRO), (Owner: CS VP), or (Owner: AE Team).
End the digest with a single plain text focus sentence starting with "This week's priority:".
The first line of your response must be the HEADLINE NUMBERS section header.

DATA
HERO METRICS (FY{FY} Full Year):
  Renewal win rate: {hero.get('renewal_win_rate_pct')}%
  Renewal won ACV: ${hero.get('renewal_won_acv')}M | Lost: ${hero.get('renewal_lost_acv')}M
  Sales won ACV: ${hero.get('sales_won_acv')}M | Net-new won: ${hero.get('net_new_won_acv')}M
  Expansion won ACV: ${hero.get('expansion_won_acv')}M | Open: ${hero.get('expansion_open_acv')}M ({hero.get('expansion_open_count')} opps)
  Total open pipeline: ${hero.get('total_open_acv')}M ({hero.get('open_opps')} opps)
  Sales coverage: {hero.get('sales_coverage_pct')}%

SIGNAL HEADLINES (week {week_key}):
  Pipeline: {headlines.get('pipeline_headline', 'N/A')}
  Renewal: {headlines.get('renewal_headline', 'N/A')}
  Win/Loss: {headlines.get('winloss_headline', 'N/A')}

TOP ICP WIN RATES BY BU:
{icp_summary or '  (no ICP data available)'}
{regional_section}
Write the digest now."""

    ac = anthropic.Anthropic()
    response = ac.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return _clean_digest(response.content[0].text), week_key


def _clean_digest(text: str) -> str:
    """Strip common markdown artifacts the model occasionally emits."""
    cleaned_lines = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        titleish = re.sub(r"^[#\s]+", "", line).strip()
        if re.match(r"(?i)^revenue intelligence brief\b", titleish):
            continue
        if re.match(r"(?i)^week of\b", titleish):
            continue
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = line.replace("**", "").replace("__", "").replace("---", "")
        cleaned_lines.append(line.strip())
    cleaned = "\n".join(cleaned_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)


# ── SLACK SENDER ──────────────────────────────────────────────────────────────

def send_to_slack(webhook_url: str, digest_text: str, week_key: str) -> bool:
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Revenue Intelligence Brief — {today}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": digest_text},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"{week_key}  ·  QAD Revenue Intelligence Platform · FY2027",
                    }
                ],
            },
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


# ── SNAPSHOT SAVER ────────────────────────────────────────────────────────────

def save_snapshot(bq, digest_text: str, hero: dict, week_key: str, slack_sent: bool) -> bool:
    """Insert one row into digest_snapshots. Returns True on success."""
    table_ref = f"{PROJECT}.{DATASET}.digest_snapshots"
    row = {
        "week_key":     week_key,
        "digest_text":  digest_text,
        "hero_json":    json.dumps(hero),
        "slack_sent":   slack_sent,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    errors = bq.insert_rows_json(table_ref, [row])
    return not errors
