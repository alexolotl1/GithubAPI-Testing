# OpenSourceContribution

Answers: *for projects whose own policy says AI use is allowed but must be disclosed, do contributors actually disclose it in their commits?* This is the "policy vs. practice" leg — where [`../WebCrawling/`](../WebCrawling/) finds whether an org *publishes* a policy, this folder checks a curated, human-verified list of real policies against real commit history.

## How it works

```
melissawm/open-source-ai-contribution-policies (README table on GitHub)
  → fetch_policies.py parses the table into policies.json/.csv
  → filter to: AI/LLMs allowed == Yes AND Disclosure required == Yes
  → resolve each to a github.com/owner/repo (many projects link to their
    own docs site, not GitHub directly — resolved via a hand-checked map
    for well-known ones; foundations/orgs with no single canonical repo
    are left unresolved on purpose)
  → disclosure_scanner.py fetches up to 300 recent commits per resolved
    repo, scans each for an AI co-author trailer (same regex as
    ../HeuristicScanner/heuristic.py's AI_TRAILER_RE)
  → write disclosure_compliance_results.json: per-repo and aggregate
    disclosure rates
```

## Files

| File | Role |
|---|---|
| `fetch_policies.py` | Fetches & parses the source README table, writes `policies.json` (all projects + the qualifying subset) and `policies.csv`. |
| `disclosure_scanner.py` | Scans each qualifying repo's recent commits for AI-trailer disclosures, writes `disclosure_compliance_results.json`. |
| `policies.json` / `policies.csv` | Output of `fetch_policies.py` — every project in the source table, with parsed columns + resolved GitHub repo where possible. |
| `disclosure_compliance_results.json` | Output of `disclosure_scanner.py` — per-repo commit counts, trailer counts, and disclosure rate, plus an aggregate summary. |

## Running it

```bash
python fetch_policies.py                    # -> policies.json, policies.csv
python disclosure_scanner.py                 # -> disclosure_compliance_results.json
python disclosure_scanner.py --limit 5        # test run, first 5 qualifying repos
```

`GITHUB_TOKEN` is read from `../WebCrawling/.env`, same as the other two folders — same placeholder/invalid-token handling as `heuristic.py` (sanitized, one-time warning, automatic unauthenticated fallback, fast-fail on real exhaustion instead of hanging).

## What "qualifying" means

The source repo classifies each project across several columns; we filter to **`AI/LLMs allowed? == Yes` AND `Disclosure required? == Yes`** — projects where disclosure isn't just encouraged, it's the stated rule, so a missing trailer is a real compliance gap rather than an absence of policy. As of the last fetch: 183 projects tracked, 45 qualifying, 39 resolved to a single GitHub repo.

**6 qualifying projects couldn't be resolved to one repo** and are excluded from the scan rather than guessed: the Apache Software Foundation (policy applies org-wide, not to one repo), Fedora (thousands of package repos on Pagure, not GitHub), OpenInfra (foundation, not one repo), Drupal, EasyBuild, and Mesa (hosted on GitLab, not GitHub — this scanner only reaches the GitHub API). See `policies.json`'s full `all_projects` list for the complete picture including non-qualifying and non-GitHub projects.

## Known limitations

- **Recent commits only** (300/repo), not full history — high-velocity repos like `rust-lang/rust` may show a small, recent slice rather than the true rate since policy adoption.
- **Commit trailers only, not PR descriptions** — some policies (e.g. Homebrew's) accept disclosure in the PR body instead; not scanned here.
- **A few resolved repos are governance/meta repos** (`cilium/community`, `cloudnative-pg/governance`, `torchgeo/governance`) — hold the policy doc itself but see far less real contribution traffic than the project's actual code repo.
- **`.github.io`-style repo names are real**, not a parsing artifact — e.g. `stac-utils/stac-utils.github.io` is STAC's actual repo.
