# AI Disclosure Scanner

Scans GitHub organizations for a real AI disclosure / responsible-AI policy, using GitHub API + Groq classification + a two-stage web crawl (direct path probing + Scrapy).

## How it works

```
../HeuristicScanner/sample.json (repo list)
  → group repos by owner; scan MAX_ORG_WORKERS (default 5) orgs concurrently
  → classify each org with Groq (type, likelihood of having an AI policy)
  → find the org's real website (GitHub API fields + domain guessing)
  → crawl the site for policy pages:
      1. direct probe of ~90 known policy paths + 6 docs/handbook subdomains
      2. Scrapy crawl, depth 5, up to 100 pages, in a subprocess
  → score every page found with weighted phrase + proximity matching
  → send surviving candidates (top 5 by score) to Groq, which verifies each individually
  → keep only pages Groq confirms are real (is_policy_page: true)
  → compute a 0-100 transparency score from Groq's own per-page score
  → write ai_disclosure_results.json
```

Two passes because neither is reliable alone: crawler scoring is fast but produces false positives; Groq is accurate but too slow/expensive to run on every page a site has. Crawler narrows candidates, Groq makes the final call.

## Files

| File | Role |
|---|---|
| `ai_disclosure_scanner.py` | Orchestrator. Loads repos, runs the pipeline per org (concurrently), writes results. |
| `web_crawler.py` | Finds the org website's policy pages — direct path probe + Scrapy subprocess crawl. |
| `groq_classifier.py` | All Groq calls: org classification, page verification, repo AI-usage analysis, summaries. |
| `verify_setup.py` | Pre-flight check — Python version, dependencies, API keys, input files. |
| `examples_and_config.py` | Copy-paste snippets: batch scanning, CSV export, custom prompts. |
| `testing/known_policy_pages.json` | Known-good AI disclosure pages used as a regression fixture. |
| `testing/test_known_orgs.py` | Runs the crawler against the known orgs above and checks the expected page is still found. |

## Setup

```bash
cd WebCrawling
pip install -r ../requirements.txt
cp .env.example .env      # fill in your keys — .env is gitignored, never committed
python verify_setup.py    # verify before running a full scan
```

```
GROQ_API_KEY=gsk_your_key_here      # console.groq.com, free tier
GITHUB_TOKEN=ghp_your_token_here    # optional, raises GitHub API rate limit
```

## Running it

```bash
python ai_disclosure_scanner.py --limit 5          # test run
python ai_disclosure_scanner.py                    # full scan, all repos in ../HeuristicScanner/sample.json
python ai_disclosure_scanner.py --output out.json   # custom output path
python ai_disclosure_scanner.py --input ../my.json  # custom repo list
python ai_disclosure_scanner.py --workers 8         # orgs scanned concurrently (default 5)
```

Each org budgets up to 240s for the Scrapy stage. Results save incrementally after every completed org, so a crash or interrupt doesn't lose progress — just re-run.

## Output shape

```json
{
  "owner": "acme-corp",
  "org_website": "https://acme.com",
  "candidate_pages": [ /* everything the crawler scored above threshold */ ],
  "confirmed_policies": [ /* Groq verified these are real */ ],
  "rejected_candidates": [ /* Groq verified these are NOT real policy pages */ ],
  "policies_found": [ /* = confirmed_policies, kept for backward compatibility */ ],
  "transparency_score": 62,
  "disclosure_required": true
}
```

`rejected_candidates` is kept (not discarded) — each carries a `groq_verdict.summary` explaining the rejection.

## Testing against known-good pages

`testing/known_policy_pages.json` lists 12 real, currently-live AI disclosure policy pages with their org homepage and expected policy URL.

```bash
python testing/test_known_orgs.py --quick   # direct known-path probe only, ~seconds
python testing/test_known_orgs.py           # full pipeline incl. Scrapy crawl, slower
```

If this regresses after changing `HIGH_VALUE_PHRASES`, `KNOWN_POLICY_PATHS`, `SUBDOMAIN_PROBE_PATHS`, or `MIN_SCORE`, the failing org's output shows which candidates were found instead.

The fixture's `known_limitations` section documents real pages the crawler currently *can't* find and why (anti-bot protection, content gated behind an internal-only link, an unpredictable blog-post URL). Not in the pass/fail list on purpose — check each entry's `reason` before loosening scoring to force one through.

## If results look thin

Won't help: more crawl depth (5/100 pages already covers most sites), or a lower score threshold (that reintroduces false positives).

Will help: setting `GITHUB_TOKEN` (unauthenticated calls rate-limit at 60/hr, silently breaking `_find_org_website` mid-scan), or extending `KNOWN_POLICY_PATHS` if you spot a real path pattern it's missing.

## Customizing

- **Policy paths** — `KNOWN_POLICY_PATHS` in `web_crawler.py`.
- **Phrases/weights** — `HIGH_VALUE_PHRASES` / `GENERIC_PROXIMITY_TERMS` in `web_crawler.py`.
- **Groq verification prompt** — `evaluate_policy_pages()` in `groq_classifier.py`; update `_parse_verdict_array` if you change the response shape.
- **Batch scans, CSV export, progress monitoring** — see `examples_and_config.py`.

## Next steps worth doing

- Hardcode an org→URL map for orgs the website finder still can't resolve (see `examples_and_config.py` Example 3).
- Run `analyze_compliance()` from `examples_and_config.py` against a full scan to see whether scoring over/under-triggers at scale.
- No retry/backoff on `_find_org_website`'s GitHub API calls specifically (Groq calls do retry now).
