#!/usr/bin/env python3
"""Pull activities and daily steps from Garmin Connect into CSV and an HTML dashboard.

Uses the tokens saved by example.py (~/.garminconnect), so no password is asked.
Output goes to your_data/, which is gitignored: keep it out of this public fork.

    python garmin_tracker.py              # last 365 days, view switchable in the page
    python garmin_tracker.py --days 730   # longer window
    python garmin_tracker.py --no-open    # do not open the dashboard
"""

import argparse
import csv
import json
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

OUT = Path(__file__).parent / "your_data"
TOKENS = Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser()

ACTIVITY_FIELDS = [
    "date", "name", "type", "distance_km", "duration_min", "pace_min_km",
    "avg_hr", "max_hr", "calories", "elevation_gain_m",
]
STEP_FIELDS = ["date", "steps", "distance_km", "step_goal"]


def login() -> Garmin:
    try:
        garmin = Garmin()
        garmin.login(str(TOKENS))
        return garmin
    except GarminConnectTooManyRequestsError as err:
        sys.exit(f"Rate limit: {err}")
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as err:
        sys.exit(f"Not logged in ({err}). Run `python example.py` once to log in again.")


def activity_row(a: dict) -> dict:
    meters = a.get("distance") or 0
    seconds = a.get("duration") or 0
    km = meters / 1000
    return {
        "date": (a.get("startTimeLocal") or "")[:16],
        "name": a.get("activityName") or "",
        "type": (a.get("activityType") or {}).get("typeKey", ""),
        "distance_km": round(km, 2),
        "duration_min": round(seconds / 60, 1),
        # decimal minutes per km (5.5 = 5:30); the dashboard shows it as m:ss
        "pace_min_km": round(seconds / 60 / km, 2) if km > 0 else "",
        "avg_hr": round(a["averageHR"]) if a.get("averageHR") else "",
        "max_hr": round(a["maxHR"]) if a.get("maxHR") else "",
        "calories": round(a["calories"]) if a.get("calories") else "",
        "elevation_gain_m": round(a["elevationGain"]) if a.get("elevationGain") else "",
    }


def step_row(s: dict) -> dict:
    return {
        "date": s.get("calendarDate", ""),
        "steps": s.get("totalSteps") or 0,
        "distance_km": round((s.get("totalDistance") or 0) / 1000, 2),
        "step_goal": s.get("stepGoal") or "",
    }


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Garmin Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.plot.ly/plotly-3.4.0.min.js" crossorigin="anonymous"></script>
<style>
:root{--accent:#2196F3;--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;--panel:#fafafa}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;padding:20px 16px 40px;color:var(--ink);background:#fff}
main{max-width:1200px;margin:0 auto}
h1{font-size:24px;margin:0 0 2px}
.sub{color:var(--muted);font-size:14px;margin:0 0 16px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px}
.bar button{font:inherit;font-size:14px;padding:6px 12px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--ink);cursor:pointer}
.bar button:hover{border-color:var(--accent)}
.bar button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.range{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:14px;color:var(--muted)}
.range input{font:inherit;font-size:14px;padding:5px 8px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:22px}
.kpi span{font-size:12px;color:var(--muted)}
#chart{height:880px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
@media(max-width:640px){#chart{height:760px}}
</style></head>
<body><main>
<h1>Garmin Tracker</h1>
<p class="sub" id="sub"></p>
<div class="bar">
  <button id="prev" aria-label="Previous period" title="Previous period">&#9664;</button>
  <span id="presets" style="display:contents"></span>
  <button id="next" aria-label="Next period" title="Next period">&#9654;</button>
  <span class="range"><input type="date" id="from" aria-label="From"> to <input type="date" id="to" aria-label="To"></span>
</div>
<div class="kpis" id="kpis"></div>
<div id="chart"></div>
</main>
<script>
const STEPS = __STEPS__;       // [{date, steps, distance_km, step_goal}]
const ACTS = __ACTS__;         // [{date 'YYYY-MM-DD HH:MM', name, type, distance_km, pace_min_km, ...}]
const TODAY = __TODAY__;       // last day of the data, 'YYYY-MM-DD'
const UPDATED = __UPDATED__;

const DAY = 86400000;
const pad = n => String(n).padStart(2, '0');
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseD = s => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const nf = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 0});
const nf1 = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 1});
const fmtPace = p => { const s = Math.round(p * 60); return `${Math.floor(s / 60)}:${pad(s % 60)}`; };
const dateLabel = d => d.toLocaleDateString('en-GB', {weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'});
const trail = (vals, n) => vals.map((_, i) => {
  const w = vals.slice(Math.max(0, i - n + 1), i + 1);
  return w.reduce((a, b) => a + b, 0) / w.length;
});

const stepDays = STEPS.map(s => ({...s, d: parseD(s.date)})).sort((a, b) => a.date.localeCompare(b.date));
const acts = ACTS.map(a => ({...a, iso: a.date.replace(' ', 'T'), day: a.date.slice(0, 10)}))
  .sort((a, b) => a.date.localeCompare(b.date));
const runs = acts.filter(a => /run/.test(a.type) && a.pace_min_km !== '');
const END = parseD(TODAY);
const FIRST = parseD([stepDays[0]?.date, acts[0]?.day].filter(Boolean).sort()[0] ?? TODAY);

document.getElementById('sub').textContent =
  `${stepDays.length} days of steps, ${acts.length} activities, ${ymd(FIRST)} to ${TODAY}. Updated ${UPDATED}.`;

// ---- traces: three stacked panels that share one date axis ----
const complete = stepDays.filter(s => s.date !== TODAY);      // today is still in progress
const actText = a => `<b>${esc(a.name || a.type)}</b><br>${esc(a.date)}<br>Type: ${esc(a.type)}`
  + `<br>Distance: ${a.distance_km.toFixed(2)} km`
  + (a.pace_min_km !== '' ? `<br>Pace: ${fmtPace(a.pace_min_km)} /km` : '')
  + `<br>Time: ${a.duration_min} min` + (a.avg_hr !== '' ? `<br>Avg HR: ${a.avg_hr} bpm` : '');
const types = [...new Set(acts.map(a => a.type))];
const palette = ['#2196F3', '#9C27B0', '#009688', '#FFC107', '#795548', '#E91E63'];

const traces = [
  {type: 'bar', name: 'Steps', x: stepDays.map(s => s.date + 'T12:00'), y: stepDays.map(s => s.steps),
   marker: {color: '#26A69A', opacity: 0.6}, hoverinfo: 'text', xaxis: 'x', yaxis: 'y',
   hovertext: stepDays.map(s => `<b>${dateLabel(s.d)}</b><br>Steps: ${nf.format(s.steps)}`
     + (s.step_goal ? `<br>Goal: ${nf.format(s.step_goal)} (${Math.round(100 * s.steps / s.step_goal)}%)` : ''))},
  {type: 'scatter', mode: 'lines', name: 'Step goal', x: stepDays.map(s => s.date + 'T12:00'),
   y: stepDays.map(s => s.step_goal || null), line: {color: '#9ca3af', width: 1.5, dash: 'dot', shape: 'hvh'},
   hoverinfo: 'skip', xaxis: 'x', yaxis: 'y'},
  {type: 'scatter', mode: 'lines', name: 'Steps trend (7-day avg)', x: complete.map(s => s.date + 'T12:00'),
   y: trail(complete.map(s => s.steps), 7), line: {color: '#00796B', width: 2.5}, hoverinfo: 'skip',
   xaxis: 'x', yaxis: 'y'},
  {type: 'scatter', mode: 'markers', name: 'Pace', x: runs.map(a => a.iso), y: runs.map(a => a.pace_min_km),
   marker: {color: '#FC4C02', opacity: 0.6, size: 8}, hoverinfo: 'text', hovertext: runs.map(actText),
   xaxis: 'x2', yaxis: 'y2'},
  {type: 'scatter', mode: 'lines', name: 'Pace trend (5-run avg)', x: runs.map(a => a.iso),
   y: trail(runs.map(a => a.pace_min_km), 5), line: {color: '#FC4C02', width: 2.5}, hoverinfo: 'skip',
   xaxis: 'x2', yaxis: 'y2'},
  ...types.map((t, i) => {
    const of = acts.filter(a => a.type === t);
    return {type: 'scatter', mode: 'markers', name: types.length > 1 ? t : 'Distance', x: of.map(a => a.iso),
      y: of.map(a => a.distance_km), marker: {color: palette[i % palette.length], opacity: 0.6, size: 8},
      hoverinfo: 'text', hovertext: of.map(actText), xaxis: 'x3', yaxis: 'y3'};
  }),
  {type: 'scatter', mode: 'lines', name: 'Distance trend (5-activity avg)', x: acts.map(a => a.iso),
   y: trail(acts.map(a => a.distance_km), 5), line: {color: '#2196F3', width: 2.5}, hoverinfo: 'skip',
   xaxis: 'x3', yaxis: 'y3'},
];

const axis = {type: 'date', showgrid: true, gridcolor: '#eee', zeroline: false, showspikes: true,
  spikemode: 'across', spikethickness: 1, spikecolor: '#bbb', spikedash: 'solid'};
const panelTitle = (text, y) => ({text, x: 0.5, xanchor: 'center', xref: 'paper', y, yanchor: 'bottom',
  yref: 'paper', showarrow: false, font: {size: 16}});
const layout = {
  font: {family: 'system-ui, sans-serif'}, paper_bgcolor: '#fafafa', plot_bgcolor: '#fafafa',
  hovermode: 'closest', dragmode: 'zoom', margin: {t: 30, r: 24, b: 80, l: 64},
  legend: {orientation: 'h', y: -0.09},
  xaxis: {...axis, anchor: 'y', domain: [0, 1], matches: 'x3', showticklabels: false},
  xaxis2: {...axis, anchor: 'y2', domain: [0, 1], matches: 'x3', showticklabels: false},
  xaxis3: {...axis, anchor: 'y3', domain: [0, 1], title: {text: 'Date'}, tickangle: 0, tickformatstops: [
    {dtickrange: [null, 43200000], value: '%H:%M'},
    {dtickrange: [43200000, 'M1'], value: '%d %b'},
    {dtickrange: ['M1', 'M12'], value: '%b %Y'},
    {dtickrange: ['M12', null], value: '%Y'}]},
  yaxis: {domain: [0.72, 1], title: {text: 'steps'}, fixedrange: true, gridcolor: '#eee'},
  yaxis2: {domain: [0.38, 0.66], title: {text: 'min/km'}, fixedrange: true, gridcolor: '#eee'},
  yaxis3: {domain: [0, 0.32], title: {text: 'km'}, fixedrange: true, gridcolor: '#eee'},
  annotations: [panelTitle('Steps per day', 1.0), panelTitle('Pace (min/km), lower is faster', 0.66),
    panelTitle('Distance per activity (km)', 0.32)],
};

// ---- views: presets, previous/next, custom dates ----
const PRESETS = [
  {id: '7d', label: 'Last 7 days', range: () => [addDays(END, -6), END]},
  {id: '30d', label: 'Last 30 days', range: () => [addDays(END, -29), END]},
  {id: '90d', label: 'Last 90 days', range: () => [addDays(END, -89), END]},
  {id: 'thisMonth', label: 'This month', month: true, range: () => [new Date(END.getFullYear(), END.getMonth(), 1), END]},
  {id: 'prevMonth', label: 'Previous month', month: true,
   range: () => [new Date(END.getFullYear(), END.getMonth() - 1, 1), new Date(END.getFullYear(), END.getMonth(), 0)]},
  {id: 'ytd', label: 'This year', range: () => [new Date(END.getFullYear(), 0, 1), END]},
  {id: 'all', label: 'All data', range: () => [FIRST, END]},
];
const gd = document.getElementById('chart');
const fromEl = document.getElementById('from');
const toEl = document.getElementById('to');
let cur = {start: addDays(END, -89), end: END, preset: '90d', month: false};
let busy = false;

PRESETS.forEach(p => {
  const b = document.createElement('button');
  b.textContent = p.label;
  b.dataset.id = p.id;
  b.onclick = () => { const [s, e] = p.range(); applyView(s, e, {preset: p.id, month: !!p.month}); };
  document.getElementById('presets').appendChild(b);
});

function shift(dir) {
  let s, e;
  if (cur.month) {
    s = new Date(cur.start.getFullYear(), cur.start.getMonth() + dir, 1);
    e = new Date(cur.start.getFullYear(), cur.start.getMonth() + dir + 1, 0);
  } else {
    const span = Math.round((cur.end - cur.start) / DAY) + 1;
    s = addDays(cur.start, dir * span);
    e = addDays(cur.end, dir * span);
  }
  if (e > END) e = END;
  if (s > END || e < FIRST) return;
  applyView(s, e, {preset: 'custom', month: cur.month});
}
document.getElementById('prev').onclick = () => shift(-1);
document.getElementById('next').onclick = () => shift(1);
const fromTo = () => {
  if (!fromEl.value || !toEl.value) return;
  const s = parseD(fromEl.value), e = parseD(toEl.value);
  if (s <= e) applyView(s, e, {preset: 'custom', month: false});
};
fromEl.onchange = toEl.onchange = fromTo;

function renderKpis(st, aa, rr) {
  const done = st.filter(s => s.date !== TODAY);
  const total = st.reduce((a, s) => a + s.steps, 0);
  const avg = done.length ? done.reduce((a, s) => a + s.steps, 0) / done.length : null;
  const km = aa.reduce((a, x) => a + x.distance_km, 0);
  const runKm = rr.reduce((a, x) => a + x.distance_km, 0);
  const runMin = rr.reduce((a, x) => a + x.duration_min, 0);
  const hr = aa.filter(x => x.avg_hr !== '');
  const cards = [
    ['Avg steps / day', avg === null ? '–' : nf.format(avg), 'complete days only'],
    ['Total steps', nf.format(total), `${st.length} days`],
    ['Activities', nf.format(aa.length), `${nf1.format(aa.reduce((a, x) => a + x.duration_min, 0) / 60)} h`],
    ['Distance', `${nf1.format(km)} km`, 'all activities'],
    ['Avg run pace', runKm > 0 ? `${fmtPace(runMin / runKm)} /km` : '–', `${rr.length} runs`],
    ['Avg heart rate', hr.length ? `${nf.format(hr.reduce((a, x) => a + x.avg_hr, 0) / hr.length)} bpm` : '–', 'per activity'],
  ];
  document.getElementById('kpis').innerHTML = cards
    .map(([label, value, note]) => `<div class="kpi"><span>${label}</span><b>${value}</b><span>${note}</span></div>`)
    .join('');
}

// Fit the y axes to what is visible; set the x range too unless the user just zoomed there.
function applyView(start, end, {preset = null, month = false, setX = true} = {}) {
  cur = {start, end, preset, month};
  const a = ymd(start), b = ymd(end);
  const inRange = day => day >= a && day <= b;
  const st = stepDays.filter(s => inRange(s.date));
  const aa = acts.filter(x => inRange(x.day));
  const rr = runs.filter(x => inRange(x.day));

  const maxSteps = Math.max(1000, ...st.map(s => s.steps), ...st.map(s => s.step_goal || 0));
  const paces = rr.map(x => x.pace_min_km);
  const lo = paces.length ? Math.floor((Math.min(...paces) - 0.2) * 2) / 2 : 4;
  const hi = paces.length ? Math.ceil((Math.max(...paces) + 0.2) * 2) / 2 : 7;
  const tickvals = [];
  for (let v = lo; v <= hi + 1e-9; v += 0.5) tickvals.push(v);
  const maxKm = Math.max(5, ...aa.map(x => x.distance_km));

  const upd = {
    'yaxis.range': [0, maxSteps * 1.1],
    'yaxis2.range': [hi, lo], 'yaxis2.tickvals': tickvals, 'yaxis2.ticktext': tickvals.map(fmtPace),
    'yaxis3.range': [0, maxKm * 1.15],
  };
  if (setX) upd['xaxis3.range'] = [a, ymd(addDays(end, 1))];
  busy = true;
  Promise.resolve(Plotly.relayout(gd, upd)).then(() => { busy = false; }, () => { busy = false; });

  renderKpis(st, aa, rr);
  document.querySelectorAll('#presets button').forEach(el => el.classList.toggle('on', el.dataset.id === preset));
  fromEl.value = a;
  toEl.value = b;
  fromEl.min = toEl.min = ymd(FIRST);
  fromEl.max = toEl.max = TODAY;
  try { if (PRESETS.some(p => p.id === preset)) localStorage.setItem('garmin-view', preset); } catch (e) {}
}

const config = {responsive: true, displaylogo: false, doubleClick: false,
  modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d', 'resetScale2d']};
Plotly.newPlot(gd, traces, layout, config).then(() => {
  let saved = null;
  try { saved = localStorage.getItem('garmin-view'); } catch (e) {}
  const p = PRESETS.find(x => x.id === saved) || PRESETS.find(x => x.id === '90d');
  const [s, e] = p.range();
  applyView(s, e, {preset: p.id, month: !!p.month});

  // Dragging a zoom box on the date axis keeps that range and refits the panels to it.
  gd.on('plotly_relayout', ev => {
    if (busy) return;
    const k0 = Object.keys(ev).find(k => /^xaxis\d?\.range(\[0\])?$/.test(k));
    if (!k0) return;
    const r = k0.endsWith('[0]') ? [ev[k0], ev[k0.replace('[0]', '[1]')]] : ev[k0];
    let s2 = parseD(String(r[0])), e2 = parseD(String(r[1]));
    if (e2 < s2) e2 = s2;
    applyView(s2, e2, {preset: 'custom', month: false, setX: false});
  });
});
</script></body></html>
"""


def js(value) -> str:
    """JSON for embedding inside a <script> tag."""
    return json.dumps(value).replace("</", "<\\/")


def write_html(path: Path, steps: list[dict], acts: list[dict], end: date) -> None:
    page = (
        HTML.replace("__STEPS__", js(steps))
        .replace("__ACTS__", js(acts))
        .replace("__TODAY__", js(end.isoformat()))
        .replace("__UPDATED__", js(datetime.now().strftime("%d %b %Y %H:%M")))
    )
    path.write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=365, help="days back to fetch (default 365)")
    parser.add_argument("--no-open", action="store_true", help="do not open the dashboard")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days - 1)
    garmin = login()

    print(f"Fetching {start} to {end} ...")
    activities = garmin.get_activities_by_date(start.isoformat(), end.isoformat(), sortorder="asc")
    steps = garmin.get_daily_steps(start.isoformat(), end.isoformat())

    acts = [activity_row(a) for a in activities]
    daily = sorted((step_row(s) for s in steps), key=lambda r: r["date"])

    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "activities.csv", ACTIVITY_FIELDS, acts)
    write_csv(OUT / "steps.csv", STEP_FIELDS, daily)
    write_html(OUT / "garmin_stats.html", daily, acts, end)

    avg = round(sum(r["steps"] for r in daily) / len(daily)) if daily else 0
    print(f"{len(acts)} activities, {len(daily)} days of steps (avg {avg}/day)")
    print(f"Saved to {OUT}")
    if not args.no_open:
        webbrowser.open((OUT / "garmin_stats.html").as_uri())


if __name__ == "__main__":
    main()
