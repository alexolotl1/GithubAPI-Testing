# AI Disclosure Scanner

Scans GitHub organizations to find out whether they publish a real AI disclosure / responsible-AI policy, using GitHub API + Groq AI classification + a two-stage web crawl (direct path probing + Scrapy).

## How it works

```
sample_100.json (repo list)
  → classify each org with Groq (type, likelihood of having an AI policy)
  → find the org's real website (GitHub API fields + domain guessing)
  → crawl the site for policy pages:
      1. direct probe of ~40 known policy paths (/responsible-ai, /ai-ethics, …)
      2. Scrapy crawl, depth 5, up to 100 pages, in a subprocess
  → score every page found with weighted phrase + proximity matching
  → send surviving candidates to Groq, which verifies each one individually
  → keep only pages Groq confirms are real (is_policy_page: true)
  → compute a 0-100 transparency score from Groq's own per-page score
  → write ai_disclosure_results.json
```

The two AI passes (crawler scoring, then Groq verification) exist because neither is reliable alone: the crawler scoring is fast but produces false positives (a conference page that happens to mention "policy" and "trusted"), and Groq is accurate but can't read 100 pages per org cheaply — so the crawler narrows candidates down first, and Groq makes the final call on each one.

## Files

| File | Role |
|---|---|
| `ai_disclosure_scanner.py` | Orchestrator. Loads repos, runs the pipeline per repo, writes results. |
| `web_crawler.py` | Finds the org website's policy pages — direct path probe + Scrapy subprocess crawl. |
| `groq_classifier.py` | All Groq calls: org classification, page verification, repo AI-usage analysis, summaries. |
| `verify_setup.py` | Pre-flight check — Python version, dependencies, API keys, input files. |
| `examples_and_config.py` | Copy-paste snippets: batch scanning, CSV export, custom prompts. |

## Setup

```bash
cd WebCrawling
pip install -r ../requirements.txt
export GROQ_API_KEY='gsk_your_key_here'      # console.groq.com, free tier
export GITHUB_TOKEN='ghp_your_token_here'    # optional, raises GitHub API rate limit
```

Verify before running a full scan:

```bash
python verify_setup.py
```

## Running it

```bash
python ai_disclosure_scanner.py --limit 5          # test run
python ai_disclosure_scanner.py                    # full scan, all repos in sample_100.json
python ai_disclosure_scanner.py --output out.json   # custom output path
python ai_disclosure_scanner.py --input ../my.json  # custom repo list
```

`--workers` is accepted for backward compatibility but crawls run sequentially per org (each Scrapy crawl spawns its own subprocess — see "Why a subprocess" below).

A full 100-repo scan now budgets up to 240s per org for the Scrapy stage alone, so expect this to take noticeably longer than earlier versions — that's intentional; the old version finished fast because it wasn't actually crawling anything (see Known issues, fixed).

Results save incrementally after every repo, so a crash or interrupt doesn't lose progress — just re-run and the output file will have everything completed so far.

## Output shape

Each entry in `scan_results` now distinguishes three buckets instead of one flat list:

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

`rejected_candidates` is kept in the output (not discarded) so you can see what the crawler flagged and why Groq said no — each entry carries a `groq_verdict` with a one-line `summary` explaining the rejection. Useful for tuning the scoring further if you find a pattern of misses.

## Why results were wrong before, and what changed

Three real bugs compounded into "scans run but find nothing, or find garbage":

**1. Scrapy crashes after the first org.** `CrawlerProcess` can only be started once per Python process — Twisted's reactor doesn't reset. Every org after the first silently produced zero crawl results. *Fixed by running each crawl in its own subprocess* (`web_crawler.py`, `_run_spider_subprocess`).

**2. Keyword matching was either too strict or too loose.** The original used exact multi-word phrases ("ai disclosure policy") that essentially never appear verbatim on a real page — zero recall. The first fix swung the other way to bare single tokens ("governance", "safety", "trusted", "policy") scored independently, which flagged anything containing common legal/corporate boilerplate — a conference announcement, a hardware product page, a docs index all "matched" because they happened to contain a few of those words anywhere on the page, including nav/footer menus. *Fixed with weighted phrase matching*: high-precision phrases ("responsible ai", "ai governance", "ai ethics") score heavily on their own; generic terms only count if they appear within ~100 characters of an actual AI-identifying term ("artificial intelligence", "machine learning", etc.), and nav/header/footer content is stripped before scoring so site-wide boilerplate links can't contribute. Minimum score to be considered a candidate raised from 2 to 8.

**3. Groq's verdict was computed but never actually used.** Pages sent to Groq for verification came back with a clear `is_policy_page: false` for things like a Zephyr Project conference page — but the code that computed the transparency score did a flat substring search like `'is_policy_page": true' in raw_text` against Groq's markdown-formatted reply, which never matched due to formatting differences. The false verdict was silently dropped and the page stayed in `policies_found` anyway. *Fixed two ways*: `groq_classifier.py` now asks Groq for one strict JSON array (not per-page markdown fences) which parses far more reliably, and `ai_disclosure_scanner.py` actually filters `confirmed_policies` vs `rejected_candidates` based on the parsed verdict before computing anything.

If you re-run the same five orgs from before, Zephyr's four false-positive pages (conference page, RTOS safety blog post, a hardware product called "Aistin", and a docs governance index) all score below threshold and get filtered out before ever reaching Groq — that pipeline stage was retested directly against the real text from your last run and confirmed.

## If results still look thin

The crawler can only find what's on the page. Things that won't help:
- Increasing depth further — depth 5 / 100 pages already covers most sites; if nothing's found at that depth, the org likely doesn't publish a policy page, or it's behind a path the prober doesn't know about (extend `KNOWN_POLICY_PATHS` in `web_crawler.py` if you spot a pattern).
- Loosening the score threshold — this is what caused the false-positive problem in the first place. If you genuinely think real pages are being missed, check `rejected_candidates` first; if Groq is rejecting things that should pass, that's a prompt-tuning problem in `groq_classifier.py`, not a threshold problem.

Things that will help if coverage still seems low:
- `GITHUB_TOKEN` — unauthenticated GitHub API calls are rate-limited to 60/hour, which will silently cause `_find_org_website` to start failing partway through a large scan. Set the token.
- Org website finder still misses orgs whose site isn't `{org}.com/.io/.org/.dev` and isn't listed in their GitHub profile — for those, manually map known orgs to URLs (see `examples_and_config.py`, Example 3) and feed it into `_find_org_website`.

## Customizing

**Add more policy paths to probe directly** — edit `KNOWN_POLICY_PATHS` in `web_crawler.py`.

**Add or reweight phrases** — edit `HIGH_VALUE_PHRASES` (phrase, weight) or `GENERIC_PROXIMITY_TERMS` in `web_crawler.py`. Keep weights roughly proportional to how unambiguous the phrase is — "ai disclosure" (9) should outweigh "principles" (3, and only when near an AI term).

**Change the Groq verification prompt** — `evaluate_policy_pages()` in `groq_classifier.py`. It currently asks for a strict JSON array with no prose; if you change the response shape, update `_parse_verdict_array` in the same file to match.

**Batch large repo lists, export to CSV, monitor progress** — see `examples_and_config.py`, which has ten ready-to-run snippets for these.

## Next steps worth doing

- The org website finder (`_find_org_website` in `ai_disclosure_scanner.py`) is still pattern-guessing for orgs without a `blog`/`website`/`homepage` field on GitHub. A small hardcoded map of well-known orgs (see `examples_and_config.py` Example 3) would improve hit rate cheaply.
- `rejected_candidates` data isn't analyzed anywhere yet — running `generate_report()` / `analyze_compliance()` from `examples_and_config.py` against a full scan would surface whether the scoring still over- or under-triggers at scale, beyond the 5-org sample tested here.
- No retry/backoff on Groq API errors — a transient failure on `evaluate_policy_pages()` currently leaves candidates `unverified` (correctly not counted as confirmed) for that repo, but a simple retry would recover more data on flaky connections.
