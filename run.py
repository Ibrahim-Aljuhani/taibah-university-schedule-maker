"""
Taibah University Schedule Finder
Parses the university subjects table HTML and finds conflict-free
timetable combinations with a target number of off days per week.

Usage:
    python run.py [path/to/SubjectsTable.html]

If no path is provided, the script looks for any valid .html file
in the same directory as this file.
"""

# ── Standard imports ──────────────────────────────────────────────────────────
import logging
import os
import re
import sys
import threading
import webbrowser
from itertools import product
from pathlib import Path

from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request

# ── Constants ─────────────────────────────────────────────────────────────────
DAY_COLUMN_MAP = {2: "Thu", 3: "Wed", 4: "Tue", 5: "Mon", 6: "Sun"}
ALL_DAYS       = {"Sun", "Mon", "Tue", "Wed", "Thu"}
TIME_PATTERN   = re.compile(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})")

# ── HTML parser ───────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()

def parse_time_slot(cell_text: str):
    match = TIME_PATTERN.search(cell_text)
    if match:
        return {"start": match.group(1), "end": match.group(2)}
    return None

def parse_days_and_time(cells):
    time_to_days = {}
    for col_idx, day_name in DAY_COLUMN_MAP.items():
        if col_idx >= len(cells):
            continue
        slot = parse_time_slot(clean(cells[col_idx].get_text()))
        if slot:
            key = f"{slot['start']}-{slot['end']}"
            time_to_days.setdefault(key, []).append(day_name)
    all_days, schedules = [], []
    for time_key, days in time_to_days.items():
        start, end = time_key.split("-", 1)
        all_days.extend(days)
        schedules.append({"days": days, "start_time": start, "end_time": end})
    return all_days, schedules

def parse_row(cells):
    if len(cells) < 12:
        return None
    dept_code    = clean(cells[11].get_text())
    subject_num  = clean(cells[10].get_text())
    subject_name = clean(cells[9].get_text())
    if not dept_code or not subject_num or not dept_code.isascii():
        return None
    subject_code = f"{dept_code}{subject_num}"
    section_id   = clean(cells[8].get_text())
    instructor   = clean(cells[7].get_text())
    try:
        capacity = int(clean(cells[1].get_text()))
    except ValueError:
        capacity = None
    try:
        enrolled = int(clean(cells[0].get_text()))
    except ValueError:
        enrolled = None
    branch = clean(cells[12].get_text()) if len(cells) > 12 else ""
    days, schedules = parse_days_and_time(cells)
    start_time = schedules[0]["start_time"] if schedules else None
    end_time   = schedules[0]["end_time"]   if schedules else None
    return {
        "subject_code":   subject_code,
        "subject_name":   subject_name,
        "dept_code":      dept_code,
        "subject_number": subject_num,
        "section": {
            "section_id": section_id,
            "instructor": instructor,
            "days":       days,
            "start_time": start_time,
            "end_time":   end_time,
            "schedules":  schedules,
            "capacity":   capacity,
            "enrolled":   enrolled,
            "branch":     branch,
        },
    }

def parse_schedule_html(html_path: Path) -> dict:
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f, "html.parser")
    all_tables = soup.find_all("table")
    if not all_tables:
        return {}
    schedule_table = max(all_tables, key=lambda t: len(t.find_all("tr")))
    subjects = {}
    for row in schedule_table.find_all("tr"):
        cells    = row.find_all(["td", "th"])
        row_data = parse_row(cells)
        if row_data is None:
            continue
        code = row_data["subject_code"]
        if code not in subjects:
            subjects[code] = {
                "subject_code":   code,
                "subject_name":   row_data["subject_name"],
                "dept_code":      row_data["dept_code"],
                "subject_number": row_data["subject_number"],
                "sections":       [],
            }
        if row_data["subject_name"]:
            subjects[code]["subject_name"] = row_data["subject_name"]
        subjects[code]["sections"].append(row_data["section"])
    return subjects

# ── Schedule finder ───────────────────────────────────────────────────────────

def count_off_days(selected_sections: list) -> int:
    busy = set()
    for sec in selected_sections:
        busy.update(sec.get("days", []))
    return len(ALL_DAYS - busy)

def has_conflict(sections) -> bool:
    slots = []
    for sec in sections:
        for sched in sec.get("schedules", []):
            sh, sm = map(int, sched["start_time"].split(":"))
            eh, em = map(int, sched["end_time"].split(":"))
            start  = sh * 60 + sm
            end    = eh * 60 + em
            for day in sched["days"]:
                slots.append((day, start, end))
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            d1, s1, e1 = slots[i]
            d2, s2, e2 = slots[j]
            if d1 == d2 and s1 < e2 and s2 < e1:
                return True
    return False

def find_schedules(schedule_data: dict, subject_codes: list,
                   desired_off_days: int, skip: int = 0, limit: int = 20):
    pools = []
    for code in subject_codes:
        code = code.strip().upper()
        subj = schedule_data.get(code)
        if not subj:
            return None, None, f"Subject '{code}' not found."
        valid = [s for s in subj["sections"] if s.get("days")]
        if not valid:
            return None, None, f"Subject '{code}' has no sections with schedule data."
        pools.append((code, valid))

    codes         = [p[0] for p in pools]
    section_lists = [p[1] for p in pools]

    results  = []
    seen     = 0
    has_more = False

    for combo in product(*section_lists):
        if has_conflict(combo):
            continue
        if count_off_days(combo) != desired_off_days:
            continue
        if seen < skip:
            seen += 1
            continue
        if len(results) < limit:
            results.append(
                [{"subject_code": c, "section": s} for c, s in zip(codes, combo)]
            )
            seen += 1
        else:
            has_more = True
            break

    return results, has_more, None

# ── UI ─────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Schedule Finder</title>
<link href="https://fonts.googleapis.com/css2?family=Chivo:wght@400;500;700;800;900&display=swap" rel="stylesheet"/>
<style>
  /* ── Design tokens (Slate Editorial) ─────────────────────────── */
  :root {
    --surface:           #f7f9fb;
    --surface-low:       #f2f4f6;
    --surface-container: #eceef0;
    --surface-high:      #e6e8ea;
    --surface-highest:   #e0e3e5;
    --on-surface:        #191c1e;
    --on-surface-var:    #44474c;
    --outline:           #75777d;
    --outline-var:       #c5c6cd;
    --primary:           #334155;
    --primary-dark:      #1d2b3e;
    --on-primary:        #ffffff;
    --secondary:         #565e74;
    --error:             #ba1a1a;
    --error-surface:     #ffdad6;

    --font: 'Chivo', system-ui, sans-serif;
  }

  /* ── Reset ────────────────────────────────────────────────────── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--font);
    background: var(--surface);
    color: var(--on-surface);
    min-height: 100dvh;
  }

  /* ── Layout ───────────────────────────────────────────────────── */
  .page-wrap {
    max-width: 800px;
    margin: 0 auto;
    padding: 0 20px;
  }

  /* ── Header ───────────────────────────────────────────────────── */
  header {
    border-bottom: 4px solid var(--on-surface);
    background: var(--surface);
    position: sticky;
    top: 0;
    z-index: 50;
  }
  .header-inner {
    max-width: 800px;
    margin: 0 auto;
    padding: 0 20px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .site-title {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .header-tag {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--on-surface-var);
  }

  /* ── Page header ──────────────────────────────────────────────── */
  .page-header {
    padding: 48px 0 40px;
    border-bottom: 4px solid var(--on-surface);
    margin-bottom: 40px;
  }
  .eyebrow {
    display: inline-block;
    background: var(--primary);
    color: var(--on-primary);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 4px 10px;
    margin-bottom: 16px;
  }
  .page-title {
    font-size: clamp(32px, 5vw, 48px);
    font-weight: 900;
    letter-spacing: -0.03em;
    line-height: 1.1;
    color: var(--on-surface);
    margin-bottom: 12px;
  }
  .page-subtitle {
    font-size: 16px;
    font-weight: 400;
    color: var(--on-surface-var);
    line-height: 1.5;
  }

  /* ── Form ─────────────────────────────────────────────────────── */
  .form-card {
    border: 1px solid var(--outline-var);
    background: var(--surface);
    padding: 32px;
    margin-bottom: 40px;
  }
  .form-row {
    display: grid;
    grid-template-columns: 1fr 180px;
    gap: 24px;
    align-items: end;
  }
  .field { display: flex; flex-direction: column; gap: 8px; }
  .field label {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--on-surface-var);
  }
  .field input {
    font-family: var(--font);
    font-size: 16px;
    font-weight: 400;
    color: var(--on-surface);
    background: var(--surface);
    border: 1px solid var(--outline-var);
    padding: 10px 12px;
    border-radius: 0;
    outline: none;
    transition: border-color 0.15s;
    width: 100%;
  }
  .field input:focus { border-color: var(--primary); }
  .field .hint {
    font-size: 12px;
    color: var(--outline);
    font-weight: 400;
  }
  .btn-primary {
    font-family: var(--font);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    background: var(--primary);
    color: var(--on-primary);
    border: none;
    padding: 12px 24px;
    cursor: pointer;
    transition: background 0.15s, opacity 0.15s;
    width: 100%;
    border-radius: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .btn-primary:hover   { background: var(--primary-dark); }
  .btn-primary:active  { opacity: 0.85; }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Results header ───────────────────────────────────────────── */
  .results-meta {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 24px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--outline-var);
  }
  .results-meta .count {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--on-surface-var);
  }
  .results-meta .count strong { color: var(--primary); }

  /* ── Schedule card ────────────────────────────────────────────── */
  .schedule-card {
    border: 1px solid var(--outline-var);
    margin-bottom: 24px;
    background: var(--surface);
  }
  .card-header {
    background: var(--primary);
    color: var(--on-primary);
    padding: 10px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card-num {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .card-offdays {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    opacity: 0.75;
  }
  .card-body { padding: 20px; }

  /* ── Week grid ────────────────────────────────────────────────── */
  .week-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 1px;
    background: var(--outline-var);
    border: 1px solid var(--outline-var);
    margin-bottom: 20px;
  }
  .day-col {
    background: var(--surface);
    min-height: 80px;
  }
  .day-header {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 8px 10px;
    border-bottom: 2px solid var(--outline-var);
    color: var(--on-surface-var);
    background: var(--surface-low);
  }
  .day-header.active {
    background: var(--primary);
    color: var(--on-primary);
    border-bottom-color: var(--primary-dark);
  }
  .day-events { padding: 8px; display: flex; flex-direction: column; gap: 6px; }
  .day-off {
    font-size: 11px;
    color: var(--outline);
    padding: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 700;
  }
  .event-chip {
    font-size: 11px;
    font-weight: 700;
    line-height: 1.4;
    padding: 5px 7px;
    border-left: 3px solid;
    background: var(--surface-low);
    letter-spacing: 0.02em;
  }

  /* ── Section table ────────────────────────────────────────────── */
  .section-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    border: 1px solid var(--outline-var);
  }
  .section-table thead tr {
    background: var(--on-surface);
    color: var(--surface);
  }
  .section-table th {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    padding: 8px 12px;
    text-align: left;
  }
  .section-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--outline-var);
    color: var(--on-surface);
    vertical-align: top;
  }
  .section-table tr:last-child td { border-bottom: none; }
  .section-table tr:hover td { background: var(--surface-low); }
  .subject-code { font-weight: 700; color: var(--primary); }
  .day-badge {
    display: inline-block;
    background: var(--surface-high);
    color: var(--on-surface);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 2px 6px;
    margin-right: 3px;
    margin-bottom: 2px;
  }

  /* ── Summary strip ────────────────────────────────────────────── */
  .summary-strip {
    display: flex;
    gap: 1px;
    background: var(--outline-var);
    border: 1px solid var(--outline-var);
    margin-bottom: 16px;
  }
  .summary-item {
    flex: 1;
    background: var(--surface-low);
    padding: 12px 16px;
  }
  .summary-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--on-surface-var);
    margin-bottom: 4px;
  }
  .summary-value {
    font-size: 18px;
    font-weight: 800;
    color: var(--primary);
    letter-spacing: -0.01em;
  }

  /* ── Load more ────────────────────────────────────────────────── */
  .btn-outline {
    font-family: var(--font);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    background: transparent;
    color: var(--primary);
    border: 2px solid var(--primary);
    padding: 12px 24px;
    cursor: pointer;
    width: 100%;
    margin: 8px 0 48px;
    border-radius: 0;
    transition: background 0.15s, color 0.15s;
  }
  .btn-outline:hover    { background: var(--primary); color: var(--on-primary); }
  .btn-outline:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── State messages ───────────────────────────────────────────── */
  .msg-box {
    padding: 32px;
    border: 1px solid var(--outline-var);
    margin-bottom: 24px;
    text-align: center;
  }
  .msg-box.error   { border-color: var(--error); background: var(--error-surface); }
  .msg-title {
    font-size: 18px;
    font-weight: 700;
    color: var(--on-surface);
    margin-bottom: 8px;
  }
  .msg-body { font-size: 14px; color: var(--on-surface-var); }
  .msg-box.error .msg-title { color: var(--error); }

  /* ── Spinner ──────────────────────────────────────────────────── */
  .spinner {
    width: 18px; height: 18px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.65s linear infinite;
    display: none;
  }
  .spinner-inline {
    width: 14px; height: 14px;
    border: 2px solid var(--primary);
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.65s linear infinite;
    display: inline-block;
    vertical-align: middle;
    margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Divider ──────────────────────────────────────────────────── */
  hr.thick { height: 4px; background: var(--on-surface); border: none; margin: 40px 0; }
  hr.thin  { height: 1px; background: var(--outline-var); border: none; margin: 24px 0; }

  /* ── Footer ───────────────────────────────────────────────────── */
  footer {
    background: var(--on-surface);
    color: var(--surface);
    padding: 32px 20px;
    margin-top: 48px;
  }
  .footer-inner {
    max-width: 800px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .footer-name {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .footer-note {
    font-size: 12px;
    color: rgba(247,249,251,0.55);
    letter-spacing: 0.04em;
  }

  /* ── Responsive ───────────────────────────────────────────────── */
  @media (max-width: 560px) {
    .form-row { grid-template-columns: 1fr; }
    .week-grid { grid-template-columns: repeat(5, 1fr); }
    .event-chip { font-size: 10px; padding: 4px 5px; }
    .section-table { font-size: 12px; }
  }
</style>
</head>
<body>

<header>
  <div class="header-inner">
    <span class="site-title">Taibah University</span>
    <span class="header-tag">Schedule Finder</span>
  </div>
</header>

<div class="page-wrap">

  <div class="page-header">
    <span class="eyebrow">Timetable Tool</span>
    <h1 class="page-title">Schedule Finder</h1>
    <p class="page-subtitle">
      Enter your subject codes and target number of off days.<br>
      The tool will generate all conflict-free combinations.
    </p>
  </div>

  <div class="form-card">
    <div class="form-row">
      <div class="field">
        <label for="subjects">Subject Codes</label>
        <input type="text" id="subjects" placeholder="CS112, MATH320, EE222" autocomplete="off"/>
        <span class="hint">Comma-separated, case-insensitive</span>
      </div>
      <div class="field">
        <label for="offdays">Off Days</label>
        <input type="number" id="offdays" min="0" max="5" value="2"/>
        <span class="hint">0 to 5 days per week</span>
      </div>
    </div>
    <div style="margin-top:24px;">
      <button class="btn-primary" id="searchBtn" onclick="search()">
        <div class="spinner" id="searchSpinner"></div>
        <span id="searchLabel">Find Schedules</span>
      </button>
    </div>
  </div>

  <div id="resultsHeader" style="display:none" class="results-meta">
    <span class="count">Showing <strong id="shownCount">0</strong> result(s)</span>
  </div>

  <div id="results"></div>

  <button class="btn-outline" id="loadMoreBtn" style="display:none" onclick="loadMore()">
    Load 20 More Schedules
  </button>

</div>

<footer>
  <div class="footer-inner">
    <span class="footer-name">Taibah University</span>
    <span class="footer-note">Schedule data sourced from university timetable</span>
  </div>
</footer>

<script>
/* ── Palette for subject chips ── */
const CHIP_COLORS = [
  { bg: "#dae2fd", border: "#334155", text: "#1d2b3e" },
  { bg: "#e0e3e5", border: "#565e74", text: "#191c1e" },
  { bg: "#d5e3fd", border: "#3a485c", text: "#0d1c2f" },
  { bg: "#f2f4f6", border: "#44474c", text: "#191c1e" },
  { bg: "#eceef0", border: "#334155", text: "#1d2b3e" },
  { bg: "#e6e8ea", border: "#565e74", text: "#191c1e" },
];

const DAY_ORDER = ["Sun", "Mon", "Tue", "Wed", "Thu"];

let state = { skip: 0, codes: "", off: 2, total: 0 };

/* ── Search (first page) ── */
async function search() {
  const codes = document.getElementById("subjects").value.trim();
  const off   = parseInt(document.getElementById("offdays").value, 10);
  if (!codes) { alert("Enter at least one subject code."); return; }

  state = { skip: 0, codes, off, total: 0 };

  setSearching(true);
  document.getElementById("results").innerHTML = "";
  document.getElementById("loadMoreBtn").style.display = "none";
  document.getElementById("resultsHeader").style.display = "none";

  await fetchPage(true);
  setSearching(false);
}

/* ── Load more ── */
async function loadMore() {
  const btn = document.getElementById("loadMoreBtn");
  btn.disabled = true;
  btn.textContent = "Loading...";
  await fetchPage(false);
  btn.disabled = false;
  btn.textContent = "Load 20 More Schedules";
}

/* ── Core fetch ── */
async function fetchPage(isFirst) {
  try {
    const res  = await fetch("/find", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ subjects: state.codes, off_days: state.off, skip: state.skip, limit: 20 }),
    });
    const data = await res.json();

    if (data.error) {
      appendBox("error", "Error", data.error);
      return;
    }

    if (isFirst && (!data.schedules || data.schedules.length === 0)) {
      appendBox("", "No schedules found",
        `No conflict-free combination exists with ${state.off} off day${state.off !== 1 ? "s" : ""}. Try a different number.`);
      return;
    }

    data.schedules.forEach((schedule, i) => {
      const num = state.skip + i + 1;
      document.getElementById("results").insertAdjacentHTML("beforeend", buildCard(schedule, num));
    });

    state.skip  += data.schedules.length;
    state.total  = state.skip;

    document.getElementById("resultsHeader").style.display = "flex";
    document.getElementById("shownCount").textContent = state.total;
    document.getElementById("loadMoreBtn").style.display = data.has_more ? "block" : "none";

  } catch (err) {
    appendBox("error", "Network error", err.message);
  }
}

/* ── Build a single schedule card ── */
function buildCard(schedule, num) {
  const busy    = new Set(schedule.flatMap(s => s.section.days));
  const offDays = DAY_ORDER.filter(d => !busy.has(d));

  /* summary strip */
  const summary = `
    <div class="summary-strip">
      <div class="summary-item">
        <div class="summary-label">Off Days</div>
        <div class="summary-value">${offDays.join(", ") || "None"}</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">Busy Days</div>
        <div class="summary-value">${[...busy].join(", ") || "None"}</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">Subjects</div>
        <div class="summary-value">${schedule.length}</div>
      </div>
    </div>`;

  /* week grid */
  let grid = '<div class="week-grid">';
  DAY_ORDER.forEach(day => {
    const isOff = !busy.has(day);
    grid += `<div class="day-col">
      <div class="day-header ${isOff ? "" : "active"}">${day}</div>`;
    if (isOff) {
      grid += `<div class="day-off">Off</div>`;
    } else {
      grid += `<div class="day-events">`;
      schedule.forEach((item, idx) => {
        const c = CHIP_COLORS[idx % CHIP_COLORS.length];
        item.section.schedules.forEach(sched => {
          if (sched.days.includes(day)) {
            grid += `<div class="event-chip" style="background:${c.bg};border-color:${c.border};color:${c.text}">
              ${item.subject_code}<br>${sched.start_time}–${sched.end_time}
            </div>`;
          }
        });
      });
      grid += `</div>`;
    }
    grid += `</div>`;
  });
  grid += `</div>`;

  /* section table */
  let table = `
    <table class="section-table">
      <thead><tr>
        <th>Code</th>
        <th>Section</th>
        <th>Instructor</th>
        <th>Schedule</th>
        <th>Capacity</th>
      </tr></thead>
      <tbody>`;
  schedule.forEach((item, idx) => {
    const sec  = item.section;
    const c    = CHIP_COLORS[idx % CHIP_COLORS.length];
    const days = sec.days.map(d => `<span class="day-badge">${d}</span>`).join("");
    const time = sec.schedules.map(s =>
      `${s.days.join(",")} ${s.start_time}–${s.end_time}`
    ).join("<br>");
    table += `<tr>
      <td><span class="subject-code" style="color:${c.border}">${item.subject_code}</span></td>
      <td>${sec.section_id}</td>
      <td>${sec.instructor || "—"}</td>
      <td>${days}<br><span style="font-size:11px;color:var(--on-surface-var)">${time}</span></td>
      <td style="font-size:12px">${sec.enrolled ?? "?"}/${sec.capacity ?? "?"}</td>
    </tr>`;
  });
  table += `</tbody></table>`;

  return `
    <div class="schedule-card">
      <div class="card-header">
        <span class="card-num">Schedule ${num}</span>
        <span class="card-offdays">${offDays.length} Off Day${offDays.length !== 1 ? "s" : ""}</span>
      </div>
      <div class="card-body">
        ${summary}${grid}${table}
      </div>
    </div>`;
}

/* ── Helpers ── */
function appendBox(type, title, body) {
  document.getElementById("results").insertAdjacentHTML("beforeend", `
    <div class="msg-box ${type}">
      <div class="msg-title">${title}</div>
      <div class="msg-body">${body}</div>
    </div>`);
}

function setSearching(on) {
  const btn     = document.getElementById("searchBtn");
  const spinner = document.getElementById("searchSpinner");
  const label   = document.getElementById("searchLabel");
  btn.disabled        = on;
  spinner.style.display = on ? "block" : "none";
  label.textContent   = on ? "Searching..." : "Find Schedules";
}

document.addEventListener("keydown", e => { if (e.key === "Enter") search(); });
</script>
</body>
</html>"""

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/find", methods=["POST"])
def find():
    body     = request.get_json(force=True)
    raw      = body.get("subjects", "")
    off_days = int(body.get("off_days", 2))
    skip     = max(0, int(body.get("skip",  0)))
    limit    = max(1, int(body.get("limit", 20)))
    codes    = [c.strip().upper() for c in raw.split(",") if c.strip()]
    if not codes:
        return jsonify({"error": "No subject codes provided."})
    results, has_more, err = find_schedules(schedule, codes, off_days, skip=skip, limit=limit)
    if err:
        return jsonify({"error": err})
    return jsonify({"schedules": results or [], "has_more": has_more or False})

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Silencing the Flask dev server warning
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    script_dir = Path(__file__).parent.resolve()

    if len(sys.argv) > 1:
        html_paths = [Path(sys.argv[1]).resolve()]
    else:
        html_paths = list(script_dir.glob("*.html"))

    if not html_paths:
        print("ERROR: No HTML files found in the current directory.")
        print("Please place a valid schedule HTML file in the same folder or provide its path.")
        sys.exit(1)

    schedule = {}
    valid_html_path = None

    for html_path in html_paths:
        print(f"Trying to parse {html_path.name} ...")
        try:
            temp_schedule = parse_schedule_html(html_path)
            if not temp_schedule:
                print(f"  -> Error: {html_path.name} does not contain valid schedule data.")
                continue
            schedule = temp_schedule
            valid_html_path = html_path
            print(f"  -> Success! Found {len(schedule)} subjects in {html_path.name}.")
            break
        except Exception as e:
            print(f"  -> Error: Failed to parse {html_path.name}. Not a valid schedule file.")
    
    if not schedule:
        print("\nERROR: Could not find any valid schedule HTML files to parse in this directory.")
        sys.exit(1)

    url = "http://127.0.0.1:5050"
    print(f"\nStarting server at {url}")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5050, debug=False)