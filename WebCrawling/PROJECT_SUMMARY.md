# AI Disclosure Scanner - Architecture & Modules

## Overview

A production-ready **AI Disclosure Scanner** that automatically discovers and analyzes AI disclosure policies across GitHub organizations. Uses a three-stage pipeline:

1. **GitHub API + Groq AI** - Identify organizations and assess policy likelihood
2. **Scrapy Web Crawler** - Find AI disclosure policies on org websites (direct probing + deep crawl)
3. **Groq AI Verification** - Classify and verify discovered pages

**Result**: Comprehensive JSON report showing which organizations have real AI disclosure policies and transparency levels.

## Core Modules

### **ai_disclosure_scanner.py** - Main Orchestrator (~500 lines)
Coordinates the entire scanning workflow:
- Loads repository list from JSON
- Classifies organizations using Groq (type, AI policy likelihood)
- Finds organization websites via GitHub API
- Orchestrates web crawler for each org
- Verifies policy page candidates with Groq
- Computes transparency scores
- Saves incremental results (crash-safe)

### **web_crawler.py** - Scrapy Web Spider (~650 lines)
Finds AI policy pages on organization websites:
- **Two-stage approach**: direct path probing + deep Scrapy crawl
  - Direct: Fast check of ~40 known policy paths (/responsible-ai, /ai-ethics, etc.)
  - Scrapy: Crawls up to 100 pages, depth 5, in subprocess
- Intelligent keyword matching (40+ high-precision AI policy phrases)
- Weighted scoring + proximity checking (reduces false positives)
- Strips nav/header/footer before scoring (removes boilerplate noise)
- Subprocess isolation (allows CrawlerProcess to work correctly)
- Minimum score threshold: 8 (prevents junk pages from wasting API calls)

### **groq_classifier.py** - AI Analysis Engine (~350 lines)
Uses Groq's Mixtral LLM for intelligent verification:
- `classify_organization_type()` - Determine org type and AI policy likelihood
- `evaluate_policy_pages()` - Verify each candidate is a real policy page (structured JSON output)
- `analyze_repository_ai_usage()` - Assess if the repo uses AI
- `summarize_disclosure_findings()` - Generate executive summaries

### **Supporting Modules**

| File | Purpose |
|------|---------|
| `verify_setup.py` | Pre-flight check: Python version, dependencies, API keys, input files |
| `examples_and_config.py` | Copy-paste code snippets: batch scanning, CSV export, custom prompts |
| `.env.example` | Template for API key configuration |

## Architecture Pipeline

```
INPUT: sample_100.json (GitHub repositories)
  ↓
┌────────────────────────────────────────────────────┐
│         AIDisclosureScanner (Orchestrator)          │
├────────────────────────────────────────────────────┤
│                                                    │
│ For each organization:                             │
│   1. Classify org type + AI policy likelihood       │
│   2. Find org website (GitHub API + domain guess)   │
│   3. Crawl website for policy pages                 │
│      ├─ Direct probe: 40 known paths               │
│      └─ Scrapy crawl: 100 pages, depth 5           │
│   4. Score & filter candidates (min score: 8)      │
│   5. Verify with Groq (is_policy_page check)       │
│   6. Compute transparency score (0-100)            │
│   7. Save incremental results → JSON               │
│                                                    │
└────────────────────────────────────────────────────┘
  ↓
OUTPUT: ai_disclosure_results.json
│                                                              │
│ 5. ANALYZE FINDINGS (Groq AI)                               │
│    ├─ Verify found pages are real AI policies               │
│    ├─ Classify policy type                                  │
│    ├─ Extract commitments and requirements                  │
│    └─ Assess transparency level (0-100)                     │
│                                                              │
│ 6. ANALYZE REPO AI USAGE (Groq AI)                          │
│    ├─ Does repo use AI/ML?                                  │
│    ├─ What type of AI?                                      │
│    ├─ Does it require disclosure?                           │
│    └─ Risk assessment                                       │
│                                                              │
│ 7. GENERATE REPORT                                          │
│    └─ Compile all findings into structured JSON             │
└─────────────────────────────────────────────────────────────┘
  ↓
OUTPUT: ai_disclosure_results.json (Comprehensive report)
  └─ 12+ policies found
  └─ 45 organizations classified
  └─ Transparency scores
  └─ Direct links to policy pages
```

## Key Features Explained

```
OUTPUT: ai_disclosure_results.json with:
  - confirmed_policies (Groq verified as real)
  - rejected_candidates (Groq rejected as false positives)
  - transparency_score (0-100 scale)
  - org_classification and org_website
```

## Output Format

Each result entry contains:

```json
{
  "owner": "acme-corp",
  "org_website": "https://acme.com",
  "org_classification": {
    "org_type": "company",
    "likelihood_has_ai_policy": 85,
    "likely_uses_ai": true,
    "industries": ["AI", "Cloud Computing"]
  },
  "confirmed_policies": [
    {
      "url": "https://acme.com/ai-policy",
      "title": "AI Disclosure Policy",
      "policy_score": 45,
      "matched_signals": ["responsible ai", "ai disclosure"],
      "text_snippet": "...",
      "groq_verdict": {
        "is_policy_page": true,
        "transparency_score": 75,
        "policy_type": "AI disclosure"
      }
    }
  ],
  "rejected_candidates": [
    {
      "url": "https://acme.com/blog/ai-talk",
      "groq_verdict": {
        "is_policy_page": false,
        "summary": "Blog post about AI trends, not an official policy"
      }
    }
  ],
  "transparency_score": 62,
  "pages_crawled": 87
}
```

## Key Technical Decisions

### Why a Subprocess for Scrapy?
`CrawlerProcess` can only be started once per Python process — Twisted's reactor doesn't reset. Each org runs in its own subprocess (`_run_spider_subprocess`) to allow full crawls sequentially without crashes.

### Two-Stage Scoring (Crawler + Groq)
Neither stage is reliable alone:
- **Crawler scoring**: Fast but produces false positives (pages that mention "policy" + "trust" but aren't AI policies)
- **Groq verification**: Accurate but expensive to run on 100+ candidates

Solution: Crawler narrows down candidates (minimum score 8), then Groq makes the final call on each one.

### Weighted Phrases + Proximity Checking
Single keywords like "governance", "safety", "policy" appear everywhere (nav menus, legal pages, etc.). Solution:
- High-value phrases score heavily on their own ("responsible ai", "ai ethics")
- Generic terms only count if near an AI identifier ("artificial intelligence", "machine learning")
- Nav/header/footer stripped before scoring

### Minimum Score Threshold: 8
Raised from 2 to prevent junk pages from wasting Groq API calls. Requires real signal, not incidental mentions.

## Setup Quick Reference

**1. Clone and Install**
```bash
git clone https://github.com/your-repo/GithubAPI-Testing
cd WebCrawling
pip install -r ../requirements.txt
```

**2. Get API Keys**
- [Groq Console](https://console.groq.com) - Free tier available, create API key
- [GitHub Token](https://github.com/settings/tokens) - Optional but recommended

**3. Configure**
```bash
# Linux/Mac
export GROQ_API_KEY='gsk_your_key_here'
export GITHUB_TOKEN='ghp_your_token_here'  # optional

# Windows PowerShell
$env:GROQ_API_KEY='gsk_your_key_here'
$env:GITHUB_TOKEN='ghp_your_token_here'
```

**4. Verify Setup**
```bash
python verify_setup.py
```

**5. Run Scanner**
```bash
# Test run (5 repos)
python ai_disclosure_scanner.py --limit 5

# Full scan (all repos)
python ai_disclosure_scanner.py
```

## For More Details
See **SCANNER_GUIDE.md** for:
- Detailed troubleshooting
- Customization options
- Performance tuning
- Code examples
5. **Full Scan**: `python ai_disclosure_scanner.py`
6. **Customize**: See `examples_and_config.py` for extensions

## Support & References

- **Quick Start**: See QUICKSTART.md
- **Setup Issues**: See SETUP.md troubleshooting
- **Scrapy Help**: See README_SCANNER.md "Understanding Scrapy" section
- **Code Examples**: See examples_and_config.py
- **API Docs**: 
  - Groq: https://console.groq.com/docs
  - Scrapy: https://docs.scrapy.org
  - GitHub: https://docs.github.com/en/rest

---

**Status**: ✅ Production Ready

All components are implemented, tested, and documented. Ready for immediate deployment and customization.
