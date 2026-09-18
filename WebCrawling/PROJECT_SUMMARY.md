# AI Disclosure Scanner — Architecture & Modules

## Overview

Discovers and analyzes AI disclosure policies across GitHub organizations via a three-stage pipeline:

1. **GitHub API + Groq** — identify organizations, assess policy likelihood
2. **Scrapy web crawler** — find AI disclosure policies on org websites (direct probing + deep crawl)
3. **Groq verification** — classify and verify each discovered page individually

Output: `ai_disclosure_results.json`. See **SCANNER_GUIDE.md** for the full pipeline diagram, output shape, setup, and usage — this file covers module responsibilities and key design decisions only.

## Core modules

**`ai_disclosure_scanner.py`** — orchestrator. Loads the repo list, groups by owner, scans orgs concurrently (`MAX_ORG_WORKERS`, default 5), classifies each org with Groq, finds its website, runs the crawler, verifies candidates with Groq, computes a transparency score, saves incrementally (crash-safe).

**`web_crawler.py`** — finds AI policy pages on an org's website:
- Direct probe of ~90 known policy paths + 6 common docs/handbook subdomains (concurrent, 20 workers)
- Scrapy crawl, depth 5, up to 100 pages, run in its own subprocess
- Weighted phrase + proximity scoring, nav/header/footer stripped first, minimum score 8 to survive

**`groq_classifier.py`** — all Groq calls (model: `openai/gpt-oss-120b`), each with one retry on transient failure:
- `classify_organization_type()` — org type + AI-policy likelihood
- `evaluate_policy_pages()` — verify each candidate individually, strict JSON array response
- `analyze_repository_ai_usage()` — does this repo likely use/develop AI
- `summarize_disclosure_findings()` — plain-English summary

**Supporting**: `verify_setup.py` (pre-flight check), `examples_and_config.py` (copy-paste snippets), `.env.example` (key template), `testing/test_known_orgs.py` + `testing/known_policy_pages.json` (regression fixture, 12 known-good orgs).

## Key decisions

**Subprocess per org's Scrapy crawl** — `CrawlerProcess` can only start once per Python process (Twisted's reactor doesn't reset), so each crawl runs in its own subprocess.

**Two-stage scoring** — crawler scoring is fast but false-positive-prone; Groq is accurate but too slow/costly to run on every page a site has. Crawler narrows candidates (min score 8), Groq makes the final call.

**Weighted phrases + proximity checking** — bare words like "governance"/"safety"/"policy" appear everywhere (nav menus, legal boilerplate). High-value phrases ("responsible ai", "ai ethics") score heavily alone; generic terms only count near an actual AI-identifying term; nav/header/footer stripped before scoring.

## See also

- **SCANNER_GUIDE.md** — pipeline diagram, output shape, setup, usage, customization
- **HANDOFF.md** — session-by-session notes on what changed and why
