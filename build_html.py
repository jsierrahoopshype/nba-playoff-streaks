"""Generate index.html with playoff_streaks.csv embedded as a JS const DATA array."""
import csv
import json
import os

BASE = r"C:\nba-streaks"
CSV_PATH = os.path.join(BASE, "playoff_streaks.csv")
HTML_PATH = os.path.join(BASE, "index.html")

rows = []
with open(CSV_PATH, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for i, r in enumerate(reader, start=1):
        player = f"{r['firstName']} {r['lastName']}".strip()
        team = f"{r['teamCity']} {r['teamName']}".strip()
        rows.append([
            i,
            player,
            team,
            int(r["streakLength"]),
            r["streakStartDate"],
            r["streakEndDate"],
        ])

# Compact JSON: one record per line is readable but big; keep it dense.
data_js = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NBA Playoff Iron Man Streaks</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #1a1a2e;
    --panel: #21213d;
    --panel-2: #2a2a4a;
    --row: #20203a;
    --row-alt: #242442;
    --text: #e8e8f0;
    --muted: #9a9ab5;
    --accent: #e8612a;
    --accent-soft: rgba(232, 97, 42, 0.15);
    --border: #34345a;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }
  header { margin-bottom: 24px; }
  h1 {
    font-size: clamp(26px, 5vw, 40px);
    font-weight: 800;
    letter-spacing: -0.02em;
    margin: 0 0 6px;
  }
  h1 .accent { color: var(--accent); }
  .subtitle { color: var(--muted); font-size: 15px; font-weight: 500; margin: 0; }

  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    margin: 24px 0 12px;
  }
  #search {
    flex: 1 1 260px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 15px;
    font-family: inherit;
    padding: 12px 14px;
    outline: none;
    transition: border-color .15s, box-shadow .15s;
  }
  #search::placeholder { color: var(--muted); }
  #search:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  .count {
    color: var(--muted);
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
  }
  .count b { color: var(--accent); font-weight: 700; }

  .table-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
  }
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
  thead th {
    position: sticky;
    top: 0;
    background: var(--panel-2);
    color: var(--muted);
    text-align: left;
    font-weight: 600;
    font-size: 12.5px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 13px 16px;
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
    border-bottom: 1px solid var(--border);
  }
  thead th:hover { color: var(--text); }
  thead th .arrow { color: var(--accent); font-size: 11px; margin-left: 5px; }
  thead th.active { color: var(--text); }
  tbody td {
    padding: 11px 16px;
    border-bottom: 1px solid rgba(52,52,90,0.45);
    white-space: nowrap;
  }
  tbody tr:nth-child(even) { background: var(--row-alt); }
  tbody tr:hover { background: var(--accent-soft); }
  tbody tr:last-child td { border-bottom: none; }
  .col-rank { color: var(--muted); font-variant-numeric: tabular-nums; width: 64px; }
  .col-player { font-weight: 600; }
  .col-streak {
    font-weight: 700;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .col-date { color: var(--muted); font-variant-numeric: tabular-nums; }
  th.col-streak { text-align: right; }
  .empty { padding: 40px 16px; text-align: center; color: var(--muted); }

  footer {
    margin-top: 28px;
    color: var(--muted);
    font-size: 13px;
    text-align: center;
  }

  @media (max-width: 560px) {
    .wrap { padding: 22px 12px 48px; }
    tbody td, thead th { padding: 10px 11px; }
    table { font-size: 13.5px; }
  }
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>NBA Playoff <span class="accent">Iron Man</span> Streaks</h1>
      <p class="subtitle">Longest consecutive playoff games played, 1947&ndash;present</p>
    </header>

    <div class="controls">
      <input id="search" type="search" placeholder="Search by player name&hellip;" autocomplete="off" spellcheck="false">
      <div class="count" id="count"></div>
    </div>

    <div class="table-card">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th data-key="0" class="col-rank">Rank</th>
              <th data-key="1">Player</th>
              <th data-key="2">Team</th>
              <th data-key="3" class="col-streak">Streak (games)</th>
              <th data-key="4">Start Date</th>
              <th data-key="5">End Date</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>

    <footer>Data: Kaggle / eoinamoore &middot; Updated through 2025&ndash;26 season</footer>
  </div>

<script>
const DATA = __DATA__;

// Column index -> type for sorting. 0=rank(num),3=streak(num),4/5=date,others text.
const NUMERIC = new Set([0, 3]);
const DATES = new Set([4, 5]);

let sortKey = 0;          // default sort by rank
let sortAsc = true;
const tbody = document.getElementById('tbody');
const search = document.getElementById('search');
const countEl = document.getElementById('count');
const headers = Array.from(document.querySelectorAll('thead th'));

function compare(a, b, key) {
  let av = a[key], bv = b[key];
  if (NUMERIC.has(key)) { av = +av; bv = +bv; }
  else if (DATES.has(key)) { av = av || ''; bv = bv || ''; } // ISO dates sort lexically
  else { av = String(av).toLowerCase(); bv = String(bv).toLowerCase(); }
  if (av < bv) return -1;
  if (av > bv) return 1;
  return 0;
}

function render() {
  const q = search.value.trim().toLowerCase();
  let rows = q ? DATA.filter(r => r[1].toLowerCase().includes(q)) : DATA.slice();

  rows.sort((a, b) => {
    const c = compare(a, b, sortKey);
    return sortAsc ? c : -c;
  });

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td class="empty" colspan="6">No players match &ldquo;' +
      escapeHtml(search.value) + '&rdquo;</td></tr>';
  } else {
    const frag = [];
    for (const r of rows) {
      frag.push(
        '<tr>' +
        '<td class="col-rank">' + r[0] + '</td>' +
        '<td class="col-player">' + escapeHtml(r[1]) + '</td>' +
        '<td>' + escapeHtml(r[2]) + '</td>' +
        '<td class="col-streak">' + r[3] + '</td>' +
        '<td class="col-date">' + r[4] + '</td>' +
        '<td class="col-date">' + r[5] + '</td>' +
        '</tr>'
      );
    }
    tbody.innerHTML = frag.join('');
  }

  countEl.innerHTML = '<b>' + rows.length + '</b> of ' + DATA.length + ' players';
  updateHeaderArrows();
}

function updateHeaderArrows() {
  headers.forEach(h => {
    const key = +h.dataset.key;
    h.classList.toggle('active', key === sortKey);
    const existing = h.querySelector('.arrow');
    if (existing) existing.remove();
    if (key === sortKey) {
      const span = document.createElement('span');
      span.className = 'arrow';
      span.textContent = sortAsc ? '\\u25B2' : '\\u25BC';
      h.appendChild(span);
    }
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

headers.forEach(h => {
  h.addEventListener('click', () => {
    const key = +h.dataset.key;
    if (key === sortKey) {
      sortAsc = !sortAsc;
    } else {
      sortKey = key;
      // Sensible default direction: numbers/dates high-to-low, text A-Z.
      sortAsc = !(NUMERIC.has(key) || DATES.has(key));
    }
    render();
  });
});

search.addEventListener('input', render);
render();
</script>
</body>
</html>
"""

html = html.replace("__DATA__", data_js)
with open(HTML_PATH, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Wrote {HTML_PATH} with {len(rows)} rows embedded.")
