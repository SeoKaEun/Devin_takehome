"""Dashboard - answers one question for an engineering leader:
"Is this working?" Every number is derived from state.json (real pipeline
records); nothing is estimated or invented.

Visual language follows a validated design system: ink/surface tokens,
reserved status colors always paired with a label (never color alone),
stat-tile contract for headline numbers, hairline chrome, dark mode via
token swap.
"""

import html
import os
from datetime import datetime

from . import config
from . import state as st

# state -> (label, css class for the status dot)
STATE_META = {
    st.QUEUED: ("Queued", "s-muted"),
    st.WORKING: ("In progress", "s-run"),
    st.REVIEW_PENDING: ("Review queued", "s-run"),
    st.REVIEW_WORKING: ("In review", "s-run"),
    st.DONE_FIXED: ("Fixed", "s-good"),
    st.DONE_ESCALATED: ("Escalated", "s-esc"),
    st.NEEDS_ATTENTION: ("Attention", "s-crit"),
}

ROLE_META = {
    "system": ("SYS", "s-muted"),
    "work": ("WORK", "s-run"),
    "review": ("REVIEW", "s-esc"),
}

CSS = """
:root {
  --page: #f9f9f7; --surface: #fcfcfb;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --border: rgba(11,11,11,0.10);
  --good: #0ca30c; --warn: #fab219; --crit: #d03b3b;
  --run: #2a78d6; --esc: #4a3aa7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0d0d0d; --surface: #1a1a19;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --border: rgba(255,255,255,0.10);
    --run: #3987e5; --esc: #9085e9;
  }
}
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       background: var(--page); color: var(--ink); margin: 0;
       font-size: 14px; line-height: 1.5; }
.wrap { max-width: 76rem; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }

header { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
         margin-bottom: 1.25rem; }
header h1 { font-size: 1.05rem; font-weight: 600; margin: 0; }
header .meta { color: var(--muted); font-size: .8rem; }
.pill { display: inline-flex; align-items: center; gap: .45rem;
        border: 1px solid var(--border); border-radius: 999px;
        padding: .25rem .8rem; background: var(--surface);
        font-size: .8rem; font-weight: 600; }
.dot { width: .55rem; height: .55rem; border-radius: 50%; flex-shrink: 0; }
.s-good { background: var(--good); } .s-warn { background: var(--warn); }
.s-crit { background: var(--crit); } .s-run { background: var(--run); }
.s-esc { background: var(--esc); } .s-muted { background: var(--muted); }

.tiles { display: grid; gap: .75rem;
         grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
         margin-bottom: 1.25rem; }
.tile { background: var(--surface); border: 1px solid var(--border);
        border-radius: 8px; padding: .8rem 1rem; }
.tile .label { font-size: .74rem; color: var(--ink-2); }
.tile .value { font-size: 1.7rem; font-weight: 600; line-height: 1.2;
               display: flex; align-items: center; gap: .5rem; }
.tile .value .dot { width: .5rem; height: .5rem; }

.panels { display: grid; gap: .75rem;
          grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
          margin-bottom: 1.5rem; }
.panel { background: var(--surface); border: 1px solid var(--border);
         border-radius: 8px; padding: .7rem 1rem .4rem; }
.panel h2 { font-size: .74rem; font-weight: 600; letter-spacing: .05em;
            text-transform: uppercase; color: var(--muted); margin: 0 0 .3rem; }
.panel table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.panel td { padding: .34rem 0; border-top: 1px solid var(--grid); }
.panel tr:first-child td { border-top: none; }
.panel td.v { text-align: right; font-weight: 600;
              font-variant-numeric: tabular-nums; white-space: nowrap; }
.panel td.c { width: 4.4rem; text-align: right; }
.tag { display: inline-flex; align-items: center; gap: .35rem;
       font-size: .66rem; font-weight: 600; letter-spacing: .04em;
       color: var(--ink-2); border: 1px solid var(--border);
       border-radius: 999px; padding: .06rem .5rem; background: var(--page); }

h2.sec { font-size: .74rem; font-weight: 600; letter-spacing: .05em;
         text-transform: uppercase; color: var(--muted); margin: 1.6rem 0 .5rem; }
.issues { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; overflow-x: auto; }
.issues table { width: 100%; border-collapse: collapse; font-size: .84rem; }
.issues th { text-align: left; font-size: .7rem; text-transform: uppercase;
             letter-spacing: .05em; color: var(--muted); font-weight: 600;
             padding: .55rem .9rem; border-bottom: 1px solid var(--grid); }
.issues td { padding: .55rem .9rem; border-bottom: 1px solid var(--grid);
             vertical-align: top; }
.issues tr:last-child td { border-bottom: none; }
.issues td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
a { color: var(--run); text-decoration: none; }
a:hover { text-decoration: underline; }

section.issue { background: var(--surface); border: 1px solid var(--border);
                border-radius: 8px; padding: .9rem 1.1rem; margin-bottom: .75rem; }
section.issue h3 { font-size: .9rem; font-weight: 600; margin: 0;
                   display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; }
section.issue .links { font-size: .78rem; color: var(--muted);
                       margin: .3rem 0 .6rem; }
.attnbox { border-left: 3px solid var(--crit); padding: .3rem .7rem;
           font-size: .82rem; margin: .5rem 0; color: var(--ink-2); }
.stream { border-left: 2px solid var(--grid); padding-left: .9rem;
          margin-top: .5rem; }
.ev { display: flex; gap: .55rem; align-items: baseline; margin: .4rem 0;
      font-size: .82rem; }
.ev time { color: var(--muted); font-size: .72rem;
           font-variant-numeric: tabular-nums; flex-shrink: 0; }
.ev .tag { flex-shrink: 0; }
.ev.strong { font-weight: 600; }
.ev details { display: inline; }
.ev summary { cursor: pointer; list-style: none; }
.ev summary::after { content: " \\2026"; color: var(--muted); }
.ev-detail { margin: .3rem 0; padding: .5rem .7rem; background: var(--page);
             border-radius: 6px; font-size: .78rem; color: var(--ink-2);
             font-weight: 400; white-space: pre-wrap; }
.outcome { margin: .5rem 0 .3rem; font-size: .84rem; }
.o-row { display: grid; grid-template-columns: 4.6rem 1fr; gap: .8rem;
         padding: .3rem 0; border-top: 1px solid var(--grid); }
.o-row:first-child { border-top: none; }
.o-label { font-size: .68rem; font-weight: 600; letter-spacing: .05em;
           text-transform: uppercase; color: var(--muted); padding-top: .1rem; }
.o-list { margin: 0; padding-left: 1.1rem; }
.o-list li { margin: .12rem 0; }
.o-stats { color: var(--muted); font-size: .76rem; margin-top: .25rem; }
.rawlog { margin-top: .6rem; font-size: .8rem; }
.rawlog summary { cursor: pointer; color: var(--muted); }
.stale { display: none; border: 1px solid var(--crit); border-left-width: 5px;
         border-radius: 6px; padding: .6rem 1rem; margin-bottom: 1rem;
         font-size: .85rem; background: var(--surface); }
footer { color: var(--muted); font-size: .76rem; margin-top: 2rem; }
"""


def _fmt_duration(start_iso, end_iso):
    if not start_iso or not end_iso:
        return "-"
    delta = datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)
    m = int(delta.total_seconds() // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def _issue_finished_at(e):
    for terminal in (st.DONE_FIXED, st.DONE_ESCALATED, st.NEEDS_ATTENTION):
        if e.get(f"{terminal}_at"):
            return e[f"{terminal}_at"]
    return None


def _status(state_key):
    label, cls = STATE_META.get(state_key, (state_key, "s-muted"))
    return f'<span class="pill"><span class="dot {cls}"></span>{html.escape(label)}</span>'


def _tag(text, cls):
    return (f'<span class="tag"><span class="dot {cls}"></span>'
            f'{html.escape(text)}</span>')


# --- per-issue activity stream (headlines curated at record time) -----------

def _system_headline(event):
    table = [
        ("discovered", "Detected and queued", False),
        ("state: queued -> working", "Work session started", False),
        ("state: working -> review_pending", "Gate 1 passed", True),
        ("state: review_pending -> review_working", "Independent review started", False),
        ("state: review_working -> done_fixed", "Review approved - resolved", True),
        ("state: review_working -> working", "Review rejected - rework", True),
        ("state: working -> done_escalated", "Escalated to human decision", True),
        ("-> needs_attention", "Stopped - human attention", True),
        ("state: needs_attention -> working", "Human decision - resumed", True),
        ("gate1", "Gate 1", False),
        ("scope_auto_accepted", "Scope growth auto-accepted", False),
        ("auto_merged", "PR auto-merged", True),
        ("review_verdict", "Review verdict", True),
        ("nudged", "Idle session nudged", False),
        ("rework_dispatched", "Findings sent back for rework", True),
        ("manual:", "Human decision", True),
        ("tick_error", "Pipeline error (isolated)", True),
    ]
    for needle, headline, strong in table:
        if needle in event:
            return headline, strong
    return event, False


def _merged_stream(state, key, entry, limit=25):
    items = []
    for t in entry.get("timeline", []):
        label, strong = _system_headline(t["event"])
        detail = (t.get("detail") or "").replace("\n", " ").strip()
        snippet = detail[:160] + ("..." if len(detail) > 160 else "")
        items.append({"ts": t["ts"], "headline": label, "snippet": snippet,
                      "detail": detail if len(detail) > 160 else "",
                      "strong": strong})
    items.sort(key=lambda x: x["ts"])
    return items[-limit:]


def _raw_agent_log(state, key):
    return [f for f in state.get("feed", []) if str(f["issue"]) == key]


def render(state):
    issues = state["issues"]
    counts = {}
    for e in issues.values():
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    done = counts.get(st.DONE_FIXED, 0) + counts.get(st.DONE_ESCALATED, 0)
    n_attention = counts.get(st.NEEDS_ATTENTION, 0)
    active_now = sum(1 for e in issues.values() if e["state"] in st.ACTIVE_STATES)
    in_progress = (active_now + counts.get(st.QUEUED, 0)
                   + counts.get(st.REVIEW_PENDING, 0))
    n_work = sum(bool(e.get("work_session")) for e in issues.values())
    n_review = sum(bool(e.get("review_session")) for e in issues.values())

    interventions = sum(
        1 for e in issues.values() for t in e.get("timeline", [])
        if t["event"].startswith("manual:"))
    review_catches = sum(
        1 for e in issues.values() for t in e.get("timeline", [])
        if t["event"] == "rework_dispatched")
    rework_rounds = sum(e.get("rework_count", 0) for e in issues.values())

    done_minutes = []
    for e in issues.values():
        fin = _issue_finished_at(e)
        if fin and e["state"] in (st.DONE_FIXED, st.DONE_ESCALATED):
            delta = (datetime.fromisoformat(fin)
                     - datetime.fromisoformat(e["queued_at"]))
            done_minutes.append(delta.total_seconds() / 60)
    avg_done = (f"{int(sum(done_minutes) / len(done_minutes))}m"
                if done_minutes else "-")
    acu_ceiling = (n_work * config.MAX_ACU_PER_SESSION
                   + n_review * max(2, config.MAX_ACU_PER_SESSION // 2))

    meta = state.get("meta", {})
    tick_errors = meta.get("tick_errors", 0)

    if n_attention > 0:
        pill = f'<span class="pill"><span class="dot s-crit"></span>Action required</span>'
    elif tick_errors > 0:
        pill = f'<span class="pill"><span class="dot s-warn"></span>Degraded</span>'
    else:
        pill = f'<span class="pill"><span class="dot s-good"></span>Operational</span>'

    def tile(value, label, dot_cls=None):
        dot = f'<span class="dot {dot_cls}"></span>' if dot_cls else ""
        return (f'<div class="tile"><div class="value">{dot}{value}</div>'
                f'<div class="label">{html.escape(label)}</div></div>')

    tiles = "".join([
        tile(counts.get(st.DONE_FIXED, 0), "Fixed, PR verified", "s-good"),
        tile(counts.get(st.DONE_ESCALATED, 0), "Escalated with evidence", "s-esc"),
        tile(n_attention, "Needs attention",
             "s-crit" if n_attention else "s-good"),
        tile(in_progress, "In progress", "s-run" if in_progress else None),
        tile(avg_done, "Avg resolution"),
        tile(f"&le;{acu_ceiling}", "ACU ceiling"),
    ])

    def prow(label, value, tag_html=""):
        return (f"<tr><td>{html.escape(label)}</td>"
                f'<td class="v">{value}</td><td class="c">{tag_html}</td></tr>')

    ok, warn, act = (_tag("OK", "s-good"), _tag("WARN", "s-warn"),
                     _tag("ACT", "s-crit"))
    control_panel = "".join([
        prow("Review rejections caught", review_catches),
        prow("Autonomous rework rounds",
             f"{rework_rounds} / {config.POLICY['max_reworks']} per issue", ok),
        prow("Human decisions logged", interventions),
        prow("Sessions (work / review)", f"{n_work} / {n_review}"),
        prow("Autonomy mode", html.escape(config.AUTONOMY_MODE)),
    ])
    scan_filed = meta.get("last_scan_filed", 0) or 0
    ops_panel = "".join([
        prow("Last tick (UTC)", meta.get("last_tick_at", "-")[11:19] or "-", ok),
        prow("Last scan (UTC)",
             (meta.get("last_scan_at") or "-")[11:19] + f" &middot; +{scan_filed}",
             ok if meta.get("last_scan_at") else warn),
        prow("Pipeline errors", tick_errors, warn if tick_errors else ok),
        prow("Concurrent sessions",
             f"{active_now} / {config.MAX_CONCURRENT_SESSIONS}",
             ok if active_now <= config.MAX_CONCURRENT_SESSIONS else act),
    ])

    # --- issues table -------------------------------------------------------
    rows = []
    for e in sorted(issues.values(), key=lambda x: x["number"]):
        pr = (f'<a href="{html.escape(e["pr_url"])}">view</a>'
              if e.get("pr_url") else '<span style="color:var(--muted)">-</span>')
        rows.append(
            f'<tr><td class="num"><a href="{html.escape(e["url"])}">'
            f"#{e['number']}</a></td>"
            f"<td>{html.escape(e['title'])}</td>"
            f"<td>{_status(e['state'])}</td><td>{pr}</td>"
            f'<td class="num">{_fmt_duration(e.get("queued_at"), _issue_finished_at(e))}</td></tr>')

    # --- per-issue activity -------------------------------------------------

    def _outcome_block(e):
        """Problem -> Fix -> Result: the substance, not the process."""
        title = e["title"]
        for prefix in ("[security] ", "[code-quality] "):
            title = title.replace(prefix, "")
        problem = title

        out = e.get("work_output") or {}
        review = e.get("review_output") or {}
        fix = result = None

        def _bullets(text_or_list, cap=4, width=140):
            """Scannable facts: structured bullets when the contract provided
            them, sentence-split as fallback for prose. Full text collapses."""
            if isinstance(text_or_list, list):
                items = text_or_list
            else:
                items = [s.strip() for s in (text_or_list or "").split(". ")
                         if s.strip()]
            shown = [i[:width] + ("..." if len(i) > width else "")
                     for i in items[:cap]]
            lis = "".join(f"<li>{html.escape(i)}</li>" for i in shown)
            more = ""
            if len(items) > cap or any(len(i) > width for i in items[:cap]):
                full = text_or_list if isinstance(text_or_list, str) \
                    else " ".join(items)
                more = (f'<details class="rawlog"><summary>full detail</summary>'
                        f'<div class="ev-detail">{html.escape(full)}</div></details>')
            return f'<ul class="o-list">{lis}</ul>{more}'

        if out.get("outcome") == "fixed":
            files = out.get("files_changed") or []
            tests = out.get("tests_run") or []
            facts = out.get("change_summary") or out.get("summary")
            stats = (f'<div class="o-stats">{len(files)} files changed'
                     + (f" &middot; {html.escape(tests[0])}" if tests else "")
                     + "</div>")
            fix = _bullets(facts) + stats
        elif out.get("outcome") == "blocked":
            fix = ("No safe fix exists within constraints."
                   + _bullets(out.get("blocking_reason") or ""))

        s = e["state"]
        if s == st.DONE_FIXED:
            verdict = (review.get("summary") or "approved")[:110]
            result = (f'<a href="{html.escape(e.get("pr_url") or "#")}">PR</a> '
                      f"approved by independent review &mdash; "
                      f"{html.escape(verdict)}. Awaiting human merge.")
        elif s == st.DONE_ESCALATED:
            parts = []
            if out.get("mitigation"):
                parts.append("Mitigation: " + out["mitigation"])
            if out.get("advisory_status"):
                parts.append("Advisory status: " + out["advisory_status"])
            result = ("Decision package delivered - awaiting owner decision."
                      + _bullets(". ".join(parts) or "See issue comment"))
        elif s == st.NEEDS_ATTENTION:
            result = "Stopped for a human decision: " + html.escape(
                e.get("attention_reason") or "")
        else:
            result = "In progress - no result yet."

        rows = [("Problem", html.escape(problem))]
        if fix:
            rows.append(("Fix", fix))
        rows.append(("Result", result))
        return '<div class="outcome">' + "".join(
            f'<div class="o-row"><span class="o-label">{lbl}</span>'
            f"<span>{val}</span></div>" for lbl, val in rows) + "</div>"

    sections = []
    for key in sorted(issues, key=int):
        e = issues[key]
        ws, rs = e.get("work_session") or {}, e.get("review_session") or {}
        links = []
        if ws.get("url", "").startswith("http"):
            links.append(f'<a href="{html.escape(ws["url"])}">work session</a>')
        if rs.get("url", "").startswith("http"):
            links.append(f'<a href="{html.escape(rs["url"])}">review session</a>')
        if e.get("pr_url"):
            links.append(f'<a href="{html.escape(e["pr_url"])}">pull request</a>')
        links.append(f'<a href="{html.escape(e["url"])}">issue</a>')

        stream = []
        for item in _merged_stream(state, key, e):
            t = item["ts"][11:16]
            cls = "ev strong" if item["strong"] else "ev"
            text = item["headline"] + (
                f' <span style="font-weight:400;color:var(--ink-2)">'
                f'&middot; {html.escape(item["snippet"])}</span>'
                if item["snippet"] else "")
            if item["detail"]:
                body = (f"<details><summary>{text}</summary>"
                        f'<div class="ev-detail">{html.escape(item["detail"])}</div>'
                        f"</details>")
            else:
                body = f"<span>{text}</span>"
            stream.append(f'<div class="{cls}"><time>{t}</time>{body}</div>')

        attention = (f'<div class="attnbox">{html.escape(e.get("attention_reason") or "")}'
                     f"</div>" if e.get("attention_reason") else "")
        raw = _raw_agent_log(state, key)
        raw_html = ""
        if raw:
            raw_lines = "".join(
                f'<div class="ev"><time>{f["ts"][11:16]}</time>'
                f'{_tag(*ROLE_META.get(f["role"], ("?", "s-muted")))}'
                f"<span>{html.escape(f['message'])}</span></div>"
                for f in raw[-12:])
            raw_html = (f'<details class="rawlog"><summary>Agent log '
                        f"({len(raw)} messages)</summary>{raw_lines}</details>")
        sections.append(f"""
<section class="issue">
  <h3>#{e['number']} {html.escape(e['title'])} {_status(e['state'])}</h3>
  <div class="links">{' &middot; '.join(links)}</div>
  {_outcome_block(e)}
  {attention}
  <details class="rawlog"><summary>Timeline ({len(e.get('timeline', []))} events)</summary>
  <div class="stream">{''.join(stream)}</div></details>
  {raw_html}
</section>""")

    mode = "SIMULATION" if os.environ.get("SIMULATE") == "1" else "LIVE"
    generated = st.utcnow()
    return f"""<title>Remediation Pipeline</title>
<meta http-equiv="refresh" content="15">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
<div class="wrap">
<header>
  <h1>Remediation Pipeline</h1>
  {pill}
  <span class="meta">{html.escape(config.FORK_FULL)} &middot; {mode} &middot;
  {done}/{len(issues)} resolved &middot; updated {generated[11:19]} UTC</span>
</header>

<div id="stale" class="stale">
  <b>Stale.</b> No update for over 3 minutes - the orchestrator loop may be down.
</div>

<div class="tiles">{tiles}</div>

<div class="panels">
  <div class="panel"><h2>Quality &amp; control</h2><table>{control_panel}</table></div>
  <div class="panel"><h2>Operations</h2><table>{ops_panel}</table></div>
</div>

<h2 class="sec">Issues</h2>
<div class="issues">
<table>
  <tr><th>#</th><th>Title</th><th>Status</th><th>PR</th><th>Duration</th></tr>
  {''.join(rows)}
</table>
</div>

<h2 class="sec">Activity</h2>
{''.join(sections)}

<footer>All figures derived from pipeline records. Merge decisions remain with
human maintainers. Auto-refreshes every 15 seconds.</footer>
</div>
<script>
  (function () {{
    var gen = new Date("{generated}").getTime();
    if (Date.now() - gen > 3 * 60 * 1000)
      document.getElementById("stale").style.display = "block";
  }})();
</script>
"""


def write(state):
    config.DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.DASHBOARD_PATH.write_text(render(state), encoding="utf-8")
    return config.DASHBOARD_PATH
