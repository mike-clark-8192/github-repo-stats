#!/usr/bin/env python3
"""Generate aggregate dashboard for GitHub repository statistics.

Creates an information-dense dashboard with:
- Aggregate stats header
- Per-repo cards with sparkline charts
- "Crickets" section for inactive repos
- "All Time Stars" modal chart (weekly bins, zoomable, per-week attribution)
"""

import sys
import json
import os
import csv
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional


@dataclass
class RepoStats:
    """Statistics for a single repository."""
    name: str
    full_name: str
    stars: int = 0
    forks: int = 0
    views_total: int = 0
    views_unique: int = 0
    clones_total: int = 0
    clones_unique: int = 0
    views_series: list = None  # Last 14 days
    clones_series: list = None  # Last 14 days
    stars_series: list = None  # Last 90 days cumulative
    forks_series: list = None  # Last 90 days cumulative
    stars_growth: int = 0  # Change in last 90 days
    forks_growth: int = 0  # Change in last 90 days
    stars_history: list = None  # Full history: [(datetime, cumulative), ...]
    has_activity: bool = False

    def __post_init__(self):
        if self.views_series is None:
            self.views_series = []
        if self.clones_series is None:
            self.clones_series = []
        if self.stars_series is None:
            self.stars_series = []
        if self.forks_series is None:
            self.forks_series = []
        if self.stars_history is None:
            self.stars_history = []


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats from CSV files."""
    if not date_str:
        return None
    # Try different formats
    for fmt in [
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S+00:00",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(date_str.replace("+00:00", ""), fmt.replace("%z", ""))
        except ValueError:
            continue
    return None


def read_views_clones(repo_path: str, days: int = 14) -> tuple:
    """Read views/clones aggregate CSV and return recent data."""
    csv_path = os.path.join(repo_path, "ghrs-data", "views_clones_aggregate.csv")
    views_series = []
    clones_series = []
    views_total = 0
    views_unique = 0
    clones_total = 0
    clones_unique = 0

    if not os.path.exists(csv_path):
        return views_series, clones_series, views_total, views_unique, clones_total, clones_unique

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = parse_date(row.get("time_iso8601", ""))
                if date and date.replace(tzinfo=None) >= cutoff:
                    v_unique = int(row.get("views_unique", 0) or 0)
                    c_unique = int(row.get("clones_unique", 0) or 0)
                    views_series.append(v_unique)
                    clones_series.append(c_unique)
                    views_total += int(row.get("views_total", 0) or 0)
                    views_unique += v_unique
                    clones_total += int(row.get("clones_total", 0) or 0)
                    clones_unique += c_unique
    except Exception:
        pass

    return views_series, clones_series, views_total, views_unique, clones_total, clones_unique


def read_cumulative_series(csv_path: str, value_col: str, days: int = 90) -> tuple:
    """Read cumulative series (stars/forks) and return recent data + growth."""
    series = []
    current_value = 0
    growth = 0

    if not os.path.exists(csv_path):
        return series, current_value, growth

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    all_values = []

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = parse_date(row.get("time_iso8601", ""))
                value = int(row.get(value_col, 0) or 0)
                if date:
                    all_values.append((date, value))

        if all_values:
            all_values.sort(key=lambda x: x[0])
            current_value = all_values[-1][1]

            # Get values within cutoff for series
            for date, value in all_values:
                if date.replace(tzinfo=None) >= cutoff:
                    series.append(value)

            # Calculate growth (current - value at cutoff)
            values_before = [v for d, v in all_values if d.replace(tzinfo=None) < cutoff]
            if values_before:
                growth = current_value - values_before[-1]
            elif series:
                growth = current_value - series[0] if series[0] != current_value else current_value

    except Exception:
        pass

    return series, current_value, growth


def resolve_stars_source(repo_path: str) -> tuple:
    """Pick the star history CSV for a repo, returning (csv_path, value_column).

    Prefer stargazers.csv (daily, derived from starred_at timestamps) and fall
    back to the coarser stargazer-snapshots.csv. Both readers go through here so
    the header tile and the All Time Stars chart never disagree.
    """
    stars_path = os.path.join(repo_path, "ghrs-data", "stargazers.csv")
    if os.path.exists(stars_path):
        return stars_path, "stars_cumulative"
    return (
        os.path.join(repo_path, "ghrs-data", "stargazer-snapshots.csv"),
        "stargazers_cumulative_snapshot",
    )


def read_star_history(csv_path: str, value_col: str) -> list:
    """Read the complete star history as a sorted list of (datetime, cumulative)."""
    history = []

    if not os.path.exists(csv_path):
        return history

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = parse_date(row.get("time_iso8601", ""))
                if date is None:
                    continue
                history.append((date.replace(tzinfo=None), int(row.get(value_col, 0) or 0)))
    except Exception:
        return []

    history.sort(key=lambda x: x[0])
    return history


def week_start(dt: datetime) -> datetime:
    """Return midnight on the Monday of the week containing dt."""
    monday = dt - timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def build_weekly_star_timeline(stats: list) -> dict:
    """Bin all repos' star histories into weeks and attribute each week's change.

    Every repo's cumulative count is forward-filled to the end of each week (0
    before its first observation), so the totals are a true cross-repo sum and
    the final point matches the "total stars" header tile.

    Returns {"weeks": [ISO date, ...], "totals": [int, ...], "changes": [dict|None, ...]}
    where changes[i] maps repo name -> signed delta for the weeks that moved.
    """
    histories = [(r.name, r.stars_history) for r in stats if r.stars_history]
    if not histories:
        return {"weeks": [], "totals": [], "changes": []}

    first = week_start(min(h[0][0] for _, h in histories))
    last = week_start(max(h[-1][0] for _, h in histories))

    weeks = []
    cursor = first
    while cursor <= last:
        weeks.append(cursor)
        cursor += timedelta(days=7)

    totals = [0] * len(weeks)
    changes = [{} for _ in weeks]

    for name, history in histories:
        idx = 0
        prev = 0
        for wi, week in enumerate(weeks):
            boundary = week + timedelta(days=7)
            value = prev
            while idx < len(history) and history[idx][0] < boundary:
                value = history[idx][1]
                idx += 1
            totals[wi] += value
            if value != prev:
                changes[wi][name] = value - prev
            prev = value

    return {
        "weeks": [w.strftime("%Y-%m-%d") for w in weeks],
        "totals": totals,
        "changes": [c if c else None for c in changes],
    }


def collect_repo_stats(workspace_root: str, repos: list) -> list:
    """Collect statistics for all repositories."""
    stats = []

    for repo_spec in repos:
        repo_name = repo_spec.split("/")[1]
        repo_path = os.path.join(workspace_root, repo_spec)

        repo_stats = RepoStats(name=repo_name, full_name=repo_spec)

        # Read views/clones
        (
            repo_stats.views_series,
            repo_stats.clones_series,
            repo_stats.views_total,
            repo_stats.views_unique,
            repo_stats.clones_total,
            repo_stats.clones_unique,
        ) = read_views_clones(repo_path)

        # Read stars (prefer stargazers.csv, fall back to stargazer-snapshots.csv)
        stars_path, stars_col = resolve_stars_source(repo_path)
        repo_stats.stars_series, repo_stats.stars, repo_stats.stars_growth = read_cumulative_series(
            stars_path, stars_col
        )
        repo_stats.stars_history = read_star_history(stars_path, stars_col)

        # Read forks
        forks_path = os.path.join(repo_path, "ghrs-data", "forks.csv")
        repo_stats.forks_series, repo_stats.forks, repo_stats.forks_growth = read_cumulative_series(
            forks_path, "forks_cumulative"
        )

        # Determine if repo has meaningful activity
        # Crickets = repos with no views, no stars, and only minimal clones (≤2)
        repo_stats.has_activity = (
            repo_stats.views_unique > 0
            or repo_stats.stars > 0
            or repo_stats.forks > 0
            or repo_stats.clones_unique >= 3
        )

        stats.append(repo_stats)

    # Sort by stars (descending), then by views
    stats.sort(key=lambda r: (r.stars, r.views_unique), reverse=True)

    return stats


def generate_sparkline_svg(
    series1: list,
    series2: list = None,
    width: int = 120,
    height: int = 32,
    color1: str = "#c95d2e",
    color2: str = "#d4a03c",
    cumulative: bool = False,
) -> tuple:
    """Generate an SVG sparkline with one or two series.

    Returns (svg_html, has_data, min_val, max_val).
    """
    if not series1 and not series2:
        # Empty placeholder
        svg = f'<svg viewBox="0 0 {width} {height}" class="sparkline"><text x="{width//2}" y="{height//2 + 4}" text-anchor="middle" fill="#999" font-size="10">no data</text></svg>'
        return svg, False, 0, 0

    # Combine series for scaling
    all_values = list(series1) if series1 else []
    if series2:
        all_values.extend(series2)

    if not all_values or max(all_values) == 0:
        svg = f'<svg viewBox="0 0 {width} {height}" class="sparkline"><line x1="0" y1="{height-4}" x2="{width}" y2="{height-4}" stroke="#ddd" stroke-width="1"/></svg>'
        return svg, False, 0, 0

    min_val = min(all_values) if cumulative else 0
    max_val = max(all_values)
    val_range = max_val - min_val if max_val != min_val else 1

    def make_points(series: list) -> str:
        if not series:
            return ""
        points = []
        step = width / max(len(series) - 1, 1)
        for i, val in enumerate(series):
            x = i * step
            # Invert Y (SVG 0 is top), leave padding
            y = height - 4 - ((val - min_val) / val_range) * (height - 8)
            points.append(f"{x:.1f},{y:.1f}")
        return " ".join(points)

    svg_parts = [f'<svg viewBox="0 0 {width} {height}" class="sparkline">']

    # Series 1 (solid line)
    if series1:
        points1 = make_points(series1)
        svg_parts.append(
            f'<polyline points="{points1}" fill="none" stroke="{color1}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    # Series 2 (dashed line)
    if series2:
        points2 = make_points(series2)
        svg_parts.append(
            f'<polyline points="{points2}" fill="none" stroke="{color2}" stroke-width="1.5" stroke-dasharray="3,2" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts), True, min_val, max_val


def format_number(n: int) -> str:
    """Format number with K suffix for thousands."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def generate_repo_card(repo: RepoStats, ghpages_dir: str) -> str:
    """Generate HTML for a single repository card."""
    # Traffic sparkline (views solid, clones dashed)
    traffic_spark, traffic_has_data, traffic_min, traffic_max = generate_sparkline_svg(
        repo.views_series,
        repo.clones_series,
        color1="#c95d2e",  # orange - views
        color2="#d4a03c",  # mustard - clones
    )

    # Growth sparkline (stars solid, forks dashed)
    growth_spark, growth_has_data, growth_min, growth_max = generate_sparkline_svg(
        repo.stars_series,
        repo.forks_series,
        color1="#e8b923",  # gold - stars
        color2="#8b5a2b",  # sienna - forks
        cumulative=True,
    )

    # Growth indicator
    growth_text = ""
    if repo.stars_growth > 0:
        growth_text = f'<span class="growth positive">+{repo.stars_growth} stars</span>'
    elif repo.stars_growth < 0:
        growth_text = f'<span class="growth negative">{repo.stars_growth} stars</span>'

    traffic_data_attr = ' data-has-data="1"' if traffic_has_data else ""
    growth_data_attr = ' data-has-data="1"' if growth_has_data else ""

    traffic_axis_labels = (
        f'<span class="axis-label axis-max">{format_number(traffic_max)}</span>'
        f'<span class="axis-label axis-min">{format_number(traffic_min)}</span>'
        if traffic_has_data else ""
    )
    growth_axis_labels = (
        f'<span class="axis-label axis-max">{format_number(growth_max)}</span>'
        f'<span class="axis-label axis-min">{format_number(growth_min)}</span>'
        if growth_has_data else ""
    )

    return f"""
    <a href="{repo.name}/" class="card-link">
      <div class="card">
        <div class="card-header">
          <span class="repo-name">{repo.name}</span>
          <span class="repo-stats">
            <span class="stat">⭐ {repo.stars}</span>
            <span class="stat">🍴 {repo.forks}</span>
          </span>
        </div>
        <div class="card-body">
          <div class="chart-section"{traffic_data_attr}>
            <div class="chart-label">Traffic <span class="period">(14d)</span></div>
            <div class="sparkline-wrap">
              {traffic_spark}
              {traffic_axis_labels}
            </div>
            <div class="chart-stats">
              <span class="views">👁 {format_number(repo.views_unique)}</span>
              <span class="clones">📋 {format_number(repo.clones_unique)}</span>
            </div>
          </div>
          <div class="chart-section"{growth_data_attr}>
            <div class="chart-label">Growth <span class="period">(90d)</span></div>
            <div class="sparkline-wrap">
              {growth_spark}
              {growth_axis_labels}
            </div>
            <div class="chart-stats">
              {growth_text if growth_text else '<span class="neutral">—</span>'}
            </div>
          </div>
        </div>
      </div>
    </a>
    """


def generate_cricket_card(repo: RepoStats) -> str:
    """Generate HTML for a cricket (inactive) repository card."""
    return f"""
    <a href="{repo.name}/" class="cricket-link">
      <div class="cricket-card">
        <span class="cricket-name">{repo.name}</span>
        <span class="cricket-stats">⭐ {repo.stars}</span>
        <span class="cricket-arrow">→</span>
      </div>
    </a>
    """


# Kept out of the f-string template so the CSS braces don't need doubling.
STARS_MODAL_HTML = """
    <div class="modal-overlay" id="stars-modal" hidden>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="stars-modal-title">
        <div class="modal-header">
          <div>
            <div class="modal-title" id="stars-modal-title">All Time Stars</div>
            <div class="modal-subtitle">Stars across all repositories, by week</div>
          </div>
          <div class="modal-actions">
            <button type="button" class="modal-btn" id="stars-reset">Reset zoom</button>
            <button type="button" class="modal-btn" id="stars-table-toggle" aria-pressed="false">Table</button>
            <button type="button" class="modal-btn modal-close" id="stars-close" aria-label="Close">&times;</button>
          </div>
        </div>
        <div class="chart-host" id="stars-chart"></div>
        <div class="stars-table-wrap" id="stars-table-wrap" tabindex="0" hidden></div>
        <div class="modal-hint" id="stars-hint">
          Drag across the chart to zoom &middot; scroll to zoom &middot; double-click to reset &middot;
          hover a week (dots mark weeks that moved) for the per-repository breakdown
        </div>
        <div class="chart-tooltip" id="stars-tooltip" hidden></div>
      </div>
    </div>
"""


STARS_MODAL_CSS = """
    /* ---- "total stars" tile: opens the All Time Stars chart ---- */
    .header-stat-button {
      background: none;
      border: 0;
      padding: 0;
      font: inherit;
      color: inherit;
      text-align: left;
      cursor: pointer;
    }

    .header-stat-button .header-stat-label::after {
      content: ' \\203A';
      opacity: 0.45;
    }

    .header-stat-button:hover .header-stat-value,
    .header-stat-button:focus-visible .header-stat-value {
      color: var(--gold-deep);
    }

    .header-stat-button:hover .header-stat-label,
    .header-stat-button:focus-visible .header-stat-label {
      color: var(--brown);
      text-decoration: underline;
      text-underline-offset: 3px;
    }

    .header-stat-button:hover .header-stat-label::after,
    .header-stat-button:focus-visible .header-stat-label::after {
      opacity: 1;
    }

    .header-stat-button:focus-visible {
      outline: 2px solid var(--gold-deep);
      outline-offset: 6px;
      border-radius: 6px;
    }

    /* ---- Modal shell ---- */
    body.modal-open {
      overflow: hidden;
    }

    .modal-overlay {
      position: fixed;
      inset: 0;
      z-index: 50;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: rgba(61, 44, 41, 0.45);
    }

    .modal-overlay[hidden] {
      display: none;
    }

    .modal {
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 12px;
      width: min(980px, 100%);
      max-height: 90vh;
      padding: 20px 24px 18px;
      background: var(--card-bg);
      border-radius: 14px;
      box-shadow: 0 16px 48px rgba(61, 44, 41, 0.3);
    }

    .modal-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }

    .modal-title {
      font-size: 1.2rem;
      font-weight: 600;
      color: var(--brown);
    }

    .modal-subtitle {
      font-size: 0.85rem;
      color: var(--brown-light);
      opacity: 0.8;
    }

    .modal-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .modal-btn {
      font: inherit;
      font-size: 0.8rem;
      color: var(--brown-light);
      background: none;
      border: 1px solid rgba(61, 44, 41, 0.18);
      border-radius: 6px;
      padding: 4px 10px;
      cursor: pointer;
      transition: background 0.15s ease, color 0.15s ease;
    }

    .modal-btn:hover {
      background: rgba(61, 44, 41, 0.06);
      color: var(--brown);
    }

    .modal-btn[aria-pressed="true"] {
      background: rgba(168, 121, 15, 0.14);
      border-color: var(--gold-deep);
      color: var(--brown);
    }

    .modal-close {
      font-size: 1.3rem;
      line-height: 1;
      padding: 2px 9px 5px;
    }

    /* ---- Chart ---- */
    .chart-host {
      width: 100%;
      height: 46vh;
      min-height: 260px;
      max-height: 420px;
      /* keep drag-to-zoom from selecting the surrounding text */
      user-select: none;
      -webkit-user-select: none;
    }

    .stars-svg {
      display: block;
      width: 100%;
      height: 100%;
      touch-action: none;
      cursor: crosshair;
    }

    /* SVG is not covered by the UA's :focus:not(:focus-visible) reset, so opt out
       explicitly and re-add a ring only for keyboard focus. */
    .stars-svg:focus {
      outline: none;
    }

    .stars-svg:focus-visible {
      outline: 2px solid var(--gold-deep);
      outline-offset: 2px;
    }

    .stars-svg .grid,
    .stars-svg .axis {
      stroke: rgba(61, 44, 41, 0.12);
      stroke-width: 1;
      shape-rendering: crispEdges;
    }

    .stars-svg .axis {
      stroke: rgba(61, 44, 41, 0.28);
    }

    .stars-svg .tick {
      font-size: 11px;
      font-variant-numeric: tabular-nums;
      fill: var(--brown-light);
      opacity: 0.75;
    }

    .stars-svg .area {
      fill: rgba(168, 121, 15, 0.12);
    }

    .stars-svg .line {
      fill: none;
      stroke: var(--gold-deep);
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .stars-svg .pip {
      fill: var(--gold-deep);
      stroke: var(--card-bg);
      stroke-width: 2;
    }

    .stars-svg .pip-down {
      fill: var(--brown-light);
    }

    .stars-svg .cross-line {
      stroke: rgba(61, 44, 41, 0.35);
      stroke-width: 1;
      shape-rendering: crispEdges;
    }

    .stars-svg .cross-dot {
      fill: var(--card-bg);
      stroke: var(--gold-deep);
      stroke-width: 2;
    }

    .stars-svg .cross-dot-change {
      fill: var(--gold-deep);
    }

    .stars-svg .sel-rect {
      fill: rgba(168, 121, 15, 0.16);
      stroke: var(--gold-deep);
      stroke-width: 1;
    }

    /* ---- Tooltip ---- */
    .chart-tooltip {
      position: absolute;
      z-index: 5;
      min-width: 150px;
      max-width: 260px;
      padding: 10px 12px;
      background: #fffdf9;
      border: 1px solid rgba(61, 44, 41, 0.14);
      border-radius: 8px;
      box-shadow: 0 4px 16px rgba(61, 44, 41, 0.18);
      pointer-events: none;
      font-size: 0.8rem;
      line-height: 1.35;
    }

    .chart-tooltip[hidden] {
      display: none;
    }

    .tip-week {
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      color: var(--brown-light);
      opacity: 0.75;
    }

    .tip-total {
      margin-top: 2px;
      color: var(--brown);
    }

    .tip-total-num {
      font-size: 1.15rem;
      font-weight: 700;
    }

    .tip-total-unit {
      font-size: 0.78rem;
      color: var(--brown-light);
    }

    .tip-delta {
      font-weight: 600;
      margin-top: 1px;
    }

    .tip-delta.pos,
    .tip-amt.pos {
      color: #2e8b57;
    }

    .tip-delta.neg,
    .tip-amt.neg {
      color: var(--orange);
    }

    .tip-none {
      margin-top: 1px;
      color: var(--brown-light);
      opacity: 0.7;
    }

    .tip-list {
      list-style: none;
      margin: 8px 0 0;
      padding: 8px 0 0;
      border-top: 1px solid rgba(61, 44, 41, 0.12);
    }

    .tip-list li {
      display: flex;
      gap: 8px;
      align-items: baseline;
    }

    .tip-amt {
      flex: none;
      min-width: 30px;
      text-align: right;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }

    .tip-repo {
      color: var(--brown-light);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* ---- Table view (the tooltip's WCAG-clean twin) ---- */
    .stars-table-wrap {
      overflow: auto;
      max-height: 46vh;
    }

    .stars-table-wrap[hidden] {
      display: none;
    }

    .stars-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
    }

    .stars-table th,
    .stars-table td {
      text-align: left;
      padding: 6px 10px;
      border-bottom: 1px solid rgba(61, 44, 41, 0.1);
      white-space: nowrap;
    }

    .stars-table th {
      position: sticky;
      top: 0;
      background: var(--card-bg);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      color: var(--brown-light);
    }

    .stars-table td.num {
      font-variant-numeric: tabular-nums;
      text-align: right;
      font-weight: 600;
    }

    .stars-table td.repos {
      white-space: normal;
      color: var(--brown-light);
    }

    .modal-hint {
      font-size: 0.75rem;
      color: var(--brown-light);
      opacity: 0.65;
      user-select: none;
      -webkit-user-select: none;
    }

    @media (max-width: 600px) {
      .modal {
        padding: 16px;
      }

      .chart-host {
        height: 40vh;
        min-height: 220px;
      }
    }
"""


# Kept out of the f-string template so the JS braces don't need doubling.
STARS_MODAL_JS = """
(function () {
  var dataEl = document.getElementById('stars-timeline-data');
  if (!dataEl) return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (err) {
    return;
  }

  var weeks = data.weeks || [];
  var totals = data.totals || [];
  var changes = data.changes || [];
  var N = weeks.length;
  if (!N) return;

  var tile = document.getElementById('stars-tile');
  var overlay = document.getElementById('stars-modal');
  var host = document.getElementById('stars-chart');
  var tooltip = document.getElementById('stars-tooltip');
  var tableWrap = document.getElementById('stars-table-wrap');
  var hint = document.getElementById('stars-hint');
  var resetBtn = document.getElementById('stars-reset');
  var tableBtn = document.getElementById('stars-table-toggle');
  var closeBtn = document.getElementById('stars-close');
  if (!tile || !overlay || !host || !tooltip) return;

  var SVGNS = 'http://www.w3.org/2000/svg';
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var PAD = { top: 20, right: 20, bottom: 36, left: 56 };
  var MIN_SPAN = 2;

  var lo = 0;
  var hi = N - 1;
  var focusIdx = null;
  var geo = null;
  var svg = null;
  var crossG = null;
  var selRect = null;
  var dragFrom = null;
  var tableMode = false;

  function svgEl(name, attrs) {
    var node = document.createElementNS(SVGNS, name);
    for (var key in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, key)) {
        node.setAttribute(key, attrs[key]);
      }
    }
    return node;
  }

  function weekDate(i) {
    var p = weeks[i].split('-');
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
  }

  function fmtTick(i, wide) {
    var d = weekDate(i);
    if (wide) return MONTHS[d.getUTCMonth()] + " '" + String(d.getUTCFullYear()).slice(-2);
    return MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate();
  }

  function fmtFull(i) {
    var d = weekDate(i);
    return MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' + d.getUTCFullYear();
  }

  function changeTotal(i) {
    var c = changes[i];
    if (!c) return 0;
    var sum = 0;
    for (var k in c) {
      if (Object.prototype.hasOwnProperty.call(c, k)) sum += c[k];
    }
    return sum;
  }

  function contributors(i) {
    var c = changes[i] || {};
    return Object.keys(c).sort(function (a, b) {
      return Math.abs(c[b]) - Math.abs(c[a]) || a.localeCompare(b);
    });
  }

  function signed(n) {
    return (n > 0 ? '+' : '') + n;
  }

  function niceMax(v) {
    if (v <= 5) return Math.max(v, 1);
    var mag = Math.pow(10, Math.floor(Math.log(v) / Math.LN10));
    var steps = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] * mag >= v) return Math.ceil(steps[i] * mag);
    }
    return Math.ceil(10 * mag);
  }

  function yTicks(max) {
    var n = max <= 4 ? Math.max(max, 1) : 4;
    var out = [];
    for (var i = 0; i <= n; i++) {
      var v = Math.round((max * i) / n);
      if (out.indexOf(v) === -1) out.push(v);
    }
    return out;
  }

  function render() {
    var w = Math.max(host.clientWidth || 640, 320);
    var h = Math.max(host.clientHeight || 320, 200);
    var plotW = w - PAD.left - PAD.right;
    var plotH = h - PAD.top - PAD.bottom;
    var span = Math.max(hi - lo, 1);
    var visMax = 0;
    var i;
    for (i = lo; i <= hi; i++) {
      if (totals[i] > visMax) visMax = totals[i];
    }
    var yMax = niceMax(visMax || 1);
    var base = PAD.top + plotH;

    geo = {
      w: w,
      h: h,
      plotW: plotW,
      plotH: plotH,
      span: span,
      // A single observed week has nothing to interpolate across: centre it.
      x: function (idx) {
        if (hi === lo) return PAD.left + plotW / 2;
        return PAD.left + ((idx - lo) / span) * plotW;
      },
      y: function (val) { return PAD.top + plotH - (val / yMax) * plotH; }
    };

    svg = svgEl('svg', {
      'class': 'stars-svg',
      viewBox: '0 0 ' + w + ' ' + h,
      width: w,
      height: h,
      tabindex: '0',
      role: 'img',
      'aria-label': 'Total stars across all repositories, by week. ' +
        totals[hi] + ' stars as of the week of ' + fmtFull(hi) + '.'
    });

    var ticks = yTicks(yMax);
    for (i = 0; i < ticks.length; i++) {
      var ty = geo.y(ticks[i]);
      svg.appendChild(svgEl('line', {
        'class': ticks[i] === 0 ? 'axis' : 'grid',
        x1: PAD.left, y1: ty, x2: w - PAD.right, y2: ty
      }));
      var ylab = svgEl('text', { 'class': 'tick', x: PAD.left - 10, y: ty + 4, 'text-anchor': 'end' });
      ylab.textContent = ticks[i];
      svg.appendChild(ylab);
    }

    var wide = span > 40;
    var maxLabels = Math.min(7, Math.max(2, Math.floor(plotW / 90)));
    var step = Math.max(1, Math.ceil((span + 1) / maxLabels));
    for (i = hi; i >= lo; i -= step) {
      var tx = geo.x(i);
      var anchor = 'middle';
      if (tx < PAD.left + 26) anchor = 'start';
      else if (tx > w - PAD.right - 26) anchor = 'end';
      var xlab = svgEl('text', { 'class': 'tick', x: tx, y: base + 20, 'text-anchor': anchor });
      xlab.textContent = fmtTick(i, wide);
      svg.appendChild(xlab);
    }

    var pts = [];
    for (i = lo; i <= hi; i++) {
      pts.push(geo.x(i).toFixed(2) + ',' + geo.y(totals[i]).toFixed(2));
    }
    svg.appendChild(svgEl('path', {
      'class': 'area',
      d: 'M' + geo.x(lo).toFixed(2) + ',' + base + ' L' + pts.join(' L') +
         ' L' + geo.x(hi).toFixed(2) + ',' + base + ' Z'
    }));
    svg.appendChild(svgEl('polyline', { 'class': 'line', points: pts.join(' ') }));

    for (i = lo; i <= hi; i++) {
      if (!changes[i]) continue;
      svg.appendChild(svgEl('circle', {
        'class': changeTotal(i) < 0 ? 'pip pip-down' : 'pip',
        cx: geo.x(i).toFixed(2),
        cy: geo.y(totals[i]).toFixed(2),
        r: 4
      }));
    }

    crossG = svgEl('g', { 'class': 'crosshair', style: 'display:none' });
    crossG.appendChild(svgEl('line', { 'class': 'cross-line', x1: 0, y1: PAD.top, x2: 0, y2: base }));
    crossG.appendChild(svgEl('circle', { 'class': 'cross-dot', cx: 0, cy: 0, r: 5 }));
    svg.appendChild(crossG);

    selRect = svgEl('rect', {
      'class': 'sel-rect', x: 0, y: PAD.top, width: 0, height: plotH, style: 'display:none'
    });
    svg.appendChild(selRect);

    svg.addEventListener('pointermove', onPointerMove);
    svg.addEventListener('pointerleave', clearFocus);
    svg.addEventListener('pointerdown', onPointerDown);
    svg.addEventListener('pointerup', onPointerUp);
    svg.addEventListener('pointercancel', cancelDrag);
    svg.addEventListener('wheel', onWheel, { passive: false });
    svg.addEventListener('dblclick', resetZoom);
    svg.addEventListener('keydown', onKeyDown);
    svg.addEventListener('blur', clearFocus);

    host.textContent = '';
    host.appendChild(svg);

    if (focusIdx !== null) {
      setFocus(Math.min(Math.max(focusIdx, lo), hi));
    }
    if (resetBtn) resetBtn.disabled = (lo === 0 && hi === N - 1);
  }

  function viewX(clientX) {
    var r = svg.getBoundingClientRect();
    if (!r.width) return 0;
    return (clientX - r.left) * (geo.w / r.width);
  }

  function indexAt(clientX) {
    return lo + ((viewX(clientX) - PAD.left) / geo.plotW) * geo.span;
  }

  function nearestIndex(clientX) {
    return Math.min(Math.max(Math.round(indexAt(clientX)), lo), hi);
  }

  function setFocus(i) {
    focusIdx = i;
    var x = geo.x(i);
    var y = geo.y(totals[i]);
    var line = crossG.firstChild;
    var dot = crossG.lastChild;
    line.setAttribute('x1', x);
    line.setAttribute('x2', x);
    dot.setAttribute('cx', x);
    dot.setAttribute('cy', y);
    dot.setAttribute('class', changes[i] ? 'cross-dot cross-dot-change' : 'cross-dot');
    crossG.style.display = '';
    showTooltip(i, x, y);
  }

  function clearFocus() {
    if (dragFrom !== null) return;
    focusIdx = null;
    if (crossG) crossG.style.display = 'none';
    hideTooltip();
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  function showTooltip(i, x, y) {
    tooltip.textContent = '';

    var head = document.createElement('div');
    head.className = 'tip-week';
    head.textContent = 'Week of ' + fmtFull(i);
    tooltip.appendChild(head);

    var totalLine = document.createElement('div');
    totalLine.className = 'tip-total';
    var num = document.createElement('span');
    num.className = 'tip-total-num';
    num.textContent = totals[i];
    var unit = document.createElement('span');
    unit.className = 'tip-total-unit';
    unit.textContent = totals[i] === 1 ? ' star total' : ' stars total';
    totalLine.appendChild(num);
    totalLine.appendChild(unit);
    tooltip.appendChild(totalLine);

    if (!changes[i]) {
      var none = document.createElement('div');
      none.className = 'tip-none';
      none.textContent = 'no change this week';
      tooltip.appendChild(none);
    } else {
      var delta = changeTotal(i);
      var deltaLine = document.createElement('div');
      deltaLine.className = 'tip-delta ' + (delta < 0 ? 'neg' : 'pos');
      deltaLine.textContent = signed(delta) + ' this week';
      tooltip.appendChild(deltaLine);

      var names = contributors(i);
      var list = document.createElement('ul');
      list.className = 'tip-list';
      for (var n = 0; n < names.length; n++) {
        var amount = changes[i][names[n]];
        var li = document.createElement('li');
        var amt = document.createElement('span');
        amt.className = 'tip-amt ' + (amount < 0 ? 'neg' : 'pos');
        amt.textContent = signed(amount);
        var repo = document.createElement('span');
        repo.className = 'tip-repo';
        repo.textContent = names[n];
        li.appendChild(amt);
        li.appendChild(repo);
        list.appendChild(li);
      }
      tooltip.appendChild(list);
    }

    tooltip.hidden = false;

    var parent = tooltip.offsetParent;
    if (!parent) return;
    var pr = parent.getBoundingClientRect();
    var sr = svg.getBoundingClientRect();
    var scale = sr.width / geo.w;
    var cx = sr.left + x * scale;
    var cy = sr.top + y * scale;

    var left = cx - pr.left - tooltip.offsetWidth / 2;
    left = Math.max(8, Math.min(left, pr.width - tooltip.offsetWidth - 8));
    var top = cy - pr.top - tooltip.offsetHeight - 14;
    if (top < 8) top = cy - pr.top + 18;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }

  function setRange(a, b) {
    a = Math.floor(a);
    b = Math.ceil(b);
    if (N - 1 <= MIN_SPAN) {
      lo = 0;
      hi = N - 1;
    } else {
      if (b - a < MIN_SPAN) {
        a = Math.round((a + b - MIN_SPAN) / 2);
        b = a + MIN_SPAN;
      }
      if (a < 0) { b -= a; a = 0; }
      if (b > N - 1) { a -= b - (N - 1); b = N - 1; }
      lo = Math.max(a, 0);
      hi = b;
    }
    focusIdx = null;
    hideTooltip();
    render();
  }

  function resetZoom(e) {
    if (e) e.preventDefault();
    lo = 0;
    hi = N - 1;
    focusIdx = null;
    hideTooltip();
    render();
  }

  function onPointerMove(e) {
    if (dragFrom !== null) {
      var a = Math.min(dragFrom, e.clientX);
      var b = Math.max(dragFrom, e.clientX);
      var x0 = Math.max(viewX(a), PAD.left);
      var x1 = Math.min(viewX(b), geo.w - PAD.right);
      selRect.setAttribute('x', x0);
      selRect.setAttribute('width', Math.max(x1 - x0, 0));
      selRect.style.display = x1 - x0 > 2 ? '' : 'none';
      return;
    }
    setFocus(nearestIndex(e.clientX));
  }

  function onPointerDown(e) {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    dragFrom = e.clientX;
    crossG.style.display = 'none';
    hideTooltip();
    try { svg.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  }

  function onPointerUp(e) {
    if (dragFrom === null) return;
    var from = dragFrom;
    dragFrom = null;
    selRect.style.display = 'none';
    try { svg.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    if (Math.abs(e.clientX - from) > 6) {
      setRange(indexAt(Math.min(from, e.clientX)), indexAt(Math.max(from, e.clientX)));
    } else {
      setFocus(nearestIndex(e.clientX));
    }
  }

  function cancelDrag() {
    dragFrom = null;
    if (selRect) selRect.style.display = 'none';
  }

  function onWheel(e) {
    if (N - 1 <= MIN_SPAN) return;
    e.preventDefault();
    var anchor = Math.min(Math.max(indexAt(e.clientX), lo), hi);
    var span = hi - lo;
    var next = Math.max(MIN_SPAN, Math.min(span * (e.deltaY < 0 ? 0.75 : 1 / 0.75), N - 1));
    var frac = span > 0 ? (anchor - lo) / span : 0.5;
    setRange(anchor - frac * next, anchor - frac * next + next);
  }

  function onKeyDown(e) {
    var i = focusIdx === null ? hi : focusIdx;
    if (e.key === 'ArrowLeft') i -= 1;
    else if (e.key === 'ArrowRight') i += 1;
    else if (e.key === 'Home') i = lo;
    else if (e.key === 'End') i = hi;
    else if (e.key === 'Escape' && (lo !== 0 || hi !== N - 1)) { resetZoom(e); return; }
    else return;
    e.preventDefault();
    setFocus(Math.min(Math.max(i, lo), hi));
  }

  function buildTable() {
    tableWrap.textContent = '';
    var table = document.createElement('table');
    table.className = 'stars-table';

    var caption = document.createElement('caption');
    caption.className = 'modal-hint';
    caption.textContent = 'Weeks in which the total changed, most recent first.';
    table.appendChild(caption);

    var thead = document.createElement('thead');
    var hrow = document.createElement('tr');
    ['Week of', 'Change', 'Total', 'Repositories'].forEach(function (label) {
      var th = document.createElement('th');
      th.scope = 'col';
      th.textContent = label;
      hrow.appendChild(th);
    });
    thead.appendChild(hrow);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    for (var i = N - 1; i >= 0; i--) {
      var week = changes[i];
      if (!week) continue;
      var tr = document.createElement('tr');

      var when = document.createElement('td');
      when.textContent = fmtFull(i);
      tr.appendChild(when);

      var delta = document.createElement('td');
      delta.className = 'num';
      delta.textContent = signed(changeTotal(i));
      tr.appendChild(delta);

      var total = document.createElement('td');
      total.className = 'num';
      total.textContent = totals[i];
      tr.appendChild(total);

      var who = document.createElement('td');
      who.className = 'repos';
      who.textContent = contributors(i).map(function (name) {
        return name + ' ' + signed(week[name]);
      }).join(', ');
      tr.appendChild(who);

      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    tableWrap.appendChild(table);
  }

  function setTableMode(on) {
    tableMode = on;
    host.hidden = on;
    tableWrap.hidden = !on;
    if (resetBtn) resetBtn.hidden = on;
    if (hint) hint.hidden = on;
    if (tableBtn) {
      tableBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      tableBtn.textContent = on ? 'Chart' : 'Table';
    }
    hideTooltip();
    if (on) {
      buildTable();
      tableWrap.focus();
    } else {
      render();
      if (svg) svg.focus();
    }
  }

  function openModal() {
    overlay.hidden = false;
    document.body.classList.add('modal-open');
    lo = 0;
    hi = N - 1;
    focusIdx = null;
    setTableMode(false);
  }

  function closeModal() {
    overlay.hidden = true;
    document.body.classList.remove('modal-open');
    hideTooltip();
    tile.focus();
  }

  tile.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (resetBtn) resetBtn.addEventListener('click', function () { resetZoom(); });
  if (tableBtn) tableBtn.addEventListener('click', function () { setTableMode(!tableMode); });

  overlay.addEventListener('mousedown', function (e) {
    if (e.target === overlay) closeModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !overlay.hidden) closeModal();
  });

  window.addEventListener('resize', function () {
    if (!overlay.hidden && !tableMode) render();
  });
})();
"""


def generate_dashboard_html(repos: list, ghpages_prefix: str, ghpages_dir: str, stats: list) -> str:
    """Generate the full dashboard HTML."""
    # Separate active and cricket repos
    active_repos = [r for r in stats if r.has_activity]
    cricket_repos = [r for r in stats if not r.has_activity]

    # Aggregate stats
    total_stars = sum(r.stars for r in stats)
    total_forks = sum(r.forks for r in stats)
    total_views = sum(r.views_unique for r in stats)
    total_clones = sum(r.clones_unique for r in stats)

    # Aggregate sparklines (sum all series)
    max_len = max((len(r.views_series) for r in stats), default=0)
    agg_views = [0] * max_len
    agg_clones = [0] * max_len
    for r in stats:
        for i, v in enumerate(r.views_series):
            if i < max_len:
                agg_views[i] += v
        for i, c in enumerate(r.clones_series):
            if i < max_len:
                agg_clones[i] += c

    agg_traffic_spark, _, _, _ = generate_sparkline_svg(
        agg_views, agg_clones, width=200, height=40, color1="#c95d2e", color2="#d4a03c"
    )

    # All Time Stars: weekly totals plus per-week attribution
    timeline = build_weekly_star_timeline(stats)
    has_timeline = bool(timeline["weeks"])
    # "</" is escaped so a repo name can never terminate the <script> block early.
    timeline_json = json.dumps(timeline, separators=(",", ":")).replace("<", "\\u003c")

    if has_timeline:
        stars_tile = f"""<button type="button" class="header-stat header-stat-button" id="stars-tile"
             aria-haspopup="dialog" title="All Time Stars">
          <span class="header-stat-value">{total_stars}</span>
          <span class="header-stat-label">total stars</span>
        </button>"""
        stars_modal = STARS_MODAL_HTML
        stars_payload = (
            f'<script type="application/json" id="stars-timeline-data">{timeline_json}</script>'
        )
        stars_script = f"<script>{STARS_MODAL_JS}</script>"
    else:
        stars_tile = f"""<div class="header-stat">
          <span class="header-stat-value">{total_stars}</span>
          <span class="header-stat-label">total stars</span>
        </div>"""
        stars_modal = ""
        stars_payload = ""
        stars_script = ""

    # Generate active repo cards
    active_cards_html = "\n".join(generate_repo_card(r, ghpages_dir) for r in active_repos)

    # Generate cricket cards
    cricket_cards_html = "\n".join(generate_cricket_card(r) for r in cricket_repos)
    cricket_section = ""
    if cricket_repos:
        cricket_section = f"""
    <div class="crickets-section">
      <h2 class="crickets-header">🦗 Crickets</h2>
      <div class="crickets-grid">
        {cricket_cards_html}
      </div>
    </div>
    """

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Repository Statistics Dashboard</title>
  <style>
    :root {{
      --cream: #f5f0e8;
      --card-bg: #faf7f2;
      --orange: #c95d2e;
      --mustard: #d4a03c;
      --gold: #e8b923;
      --gold-deep: #a8790f;
      --brown: #3d2c29;
      --brown-light: #5d4c49;
      --shadow: rgba(61, 44, 41, 0.08);
      --shadow-hover: rgba(61, 44, 41, 0.15);
    }}

    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}

    body {{
      min-height: 100vh;
      background: var(--cream);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: var(--brown);
      line-height: 1.5;
    }}

    .container {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }}

    /* Header */
    .dashboard-header {{
      background: var(--card-bg);
      border-radius: 12px;
      padding: 24px 32px;
      margin-bottom: 32px;
      box-shadow: 0 2px 8px var(--shadow);
    }}

    .header-title {{
      font-size: 1.5rem;
      font-weight: 600;
      margin-bottom: 16px;
      color: var(--brown);
    }}

    .header-stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 32px;
      align-items: flex-end;
    }}

    .header-stat {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    .header-stat-value {{
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--orange);
    }}

    .header-stat-label {{
      font-size: 0.85rem;
      color: var(--brown-light);
    }}

    .header-sparkline {{
      flex: 1;
      min-width: 200px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    .header-sparkline .sparkline {{
      width: 100%;
      height: 40px;
    }}

    /* Cards Grid */
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 24px;
      margin-bottom: 48px;
    }}

    @media (max-width: 900px) {{
      .cards-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    /* Card */
    .card-link {{
      text-decoration: none;
      color: inherit;
    }}

    .card {{
      background: var(--card-bg);
      border-radius: 12px;
      padding: 20px 24px;
      box-shadow: 0 2px 8px var(--shadow);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}

    .card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 4px 16px var(--shadow-hover);
    }}

    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(61, 44, 41, 0.1);
    }}

    .repo-name {{
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--brown);
    }}

    .repo-stats {{
      display: flex;
      gap: 12px;
    }}

    .stat {{
      font-size: 0.9rem;
      color: var(--brown-light);
    }}

    .card-body {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }}

    .chart-section {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}

    .chart-label {{
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--brown-light);
    }}

    .chart-label .period {{
      font-weight: 400;
      opacity: 0.7;
    }}

    .sparkline {{
      width: 100%;
      height: 32px;
    }}

    .chart-stats {{
      display: flex;
      gap: 12px;
      font-size: 0.85rem;
    }}

    .views {{
      color: var(--orange);
    }}

    .clones {{
      color: var(--mustard);
    }}

    .growth.positive {{
      color: #2e8b57;
    }}

    .growth.negative {{
      color: var(--orange);
    }}

    .neutral {{
      color: #999;
    }}

    /* Crickets Section */
    .crickets-section {{
      margin-top: 48px;
      padding-top: 32px;
      border-top: 1px dashed rgba(61, 44, 41, 0.2);
    }}

    .crickets-header {{
      font-size: 1rem;
      font-weight: 500;
      color: var(--brown-light);
      margin-bottom: 16px;
      opacity: 0.7;
    }}

    .crickets-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }}

    @media (max-width: 768px) {{
      .crickets-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}
    }}

    @media (max-width: 480px) {{
      .crickets-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .cricket-link {{
      text-decoration: none;
      color: inherit;
    }}

    .cricket-card {{
      background: var(--card-bg);
      border-radius: 8px;
      padding: 12px 16px;
      display: flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 1px 4px var(--shadow);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
      opacity: 0.75;
    }}

    .cricket-card:hover {{
      transform: translateY(-1px);
      box-shadow: 0 2px 8px var(--shadow-hover);
      opacity: 1;
    }}

    .cricket-name {{
      flex: 1;
      font-size: 0.9rem;
      font-weight: 500;
      color: var(--brown);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .cricket-stats {{
      font-size: 0.8rem;
      color: var(--brown-light);
    }}

    .cricket-arrow {{
      color: var(--orange);
      opacity: 0.5;
      transition: opacity 0.15s ease;
    }}

    .cricket-card:hover .cricket-arrow {{
      opacity: 1;
    }}

    /* Sparkline wrap (positions axis labels) */
    .sparkline-wrap {{
      position: relative;
    }}

    .axis-label {{
      display: none;
      position: absolute;
      right: 2px;
      font-size: 0.65rem;
      color: var(--brown-light);
      opacity: 0.7;
      pointer-events: none;
      line-height: 1;
    }}

    .axis-max {{
      top: 0;
    }}

    .axis-min {{
      bottom: 0;
    }}

    /* Clickable charts */
    .chart-section[data-has-data] {{
      cursor: pointer;
    }}

    /* Expanded state */
    .card.expanded .card-body {{
      display: block;
    }}

    .card.expanded .chart-section:not(.expanded) {{
      display: none;
    }}

    .card.expanded .chart-section.expanded .sparkline {{
      height: 80px;
    }}

    .card.expanded .chart-section.expanded .axis-label {{
      display: block;
    }}

    /* Footer */
    .dashboard-footer {{
      text-align: center;
      padding: 24px;
      font-size: 0.8rem;
      color: var(--brown-light);
      opacity: 0.6;
    }}
{STARS_MODAL_CSS}
  </style>
</head>
<body>
  <div class="container">
    <div class="dashboard-header">
      <h1 class="header-title">Repository Statistics</h1>
      <div class="header-stats">
        {stars_tile}
        <div class="header-stat">
          <span class="header-stat-value">{total_forks}</span>
          <span class="header-stat-label">total forks</span>
        </div>
        <div class="header-stat">
          <span class="header-stat-value">{format_number(total_views)}</span>
          <span class="header-stat-label">views (14d)</span>
        </div>
        <div class="header-stat">
          <span class="header-stat-value">{format_number(total_clones)}</span>
          <span class="header-stat-label">clones (14d)</span>
        </div>
        <div class="header-sparkline">
          <span class="header-stat-label">traffic trend</span>
          {agg_traffic_spark}
        </div>
      </div>
    </div>

    <div class="cards-grid">
      {active_cards_html}
    </div>

    {cricket_section}

    <div class="dashboard-footer">
      Last updated: {timestamp} · {len(stats)} repositories
    </div>
  </div>
{stars_modal}
  {stars_payload}
  <script>
    document.querySelectorAll('.chart-section[data-has-data]').forEach(function(sec) {{
      sec.addEventListener('click', function(e) {{
        e.preventDefault();
        e.stopPropagation();
        var card = sec.closest('.card');
        if (card.classList.contains('expanded') && sec.classList.contains('expanded')) {{
          card.classList.remove('expanded');
          sec.classList.remove('expanded');
        }} else {{
          card.querySelectorAll('.chart-section').forEach(function(s) {{ s.classList.remove('expanded'); }});
          card.classList.add('expanded');
          sec.classList.add('expanded');
        }}
      }});
    }});
  </script>
  {stars_script}
</body>
</html>
"""


def main():
    """Main entry point for the script."""
    if len(sys.argv) < 4:
        print(
            "Usage: generate_aggregate_index.py <repos_json> <ghpages_prefix> <ghpages_dir> [workspace_root]",
            file=sys.stderr,
        )
        sys.exit(1)

    repos_json = sys.argv[1]
    ghpages_prefix = sys.argv[2]
    ghpages_dir = sys.argv[3]
    workspace_root = sys.argv[4] if len(sys.argv) > 4 else os.getcwd()

    try:
        repos = json.loads(repos_json)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Collect stats from CSV files
    stats = collect_repo_stats(workspace_root, repos)

    # Generate dashboard HTML
    html = generate_dashboard_html(repos, ghpages_prefix, ghpages_dir, stats)
    print(html)


if __name__ == "__main__":
    main()
