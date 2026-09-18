# HeuristicScanner

Fast, keyword-based first pass: across a broad sample of GitHub repos, which ones show *any* textual signal of AI usage — in their README, CONTRIBUTING/AGENTS.md files, or commit messages? No LLM calls, just weighted substring matching plus a regex for explicit AI commit-trailer disclosures, all via the GitHub REST API (no cloning).

This is deliberately high-recall/low-precision — a candidate list, not a verdict. See [`../WebCrawling/`](../WebCrawling/) for the LLM-verified, policy-page-focused pipeline that operates at the organization level instead.

## Files

| File | Role |
|---|---|
| `heuristic.py` | The scanner. Reads `sample.json`, fetches README/CONTRIBUTING/AGENTS.md/CODEOWNERS + last 50 commits per repo (6 repos concurrently), scores against `AI_KEYWORDS` plus an `AI_TRAILER_RE` regex for `Co-Authored-By:`/`Assisted-by:`/`Generated-by:` trailers naming an AI tool, writes `ai_scan_results.json` incrementally. |
| `main.py` | Collects a pseudo-random sample of ~5000 repos (varied creation years + languages, to avoid star-count bias), buckets by star range, plots `star_distribution.png`. Standalone — doesn't feed `sample.json`. |
| `save.py` | Same collection strategy as `main.py`, but exports full repo details into `star_maps/*.json`, resumable via `checkpoint.json`. |
| `sample.json` | The base sample both scanners scan by default — the entire `star_maps/stars_5k_plus.json` bucket (214 repos), so results describe high-star repos, not GitHub at large. Also read by `../WebCrawling/ai_disclosure_scanner.py`. |
| `star_maps/` | Output of `save.py` — repos bucketed by star count. |
| `ai_scan_results.json` | Output of the last `heuristic.py` run. |
| `star_distribution.png` | Output chart from `main.py`. |

All scripts resolve inputs/outputs relative to their own file location, so `python HeuristicScanner/heuristic.py` from the repo root and `python heuristic.py` from inside this folder behave identically.

## Setup

```bash
pip install -r ../requirements.txt
```

`GITHUB_TOKEN` is read from **`../WebCrawling/.env`** (see root [`README.md`](../README.md#secrets--one-shared-env-kept-in-webcrawling)). Technically optional, but close to required at this sample size: each repo costs up to 8 API calls, so 214 repos is ~1,700 requests — well past the unauthenticated 60/hr limit, vs. 5,000/hr with a real token.

A bad token degrades gracefully: a placeholder is never sent, and an invalid-but-real-looking token falls back to unauthenticated after one warning. If the rate limit runs out mid-scan, affected repos are marked `"scan_status": "skipped_rate_limited"` with a `scan_metadata.warning` — check that field first if a result file looks suspiciously empty.

## Running it

```bash
python heuristic.py                        # scan sample.json -> ai_scan_results.json
python heuristic.py --input other.json --output other_results.json
python main.py                              # fresh star-distribution sample -> star_distribution.png
python save.py                              # fresh sample -> star_maps/*.json (resumable)
```

Results save after every repo (not just at the end), so an interrupted run still leaves a usable partial `ai_scan_results.json`. A re-run redoes the whole input file rather than resuming/skipping.

`is_ai_related` is `true` once a repo's score crosses `AI_SCORE_THRESHOLD` (15). `score_breakdown` shows how much came from README vs. CONTRIBUTING-style files vs. commits; `ai_trailer_count` isolates the higher-confidence structured-trailer signal from generic keyword mentions.

## How scoring works

Two signals feed into one score:

1. **`AI_KEYWORDS`** — `{phrase: weight}`, whole-word matched, repeats count multiple times. Unambiguous phrases (`ai-generated`, `openai`) score high; anything with an obvious non-AI meaning is phrase-gated rather than left as a bare word (see below).
2. **`AI_TRAILER_RE`** — matches a commit trailer that explicitly names an AI tool, e.g. `Co-Authored-By: Claude <noreply@anthropic.com>`. This is the actual disclosure convention the research is about, not an incidental mention, so it's weighted higher (20 pts/match) and reported separately as `ai_trailer_count`.

**Where it looks**: README (`/readme` endpoint), `EXTRA_FILES_TO_SCAN` (`CONTRIBUTING.md`, `CONTRIBUTING.rst`, `AGENTS.md`, `CODEOWNERS`, `.github/CONTRIBUTING.md`, `.github/copilot-instructions.md` — 404s on missing ones are normal), and the last 50 commit messages.

**Known false positive, fixed**: bare `cursor`/`devin` used to be keywords; both are ordinary words/names outside AI contexts (`cursor` matched a literal database cursor in one commit). Now phrase-gated (`cursor ai`, `cursor agent`, `devin ai`, etc.).

## Customizing

- **Keywords** — edit `AI_KEYWORDS` in `heuristic.py`. Prefer a specific phrase over a bare word with an obvious non-AI meaning.
- **Trailer patterns** — edit `AI_TRAILER_RE`/`AI_TRAILER_SCORE` if you spot a real AI-tool trailer convention it's missing.
- **Files scanned per repo** — `EXTRA_FILES_TO_SCAN`.
- **Concurrency** — `MAX_WORKERS` (default 6); higher risks GitHub's secondary burst limit even with quota to spare.
- **Repo sample** — regenerate via `main.py`/`save.py`, or point `--input` at any file matching the schema: `{"repositories": [{"name": "owner/repo", "owner": "...", "owner_type": "Organization"|"User", "url": "...", "stars": N, "language": "...", ...}]}`.

## Known limitations

- Only the last 50 commits per repo — deeper AI-assisted history won't be counted.
- `sample.json` is the full 5,000+-star bucket only — findings describe popular repos, not GitHub at large.
- A run overwrites `ai_scan_results.json` fresh each time; copy it elsewhere first if you want history across runs.
