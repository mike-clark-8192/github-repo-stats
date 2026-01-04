#!/usr/bin/env python3
"""Generate aggregate dashboard for GitHub repository statistics.

Creates an information-dense dashboard with:
- Aggregate stats header
- Per-repo cards with sparkline charts
- "Crickets" section for inactive repos
"""

import sys
import json
import os
import csv
from datetime import datetime, timedelta
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

    cutoff = datetime.utcnow() - timedelta(days=days)

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

    cutoff = datetime.utcnow() - timedelta(days=days)
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

        # Read stars
        stars_path = os.path.join(repo_path, "ghrs-data", "stargazers.csv")
        repo_stats.stars_series, repo_stats.stars, repo_stats.stars_growth = read_cumulative_series(
            stars_path, "stars_cumulative"
        )

        # Read forks
        forks_path = os.path.join(repo_path, "ghrs-data", "forks.csv")
        repo_stats.forks_series, repo_stats.forks, repo_stats.forks_growth = read_cumulative_series(
            forks_path, "forks_cumulative"
        )

        # Determine if repo has activity
        repo_stats.has_activity = (
            repo_stats.views_unique > 0
            or repo_stats.clones_unique > 0
            or repo_stats.stars > 0
            or repo_stats.forks > 0
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
) -> str:
    """Generate an SVG sparkline with one or two series."""
    if not series1 and not series2:
        # Empty placeholder
        return f'<svg viewBox="0 0 {width} {height}" class="sparkline"><text x="{width//2}" y="{height//2 + 4}" text-anchor="middle" fill="#999" font-size="10">no data</text></svg>'

    # Combine series for scaling
    all_values = list(series1) if series1 else []
    if series2:
        all_values.extend(series2)

    if not all_values or max(all_values) == 0:
        return f'<svg viewBox="0 0 {width} {height}" class="sparkline"><line x1="0" y1="{height-4}" x2="{width}" y2="{height-4}" stroke="#ddd" stroke-width="1"/></svg>'

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
    return "".join(svg_parts)


def format_number(n: int) -> str:
    """Format number with K suffix for thousands."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def generate_repo_card(repo: RepoStats, ghpages_dir: str) -> str:
    """Generate HTML for a single repository card."""
    # Traffic sparkline (views solid, clones dashed)
    traffic_spark = generate_sparkline_svg(
        repo.views_series,
        repo.clones_series,
        color1="#c95d2e",  # orange - views
        color2="#d4a03c",  # mustard - clones
    )

    # Growth sparkline (stars solid, forks dashed)
    growth_spark = generate_sparkline_svg(
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
          <div class="chart-section">
            <div class="chart-label">Traffic <span class="period">(14d)</span></div>
            {traffic_spark}
            <div class="chart-stats">
              <span class="views">👁 {format_number(repo.views_unique)}</span>
              <span class="clones">📋 {format_number(repo.clones_unique)}</span>
            </div>
          </div>
          <div class="chart-section">
            <div class="chart-label">Growth <span class="period">(90d)</span></div>
            {growth_spark}
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

    agg_traffic_spark = generate_sparkline_svg(
        agg_views, agg_clones, width=200, height=40, color1="#c95d2e", color2="#d4a03c"
    )

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

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

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

    /* Footer */
    .dashboard-footer {{
      text-align: center;
      padding: 24px;
      font-size: 0.8rem;
      color: var(--brown-light);
      opacity: 0.6;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="dashboard-header">
      <h1 class="header-title">Repository Statistics</h1>
      <div class="header-stats">
        <div class="header-stat">
          <span class="header-stat-value">{total_stars}</span>
          <span class="header-stat-label">total stars</span>
        </div>
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
