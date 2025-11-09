#!/usr/bin/env python3
"""Generate aggregate index page for GitHub repository statistics.

This script creates an HTML index page that links to individual repository
statistics pages. It's designed to be used with the github-repo-stats action.
"""

import sys
import json
from datetime import datetime


def generate_index_html(repos, ghpages_prefix, ghpages_dir):
    """Generate HTML index page for repository statistics.

    Args:
        repos: List of repository specs (e.g., ["owner/repo1", "owner/repo2"])
        ghpages_prefix: URL prefix for GitHub Pages
        ghpages_dir: Directory name for GitHub Pages output

    Returns:
        HTML string for the index page
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Repository Statistics</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }}
        .repo-list {{
            list-style: none;
            padding: 0;
        }}
        .repo-item {{
            margin: 15px 0;
            padding: 10px;
            background: #f5f5f5;
            border-radius: 5px;
        }}
        .repo-item a {{
            text-decoration: none;
            color: #0366d6;
            font-size: 1.1em;
        }}
        .repo-item a:hover {{
            text-decoration: underline;
        }}
        .timestamp {{
            color: #666;
            font-size: 0.9em;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <h1>Repository Statistics</h1>
    <p>Statistics and analytics for monitored repositories.</p>

    <ol class="repo-list">
"""

    for repo in repos:
        repo_name = repo.split('/')[1]
        html += f"""        <li class="repo-item">
            <a href="{repo_name}/">{repo}</a>
        </li>
"""

    html += f"""    </ol>

    <div class="timestamp">
        <p>Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    </div>
</body>
</html>
"""

    return html


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 4:
        print("Usage: generate_aggregate_index.py <repos_json> <ghpages_prefix> <ghpages_dir>", file=sys.stderr)
        sys.exit(1)

    repos_json = sys.argv[1]
    ghpages_prefix = sys.argv[2]
    ghpages_dir = sys.argv[3]

    try:
        repos = json.loads(repos_json)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        sys.exit(1)

    html = generate_index_html(repos, ghpages_prefix, ghpages_dir)
    print(html)


if __name__ == "__main__":
    main()
