"""The usage report: one self-contained HTML file, opened in the browser.

No external assets. The page is written to disk and opened with file://, where a
CDN fetch or a webfont would simply not load, so every style and every bar is
inline.

What it answers, in order: how much did this cost, how is it trending by day,
how often does the five-hour window reset, and where does the spend actually go.
"""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from pathlib import Path

from code_agent_switcher import usage_log
from code_agent_switcher.ledger import (
    assumed_models,
    rate_for,
    Record,
    Totals,
    Window,
    by_day,
    by_model,
    by_hour,
    by_provider,
    by_weekday,
    tokens_between,
    tokens_between_for,
    by_project,
    session_windows,
    unpriced_models,
    windows_per_day,
)

REPORT_PATH = Path.home() / "Library" / "Application Support" / "Code Agent Switcher" / "usage.html"

INK = "#241F2C"
EMBER = "#D4633F"
PLUM = "#6B5B95"
TEAL = "#2E7D7B"
SAND = "#C9A227"


def _n(value: int) -> str:
    return f"{value:,}"


def _money(value: float) -> str:
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.3f}"


def _tokens(value: int) -> str:
    """Short form. A report full of ten-digit numbers is unreadable."""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if value >= limit:
            return f"{value / limit:.1f}{suffix}"
    return str(value)


def _stack(parts: list[tuple[str, int, str]], scale: int) -> str:
    """One horizontal stacked bar. parts is (label, value, colour)."""
    if scale <= 0:
        return '<div class="bar"></div>'
    cells = "".join(
        f'<span style="width:{value / scale * 100:.4f}%;background:{colour}" '
        f'title="{html.escape(label)}: {_n(value)}"></span>'
        for label, value, colour in parts
        if value > 0
    )
    return f'<div class="bar">{cells}</div>'


def _day_section(days: dict[date, Totals], per_day: dict[date, list[Window]]) -> str:
    if not days:
        return "<p class=empty>No records in this range.</p>"
    scale = max(t.billable for t in days.values())
    rows = []
    for day, t in reversed(list(days.items())):
        windows = per_day.get(day, [])
        resets = len(windows)
        peak = max((w.totals.billable for w in windows), default=0)
        bar = _stack(
            [
                ("cache read", t.cache_read, PLUM),
                ("cache write", t.cache_write, EMBER),
                ("input", t.input, TEAL),
                ("output", t.output, SAND),
            ],
            scale,
        )
        rows.append(f"""<tr>
<th scope=row>{day:%a %d %b}</th>
<td class=bars>{bar}</td>
<td class=num>{_tokens(t.billable)}</td>
<td class=num>{_n(t.messages)}</td>
<td class=num>{resets or "&ndash;"}</td>
<td class=num>{_tokens(peak) if peak else "&ndash;"}</td>
<td class="num money">{_money(t.dollars)}</td>
</tr>""")
    return f"""<table>
<thead><tr><th scope=col>Day</th><th scope=col>Where the tokens went</th>
<th scope=col class=num>Tokens</th><th scope=col class=num>Msgs</th>
<th scope=col class=num>Windows</th><th scope=col class=num>Biggest</th>
<th scope=col class=num>Est. cost</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""


def _window_section(windows: list[Window]) -> str:
    if not windows:
        return "<p class=empty>No windows in this range.</p>"
    recent = windows[-24:]
    scale = max(w.totals.billable for w in recent)
    rows = []
    for w in reversed(recent):
        bar = _stack([("tokens", w.totals.billable, EMBER)], scale)
        used = w.minutes_used
        rows.append(f"""<tr>
<th scope=row>{w.start.astimezone():%a %d %b, %H:%M}</th>
<td class=bars>{bar}</td>
<td class=num>{_tokens(w.totals.billable)}</td>
<td class=num>{_n(w.totals.messages)}</td>
<td class=num>{used / 60:.1f}h</td>
<td class="num money">{_money(w.totals.dollars)}</td>
</tr>""")
    return f"""<table>
<thead><tr><th scope=col>Window opened</th><th scope=col>Tokens in the window</th>
<th scope=col class=num>Tokens</th><th scope=col class=num>Msgs</th>
<th scope=col class=num>Active</th><th scope=col class=num>Est. cost</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""


def _totals_table(groups: dict[str, Totals], heading: str) -> str:
    if not groups:
        return ""
    scale = max(t.billable for t in groups.values())
    rows = []
    for name, t in groups.items():
        unpriced = ""
        if heading == "Model":
            rate, guessed = rate_for(name)
            if rate is None:
                unpriced = " <span class=flag>no rate</span>"
            elif guessed:
                unpriced = " <span class=flag>assumed</span>"
        rows.append(f"""<tr>
<th scope=row><code>{html.escape(name)}</code>{unpriced}</th>
<td class=bars>{_stack([("tokens", t.billable, PLUM)], scale)}</td>
<td class=num>{_n(t.messages)}</td>
<td class=num>{_tokens(t.input)}</td>
<td class=num>{_tokens(t.output)}</td>
<td class=num>{_tokens(t.cache_write)}</td>
<td class=num>{_tokens(t.cache_read)}</td>
<td class="num money">{_money(t.dollars)}</td>
</tr>""")
    return f"""<table>
<thead><tr><th scope=col>{heading}</th><th scope=col>Share</th>
<th scope=col class=num>Msgs</th><th scope=col class=num>In</th>
<th scope=col class=num>Out</th><th scope=col class=num>Cache w</th>
<th scope=col class=num>Cache r</th><th scope=col class=num>Est. cost</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _profile(buckets: dict, labels, heading: str, note_col: str) -> str:
    """A simple ranked profile: one row per bucket, bar scaled to the biggest."""
    scale = max((t.billable for t in buckets.values()), default=0)
    rows = []
    for key, t in buckets.items():
        rows.append(f"""<tr>
<th scope=row>{labels(key)}</th>
<td class=bars>{_stack([("tokens", t.billable, TEAL)], scale)}</td>
<td class=num>{_tokens(t.billable) if t.billable else "&ndash;"}</td>
<td class=num>{_n(t.messages) if t.messages else "&ndash;"}</td>
<td class="num money">{_money(t.dollars) if t.dollars else "&ndash;"}</td>
</tr>""")
    return f"""<table>
<thead><tr><th scope=col>{heading}</th><th scope=col>{note_col}</th>
<th scope=col class=num>Tokens</th><th scope=col class=num>Msgs</th>
<th scope=col class=num>Est. cost</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""


def _rates(records: list[Record], samples: list):
    """Tokens per one percent, per provider, account and window.

    Each rise in the reported figure is paired with the tokens the SAME agent
    spent in that interval - a Codex window priced with Claude's tokens would
    be a number about nothing.
    """
    grouped: dict[tuple[str, str, str, str], list] = {}
    for step in usage_log.steps(samples):
        grouped.setdefault(
            (step.provider, step.account, step.plan, step.label), []
        ).append(step)

    out = {}
    for key, group in grouped.items():
        percent = sum(g.delta_percent for g in group)
        if percent <= 0:
            continue
        tokens = sum(
            tokens_between_for(
                records, g.at - timedelta(minutes=g.minutes), g.at, key[0]
            )
            for g in group
        )
        # No tokens in the intervals where the window moved means the work did
        # not come from a transcript this Mac holds, not that it was free.
        out[key] = (tokens / percent if tokens else None, percent, len(group))
    return out


def _budget_section(records: list[Record], samples: list) -> str:
    if not samples:
        return ("<p class=empty>No readings yet. The app records one every "
                "fifteen seconds while the panel is open and every five minutes "
                "otherwise; come back tomorrow and this fills in.</p>")
    rates = _rates(records, samples)
    if not rates:
        return ("<p class=empty>Readings are being collected but none of them rose "
                "while the account was in use - that pairing is what makes this "
                "measurable.</p>")
    now = usage_log.latest(samples)
    rows = []
    for (provider, account, plan, label), (per_point, percent, count) in sorted(rates.items()):
        used = now.get((provider, account, label))
        # Under ten points observed the rate is one or two samples wide and says
        # more about when the app was running than about the allowance.
        note = "measured" if percent >= 10 else "thin"
        whole = left = used_cell = "&ndash;"
        if per_point is None:
            # The window moved but no transcript on this Mac accounts for it -
            # Codex used from somewhere other than the CLI, say. Reporting zero
            # tokens for a window that demonstrably moved would be a lie.
            note = "no local transcripts"
        else:
            whole = _tokens(int(per_point * 100))
            if used is not None:
                left = _tokens(int((100.0 - used) * per_point))
        if used is not None:
            used_cell = f"{used:.0f}%"
        rows.append(f"""<tr>
<th scope=row>{html.escape(account)}</th>
<td>{html.escape(provider)}</td>
<td>{html.escape(plan)}</td>
<td>{html.escape(label)}</td>
<td class=num>{whole}</td>
<td class=num>{left}</td>
<td class=num>{used_cell}</td>
<td class=num>{_tokens(int(per_point)) if per_point else "&ndash;"}</td>
<td class=num>{percent:.0f}% / {count} <span class=flag>{note}</span></td>
</tr>""")
    return f"""<table>
<thead><tr><th scope=col>Account</th><th scope=col>Agent</th><th scope=col>Plan</th>
<th scope=col>Window</th><th scope=col class=num>Whole window</th>
<th scope=col class=num>Left now</th><th scope=col class=num>Used</th>
<th scope=col class=num>Per 1%</th><th scope=col class=num>Observed</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""


def _rate_by_hour_section(records: list[Record], samples: list, label: str) -> str:
    """Does a percentage point buy fewer tokens at some hours than others?

    Same arithmetic as the budget table, bucketed by the local hour the rise was
    observed in. If the allowance is metered the same way all day, the column is
    flat; if it is not, this is where it shows.
    """
    buckets: dict[int, list[float]] = {}
    for step in usage_log.steps(samples):
        if step.label != label:
            continue
        spent = tokens_between_for(
            records, step.at - timedelta(minutes=step.minutes), step.at, step.provider
        )
        hour = step.at.astimezone().hour
        entry = buckets.setdefault(hour, [0.0, 0.0, 0])
        entry[0] += spent
        entry[1] += step.delta_percent
        entry[2] += 1
    usable = {h: v for h, v in buckets.items() if v[1] > 0}
    if not usable:
        return ("<p class=empty>Not enough readings yet to compare hours. This "
                "needs rises observed at several times of day.</p>")
    scale = max(v[0] / v[1] for v in usable.values())
    rows = []
    for hour in sorted(usable):
        spent, percent, count = usable[hour]
        per_point = spent / percent
        rows.append(f"""<tr>
<th scope=row>{hour:02d}:00</th>
<td class=bars>{_stack([("tokens per 1%", int(per_point), SAND)], int(scale))}</td>
<td class=num>{_tokens(int(per_point))}</td>
<td class=num>{percent:.0f}%</td>
<td class=num>{count}</td>
</tr>""")
    return f"""<table>
<thead><tr><th scope=col>Hour</th><th scope=col>Tokens per 1% of the {html.escape(label)} window</th>
<th scope=col class=num>Per 1%</th><th scope=col class=num>Observed</th>
<th scope=col class=num>Steps</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""


CSS = """
:root {
  --ink:#241F2C; --muted:#6E687A; --line:#E2DEE6; --ground:#FBF9F7;
  --card:#FFFFFF; --ember:#D4633F; --track:#EFEBF1;
}
:root:not([data-theme=light]) { }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme=light]) {
    --ink:#EDE9F2; --muted:#9A94A6; --line:#332E3C; --ground:#151319;
    --card:#1D1A22; --track:#2A2632;
  }
}
* { box-sizing:border-box }
body { margin:0; background:var(--ground); color:var(--ink);
  font:14px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing:antialiased }
main { max-width:1080px; margin:0 auto; padding:40px 24px 80px }
h1 { font-size:26px; letter-spacing:-0.4px; margin:0 0 4px }
h2 { font-size:15px; letter-spacing:0.5px; text-transform:uppercase;
  color:var(--muted); margin:44px 0 12px; font-weight:600 }
.sub { color:var(--muted); margin:0 0 28px }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px }
.card .k { color:var(--muted); font-size:12px; letter-spacing:0.3px; text-transform:uppercase }
.card .v { font-size:24px; font-variant-numeric:tabular-nums; margin-top:4px; letter-spacing:-0.5px }
.card .v small { font-size:13px; color:var(--muted); letter-spacing:0 }
.wrap { overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--card) }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums }
th, td { padding:7px 12px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap }
thead th { font-size:11px; letter-spacing:0.4px; text-transform:uppercase;
  color:var(--muted); font-weight:600 }
tbody tr:last-child th, tbody tr:last-child td { border-bottom:0 }
tbody th { font-weight:500 }
.num { text-align:right }
.money { font-weight:600 }
.bars { width:46%; min-width:180px }
.bar { display:flex; height:9px; border-radius:5px; overflow:hidden; background:var(--track) }
.bar span { display:block; height:100% }
.legend { display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); font-size:12px; margin:10px 2px 0 }
.legend i { display:inline-block; width:9px; height:9px; border-radius:3px; margin-right:6px }
code { font:12px/1 ui-monospace, SFMono-Regular, Menlo, monospace }
.flag { font-size:11px; color:var(--ember); border:1px solid var(--ember);
  border-radius:4px; padding:1px 5px; margin-left:6px }
.note { color:var(--muted); font-size:12.5px; margin:10px 2px 0 }
.empty { color:var(--muted) }
"""


def render(records: list[Record], generated: datetime | None = None) -> str:
    generated = generated or datetime.now().astimezone()
    samples = usage_log.load()
    windows = session_windows(records)
    days = by_day(records)
    per_day = windows_per_day(windows)
    models = by_model(records)
    providers = by_provider(records)

    total = Totals()
    for r in records:
        total.add(r)

    span = f"{min(days):%d %b} to {max(days):%d %b %Y}" if days else "no data"
    active_days = len(days) or 1
    def _names(models: list[str]) -> str:
        return ", ".join(f"<code>{html.escape(m)}</code>" for m in models)

    caveats = []
    assumed = assumed_models(records)
    unpriced = unpriced_models(records)
    if assumed:
        caveats.append(
            f"No published rate for {_names(assumed)}, so they are charged at their "
            f"family's rate. Marked <span class=flag>assumed</span> below."
        )
    if unpriced:
        caveats.append(
            f"No rate at all for {_names(unpriced)} - those tokens are counted and "
            f"cost nothing here."
        )
    unpriced_note = (
        "<p class=note>Priced at Anthropic list rates. "
        + " ".join(caveats)
        + " Treat the total as an order of magnitude against a subscription, "
        "not an invoice.</p>"
    )

    legend = (
        f'<div class=legend><span><i style="background:{PLUM}"></i>cache read</span>'
        f'<span><i style="background:{EMBER}"></i>cache write</span>'
        f'<span><i style="background:{TEAL}"></i>input</span>'
        f'<span><i style="background:{SAND}"></i>output</span></div>'
    )

    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Agent usage</title><style>{CSS}</style></head>
<body><main>
<h1>Agent usage</h1>
<p class=sub>{span} &middot; read from the Claude Code and Codex transcripts on this Mac
&middot; generated {generated:%d %b %Y, %H:%M}</p>

<div class=cards>
  <div class=card><div class=k>Est. cost</div><div class=v>{_money(total.dollars)}</div></div>
  <div class=card><div class=k>Per day</div><div class=v>{_money(total.dollars / active_days)}</div></div>
  <div class=card><div class=k>Tokens</div><div class=v>{_tokens(total.billable)}</div></div>
  <div class=card><div class=k>Messages</div><div class=v>{_n(total.messages)}</div></div>
  <div class=card><div class=k>Windows</div><div class=v>{len(windows)}
    <small>{len(windows) / active_days:.1f}/day</small></div></div>
  <div class=card><div class=k>Cache read</div><div class=v>
    {total.cache_read / total.billable * 100 if total.billable else 0:.0f}%
    <small>of all tokens</small></div></div>
</div>
{unpriced_note}

<h2>By day</h2>
<div class=wrap>{_day_section(days, per_day)}</div>
{legend}
<p class=note>A window is one five-hour limit period, reconstructed from the timestamps:
it opens on a message and the next message five hours later opens a new one. The count
is the number of resets. Anthropic publishes only the window you are in right now, so
this is read from your own transcripts, not from them.</p>

<h2>Recent windows</h2>
<div class=wrap>{_window_section(windows)}</div>
<p class=note>Active is the span from the first message in the window to the last, not
the full five hours. A short span with a big number is a burst; a long span means the
window was open most of its life.</p>

<h2>Time of day</h2>
<div class=wrap>{_profile(by_hour(records), lambda h: f"{h:02d}:00", "Hour", "Tokens by hour")}</div>
<p class=note>Local time. This is when you work, not a statement about what the
limits do; pair it with the window table above to see whether a heavy hour is
also where a window runs out.</p>

<h2>By weekday</h2>
<div class=wrap>{_profile(by_weekday(records), lambda d: WEEKDAYS[d], "Day", "Tokens by weekday")}</div>

<h2>Observed budget</h2>
<div class=wrap>{_budget_section(records, samples)}</div>
<p class=note><b>Per 1%</b> is the tokens spent while the reported figure rose one
point. <b>Whole window</b> is that times a hundred: what the allowance is worth at
the rate you are actually working, which is the answer to "what would I get if I
used it up". <b>Left now</b> prices the unused part of the latest reading.</p>
<p class=note>Every row is measured on this Mac, not published by anyone.
<i>Thin</i> means under ten points of movement seen so far, which is one or two
samples and says more about when the app was running than about the allowance.
<i>No local transcripts</i> means the window moved but nothing on this Mac
accounts for it - Codex driven from somewhere other than the CLI does that - so
the rate is unknown rather than zero. It also cannot be backfilled: the
transcripts carry no account attribution, so only readings taken from this
version onwards can be split by account.</p>

<h2>What a percentage point buys, by hour</h2>
<div class=wrap>{_rate_by_hour_section(records, samples, "5h")}</div>
<p class=note>The same arithmetic as the table above, bucketed by the local hour
the rise was observed in. A flat column means the allowance is metered the same
way all day. It is not a measure of how hard you worked at that hour - that is
the Time of day table - it is how much work one point of the window paid for.</p>

<h2>By project</h2>
<div class=wrap>{_totals_table(by_project(records), "Project")}</div>
<p class=note>The working directory each message was sent from, which both
agents record, so nothing has to be tagged by hand.</p>

<h2>By model</h2>
<div class=wrap>{_totals_table(models, "Model")}</div>

<h2>By agent</h2>
<div class=wrap>{_totals_table(providers, "Agent")}</div>
</main></body></html>"""


def write_report(records: list[Record], path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(records), encoding="utf-8")
    return path
