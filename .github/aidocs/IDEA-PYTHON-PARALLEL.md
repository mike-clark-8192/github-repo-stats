# Idea: Replace entrypoint.sh with Python Orchestrator

## Problem
- Current `entrypoint.sh` (~400 lines) processes repos sequentially
- Bash concurrency is painful (pid juggling, exit code handling, no early abort)
- Git push contention was an issue with matrix parallelism

## Proposal
Replace bash with Python as the "top-level shell":

```python
#!/usr/bin/env python3
"""entrypoint.py - Python as the matrix, bash as the worker."""

import shlex
from subprocess import run
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_repo(repo: str) -> tuple[str, int, str]:
    """Fetch stats for one repo."""
    result = run(shlex.split(f"python fetch.py --repo {repo}"), capture_output=True, text=True)
    return repo, result.returncode, result.stderr

def main():
    repos = get_repo_list()

    # Fan out - parallel fetches (I/O bound, threads fine)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_repo, r): r for r in repos}
        for future in as_completed(futures):
            repo, code, err = future.result()
            if code != 0:
                print(f"FAILED: {repo}\n{err}")
                pool.shutdown(cancel_futures=True)
                sys.exit(1)
            print(f"✓ {repo}")

    # Sequential analysis
    for repo in repos:
        run(shlex.split(f"python analyze.py --repo {repo}"))

    # Generate dashboard
    run(shlex.split("python generate_aggregate_index.py ..."))

    # Single git commit/push (no contention!)
    run(shlex.split("git add site/"))
    run(shlex.split("git commit -m 'ghrs: update stats'"))
    run(shlex.split("git push origin main"))
```

## Benefits
- **No matrix job contention** - single job, single git writer
- **No overlapping Pages deploys** - one push at the end
- **Real error handling** - try/except, not `set -e` prayers
- **Easy parallelism** - `concurrent.futures` vs `&` and `wait` juggling
- **Readable** - ~200 lines of debuggable Python vs ~400 lines of bash
- **shlex.split()** - keeps commands bash-readable without bracket tax

## Notes
- Work is I/O bound (GitHub API latency), so threads work fine
- `max_workers=8` probably sweet spot (tune as needed)
- Rate limit handling stays in fetch.py
- Can abort early on first failure with `pool.shutdown(cancel_futures=True)`

## Action Items
- [ ] Translate entrypoint.sh → entrypoint.py
- [ ] Update action.yml to call Python instead of bash
- [ ] Test with a subset of repos first
- [ ] Tune max_workers based on observed performance

---
*"Python is the matrix and now I know kung fu."*
