# GithubAPI-Testing

Research tooling for a paper on **AI usage disclosure in commits, in open-source projects and companies** — i.e. whether organizations publish policies on AI-generated/AI-assisted contributions (commit trailers like `Assisted-by:`/`Generated-by:`, "responsible AI" pages, contribution guidelines) and whether real-world repos actually follow them.

The repo is split into three independent folders, each usable on its own:

```
GithubAPI-Testing/
├── HeuristicScanner/         # 1. Fast keyword-based AI-usage scan across sampled repos
├── WebCrawling/              # 2. Crawls org websites for AI disclosure POLICY pages + Groq verification
├── OpenSourceContribution/   # 3. Checks real commits against a curated list of AI disclosure policies
├── requirements.txt          # shared dependencies for all folders
└── .gitignore
```

## Quick start

```bash
git clone <this repo>
cd GithubAPI-Testing
pip install -r requirements.txt

# One .env for the whole repo, kept inside WebCrawling/ (see "Secrets" below)
cp WebCrawling/.env.example WebCrawling/.env
# then edit WebCrawling/.env and fill in GROQ_API_KEY (and optionally GITHUB_TOKEN)
```

Then jump into whichever folder matches what you're doing — each has its own docs.

## 1. `HeuristicScanner/` — keyword scan across sampled repos

Answers: *"across a broad sample of GitHub repos, how many show any textual signal of AI usage at all (README, CONTRIBUTING, commit messages)?"* This is the cheap, high-recall/low-precision first pass — no LLM calls, just weighted keyword matching (`gpt`, `copilot`, `ai-generated`, etc.) plus a dedicated regex for explicit AI commit-trailer disclosures (`Co-Authored-By: Claude <noreply@anthropic.com>`, `Assisted-by: Copilot`) against README/CONTRIBUTING/commit text via the GitHub API.

| File | Role |
|---|---|
| `main.py` | Collects a pseudo-random sample of repos across years/languages, buckets by star count, plots `star_distribution.png`. |
| `save.py` | Same collection approach as `main.py`, but exports full repo details into `star_maps/*.json` (resumable via `checkpoint.json`). |
| `heuristic.py` | The actual heuristic scanner — reads `sample.json`, scans each repo's README/CONTRIBUTING/AGENTS.md/commits (6 repos concurrently) for AI keywords and AI commit-trailer disclosures, scores them, writes `ai_scan_results.json` incrementally. |
| `sample.json` | The base sample both `heuristic.py` and `WebCrawling/ai_disclosure_scanner.py` scan by default — currently the full `star_maps/stars_5k_plus.json` bucket (214 repos), so results describe high-star repos, not GitHub at large. |
| `star_maps/` | Repos bucketed by star count (`stars_0_100.json` … `stars_5k_plus.json`), produced by `save.py`. |
| `ai_scan_results.json` | Output of the last `heuristic.py` run. |
| `star_distribution.png` | Output chart from `main.py`. |

Run it:
```bash
cd HeuristicScanner
python heuristic.py     # scans sample.json, writes ai_scan_results.json
```

All scripts resolve their input/output paths relative to their own file location, so they behave the same whether you run them from inside `HeuristicScanner/` or from the repo root.

**This is deliberately a *heuristic*** — a repo scoring high just means AI-related words showed up somewhere; it's a candidate list, not a verdict. `WebCrawling/` is the more rigorous second pass (LLM-verified, policy-page-focused) for organizations, not individual repos.

## 2. `WebCrawling/` — org AI-disclosure policy scanner

Answers: *"does this organization publish a real AI disclosure / responsible-AI policy, and what does it say?"* Three-stage pipeline: classify the org with Groq → crawl its website (direct path probing + a Scrapy subprocess crawl) for policy pages → verify each candidate page with Groq before counting it. Orgs are scanned concurrently (5 at a time by default). Full details in [`WebCrawling/PROJECT_SUMMARY.md`](WebCrawling/PROJECT_SUMMARY.md) (architecture) and [`WebCrawling/SCANNER_GUIDE.md`](WebCrawling/SCANNER_GUIDE.md) (usage, troubleshooting, customization).

```bash
cd WebCrawling
python verify_setup.py                 # confirm keys/deps are set up
python ai_disclosure_scanner.py --limit 5   # test run
python ai_disclosure_scanner.py             # full scan
python testing/test_known_orgs.py --quick   # regression check: does it still find known-good policy pages?
```

Its keyword/scoring library and Groq verification prompt cover **two distinct policy genres** — this matters if you're extending it:
1. Corporate "responsible AI" governance pages (an org's policy on its *own* AI use).
2. **AI-usage-in-contributions disclosure** (a project's policy on how *contributors* may use AI, e.g. `Assisted-by:`/`Generated-by:` commit trailers) — this is the genre most directly relevant to the paper's actual research question, and the one most likely to be missed if you only tune for #1. See `testing/known_policy_pages.json` for 12 verified real-world examples.

## 3. `OpenSourceContribution/` — policy-vs-practice compliance scanner

Answers: *for projects whose own policy requires AI-use disclosure, do contributors actually disclose it in their commits?* Takes a curated, human-verified list of real policies ([melissawm/open-source-ai-contribution-policies](https://github.com/melissawm/open-source-ai-contribution-policies)) — filtered to projects where AI is allowed AND disclosure is required — and scans each resolved GitHub repo's recent commits for the same `Assisted-by:`/`Co-Authored-By:` trailer convention `HeuristicScanner/` looks for.

```bash
cd OpenSourceContribution
python fetch_policies.py       # -> policies.json/.csv
python disclosure_scanner.py    # -> disclosure_compliance_results.json
```

See [`OpenSourceContribution/README.md`](OpenSourceContribution/README.md) for the filtering logic and known limitations (recent commits only, commit trailers only, a few resolved repos are governance-only meta-repos).

## Secrets — one shared `.env`, kept in `WebCrawling/`

There's a single `.env` file for the entire repo, and it lives in `WebCrawling/.env` (not the root, not duplicated per folder):

```bash
cp WebCrawling/.env.example WebCrawling/.env
```

```
GROQ_API_KEY=gsk_...      # required for WebCrawling's Groq verification step
GITHUB_TOKEN=ghp_...      # optional, raises GitHub API rate limit 60/hr -> 5000/hr
```

- **`WebCrawling/`** scripts (`ai_disclosure_scanner.py`, `groq_classifier.py`, `verify_setup.py`) load it directly since it's in their own folder.
- **`HeuristicScanner/`** and **`OpenSourceContribution/`** scripts load `GITHUB_TOKEN` from the *same* `WebCrawling/.env` via an explicit relative path — they don't need their own copy. This is intentional: one token, one place to rotate it, no duplicated secret files to accidentally diverge or leak.
- `.env` is gitignored at the repo root (`.gitignore` → `.env`), so it's never committed regardless of which folder it's in. If you ever need a `.env` in a different folder, gitignore already covers `.env` anywhere in the tree — no changes needed there.
- `GROQ_API_KEY` is only used by `WebCrawling/`; the other two folders don't call Groq at all (pure keyword/regex matching), so they don't need that key.

## Shared files

- `requirements.txt` (repo root) — covers dependencies for *all* folders (`requests`/`matplotlib` for `HeuristicScanner`, `groq`/`scrapy`/`beautifulsoup4`/etc. for `WebCrawling`). Install once from the root.
- `HeuristicScanner/sample.json` — the canonical repo sample both `HeuristicScanner/heuristic.py` and `WebCrawling/ai_disclosure_scanner.py` scan by default, so results from both pipelines are directly comparable against the same repos.
