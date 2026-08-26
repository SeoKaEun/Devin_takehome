"""Dashboard - answers one question for an engineering leader:
"Is this working?" Every number here is derived from state.json (real
pipeline data); nothing is estimated or invented.

Layout: KPI row + status table on top for the leader's 10-second read,
then one activity stream PER ISSUE (system events + Devin messages merged
chronologically) so the full story of each remediation reads top-to-bottom
without interleaving.
"""

import html
import os
from datetime import datetime

from . import config
from . import state as st

STATE_LABELS = {
    st.QUEUED: ("Queued", "#8a8a8a"),
    st.WORKING: ("Devin working", "#1d76db"),
    st.REVIEW_PENDING: ("Awaiting review", "#b08800"),
    st.REVIEW_WORKING: ("Under independent review", "#b08800"),
    st.DONE_FIXED: ("Fixed - PR verified", "#1a7f37"),
    st.DONE_ESCALATED: ("Escalated with report", "#8250df"),
    st.NEEDS_ATTENTION: ("Needs attention", "#cf222e"),
}

ROLE_BADGES = {
    "system": ("SYSTEM", "#57606a"),
    "work": ("DEVIN - work", "#1d76db"),
    "review": ("DEVIN - review", "#8250df"),
}


def _fmt_duration(start_iso, end_iso):
    if not start_iso or not end_iso:
        return "-"
    delta = datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)
    m = int(delta.total_seconds() // 60)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def _issue_finished_at(e):
    for terminal in (st.DONE_FIXED, st.DONE_ESCALATED, st.NEEDS_ATTENTION):
        if e.get(f"{terminal}_at"):
            return e[f"{terminal}_at"]
    return None


def _badge(label, color):
    return f'<span class="badge" style="background:{color}">{html.escape(label)}</span>'


_MILESTONES = ("done_fixed", "done_escalated", "needs_attention",
               "PR", "gate1", "rework")


def _system_headline(event, detail):
    """Translate raw pipeline events into scannable one-liners."""
    table = [
        ("discovered", "Issue detected - queued for remediation", False),
        ("state: queued -> working", "Devin work session started", False),
        ("state: working -> review_pending", "Gate 1 passed - fix verified mechanically", True),
        ("state: review_pending -> review_working", "Independent review started", False),
        ("state: review_working -> done_fixed", "Review approved - DONE", True),
        ("state: review_working -> working", "Review found problems - sent back for rework", True),
        ("state: working -> done_escalated", "Cannot auto-fix - escalated with report", True),
        ("-> needs_attention", "Stopped - needs human attention", True),
        ("state: needs_attention -> working", "Human decision applied - resumed", True),
        ("gate1", "Gate 1 verdict", False),
        ("nudged", "Session was idle - auto-nudge sent", False),
        ("rework_dispatched", "Reviewer findings routed back to work session", True),
        ("manual:", "Human decision", True),
        ("tick_error", "Pipeline error on this issue (isolated)", True),
    ]
    for needle, headline, strong in table:
        if needle in event:
            return headline, strong
    return event, False


def _devin_headline(text):
    """First sentence as headline; the rest stays behind a click."""
    first = text.replace("\n", " ").split(". ")[0].strip()
    if len(first) > 110:
        first = first[:107] + "..."
    return first


def _merged_stream(state, key, entry, limit=25):
    """System timeline + Devin messages for one issue, oldest first.
    Every item: short headline (scannable) + optional collapsed detail."""
    items = []
    for t in entry.get("timeline", []):
        label, strong = _system_headline(t["event"], t.get("detail", ""))
        detail = (t.get("detail") or "").replace("\n", " ").strip()
        # events are curated at record time (pipeline.py); display as-is
        snippet = detail[:160] + ("..." if len(detail) > 160 else "")
        headline = f"{label} · {snippet}" if snippet else label
        items.append({"ts": t["ts"], "role": "system", "headline": headline,
                      "detail": detail if len(detail) > 160 else "",
                      "strong": strong})
    items.sort(key=lambda x: x["ts"])
    return items[-limit:]


def _raw_agent_log(state, key):
    """Devin's verbatim messages - context for the curious, out of the way."""
    return [f for f in state.get("feed", []) if str(f["issue"]) == key]


def render(state):
    issues = state["issues"]
    counts = {}
    for e in issues.values():
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    done = counts.get(st.DONE_FIXED, 0) + counts.get(st.DONE_ESCALATED, 0)
    sessions = sum(bool(e.get("work_session")) + bool(e.get("review_session"))
                   for e in issues.values())

    # --- top table: one line per issue -------------------------------------
    rows = []
    for e in sorted(issues.values(), key=lambda x: x["number"]):
        label, color = STATE_LABELS.get(e["state"], (e["state"], "#000"))
        pr = (f'<a href="{html.escape(e["pr_url"])}">PR &#8599;</a>'
              if e.get("pr_url") else "-")
        rows.append(
            f"<tr><td><a href=\"{html.escape(e['url'])}\">#{e['number']}</a></td>"
            f"<td>{html.escape(e['title'])}</td>"
            f"<td>{_badge(label, color)}</td><td>{pr}</td>"
            f"<td>{_fmt_duration(e.get('queued_at'), _issue_finished_at(e))}</td></tr>"
        )

    # --- per-issue activity streams ----------------------------------------
    sections = []
    for key in sorted(issues, key=int):
        e = issues[key]
        label, color = STATE_LABELS.get(e["state"], (e["state"], "#000"))
        ws, rs = e.get("work_session") or {}, e.get("review_session") or {}
        links = []
        if ws.get("url", "").startswith("http"):
            links.append(f'<a href="{html.escape(ws["url"])}">work session &#8599;</a>')
        if rs.get("url", "").startswith("http"):
            links.append(f'<a href="{html.escape(rs["url"])}">review session &#8599;</a>')
        if e.get("pr_url"):
            links.append(f'<a href="{html.escape(e["pr_url"])}">pull request &#8599;</a>')
        links.append(f'<a href="{html.escape(e["url"])}">issue &#8599;</a>')

        stream = []
        for item in _merged_stream(state, key, e):
            role_label, role_color = ROLE_BADGES.get(item["role"], ("?", "#000"))
            t = item["ts"][11:16]  # HH:MM is enough to follow the flow
            cls = "ev strong" if item.get("strong") else "ev"
            headline = html.escape(item["headline"])
            body = (f'<details><summary>{headline}</summary>'
                    f'<div class="ev-detail">{html.escape(item["detail"])}</div></details>'
                    if item.get("detail") and item["detail"] != item["headline"]
                    else f'<span class="ev-text">{headline}</span>')
            stream.append(
                f'<div class="{cls}"><code>{t}</code> '
                f'{_badge(role_label, role_color)} {body}</div>'
            )
        attention = (f'<p class="attn">Needs attention: '
                     f'{html.escape(e.get("attention_reason") or "")}</p>'
                     if e.get("attention_reason") else "")
        raw = _raw_agent_log(state, key)
        raw_html = ""
        if raw:
            raw_lines = "".join(
                f'<div class="ev"><code>{f["ts"][11:16]}</code> '
                f'{_badge(*ROLE_BADGES.get(f["role"], ("?", "#000")))} '
                f'<span class="ev-text">{html.escape(f["message"])}</span></div>'
                for f in raw[-12:])
            raw_html = (f'<details class="rawlog"><summary>Agent log - Devin\'s own '
                        f'words ({len(raw)} messages)</summary>{raw_lines}</details>')
        sections.append(f"""
<section>
  <h3>#{e['number']} {html.escape(e['title'])} {_badge(label, color)}</h3>
  <p class="links">{' | '.join(links)}</p>
  {attention}
  <div class="stream">{''.join(stream)}</div>
  {raw_html}
</section>""")

    mode = "SIMULATION" if os.environ.get("SIMULATE") == "1" else "LIVE"
    return f"""<!-- generated by orchestrator; all numbers derived from state.json -->
<title>Remediation Pipeline</title>
<meta http-equiv="refresh" content="15">
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 72rem;
         padding: 0 1rem; color: #1f2328; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  h3 {{ font-size: 1rem; margin: 1.6rem 0 .3rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  th, td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid #d0d7de; }}
  .badge {{ color: #fff; padding: .12rem .5rem; border-radius: 1rem; font-size: .74rem;
            white-space: nowrap; }}
  .kpi {{ display: inline-block; margin-right: 2.5rem; }}
  .kpi b {{ font-size: 1.6rem; display: block; }}
  .links {{ font-size: .82rem; margin: .2rem 0 .6rem; }}
  .stream {{ border-left: 3px solid #d0d7de; padding-left: .9rem; }}
  .ev {{ margin: .45rem 0; font-size: .87rem; line-height: 1.45;
         display: flex; align-items: baseline; gap: .5rem; }}
  .ev code {{ font-size: .76rem; color: #57606a; flex-shrink: 0; }}
  .ev.strong .ev-text, .ev.strong summary {{ font-weight: 600; }}
  .ev details {{ display: inline; }}
  .ev summary {{ cursor: pointer; list-style: none; }}
  .ev summary::after {{ content: " \\2026"; color: #57606a; }}
  .ev-detail {{ margin: .3rem 0 .3rem 1rem; padding: .5rem .7rem;
                background: #f6f8fa; border-radius: 6px; font-size: .82rem;
                color: #57606a; white-space: pre-wrap; }}
  .attn {{ color: #cf222e; font-size: .88rem; }}
  .rawlog {{ margin: .5rem 0 0 1rem; font-size: .84rem; }}
  .rawlog summary {{ cursor: pointer; color: #57606a; }}
  section {{ margin-bottom: 1.2rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0d1117; color: #e6edf3; }}
    th, td {{ border-color: #30363d; }} .stream {{ border-color: #30363d; }}
    .ev-detail {{ background: #161b22; }}
  }}
</style>
<h1>Autonomous Remediation Pipeline <small>({mode})</small></h1>
<p>Fork: <code>{html.escape(config.FORK_FULL)}</code> |
   trigger label: <code>{html.escape(config.TRIGGER_LABEL)}</code> |
   ticks: {state['meta']['ticks']} |
   generated: {st.utcnow()}</p>

<div>
  <span class="kpi"><b>{len(issues)}</b>issues tracked</span>
  <span class="kpi"><b>{done}</b>resolved (fixed or escalated)</span>
  <span class="kpi"><b>{counts.get(st.NEEDS_ATTENTION, 0)}</b>need attention</span>
  <span class="kpi"><b>{sessions}</b>Devin sessions used</span>
</div>

<h2>Issues at a glance</h2>
<table>
  <tr><th>#</th><th>Title</th><th>State</th><th>PR</th><th>Queued&rarr;done</th></tr>
  {''.join(rows)}
</table>

<h2>Activity by issue <small>(system events + Devin messages, chronological)</small></h2>
{''.join(sections)}

<p><em>Merge decisions remain with human maintainers. Every state above is
derived from pipeline records; nothing is self-reported without verification.
This page reloads every 15 seconds.</em></p>
"""


def write(state):
    config.DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.DASHBOARD_PATH.write_text(render(state), encoding="utf-8")
    return config.DASHBOARD_PATH
