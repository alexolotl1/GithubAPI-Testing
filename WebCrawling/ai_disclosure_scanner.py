"""
AI Disclosure Scanner — Main Orchestrator

CHANGES IN THIS VERSION (v3) — fixes the "everything gets flagged" bug:

  Root cause found in your last run:
    web_crawler found 4 "candidate" pages on zephyrproject.org (a conference
    page, a hardware product page, a docs index...). It sent them to Groq,
    and Groq CORRECTLY said is_policy_page: false for every single one.
    But compute_transparency_score() was doing a flat substring search like
    'is_policy_page": true' in analysis_text on Groq's raw markdown reply —
    that string never matches (Groq wraps each verdict in a separate ```json
    fence with different spacing), so the false verdict was silently
    ignored and all 4 junk pages stayed in policies_found.

  What changed here:
    1. _parse_policy_verdicts() — actually parses every ```json ... ```
       block out of Groq's evaluate_policy_pages() reply into structured
       per-URL verdicts (is_policy_page, transparency_score, policy_type,
       etc.), instead of treating the whole reply as one opaque string.
    2. scan_repository() now FILTERS policies_found down to only pages
       Groq confirmed as is_policy_page == true. Candidates Groq rejected
       are kept separately as 'rejected_candidates' for transparency /
       debugging, but no longer count toward disclosure or score.
    3. compute_transparency_score() rewritten to use the parsed verdicts
       directly (their own 0-100 transparency_score from Groq) instead of
       string-matching, weighted against org classification and risk.
    4. Website finder unchanged from v2 (already fixed: checks GitHub API
       blog/website/homepage fields + domain patterns, works for users too).
    5. Everything else (incremental saves, dedup-by-owner, CLI) unchanged.
"""

import json
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional, Set
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

from groq_classifier import GroqAIClassifier
from web_crawler import crawl_organization_sync

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GitHub website finder
# ---------------------------------------------------------------------------

def _find_org_website(owner: str, github_token: Optional[str] = None) -> Optional[str]:
    """
    Find an organisation's real website using the GitHub API.

    Checks org + user endpoints (blog, website, homepage fields), then
    falls back to common domain patterns. Works for orgs AND individuals.
    """
    headers = {}
    if github_token:
        headers['Authorization'] = f'token {github_token}'

    for endpoint in (
        f'https://api.github.com/orgs/{owner}',
        f'https://api.github.com/users/{owner}',
    ):
        try:
            resp = requests.get(endpoint, headers=headers, timeout=6)
            if resp.status_code != 200:
                continue
            data = resp.json()

            candidates = [
                data.get('blog', ''),
                data.get('website', ''),
                data.get('homepage', ''),
            ]

            for url in candidates:
                if not url:
                    continue
                url = url.strip()
                if 'github.com' in url:
                    continue
                if not url.startswith('http'):
                    url = 'https://' + url
                url = url.rstrip('/')
                if _url_reachable(url):
                    return url

        except Exception as e:
            logger.debug(f"GitHub API error for {owner}: {e}")

    clean = owner.lower().replace('-', '').replace('_', '')
    patterns = [
        f'https://{owner}.com',
        f'https://www.{owner}.com',
        f'https://{owner}.io',
        f'https://{owner}.org',
        f'https://{owner}.dev',
        f'https://{clean}.com',
        f'https://www.{clean}.com',
    ]
    for url in patterns:
        if _url_reachable(url):
            return url

    return None


def _url_reachable(url: str, timeout: int = 5) -> bool:
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={'User-Agent': 'Mozilla/5.0'})
        return r.status_code < 400
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Groq verdict handling
# ---------------------------------------------------------------------------
#
# NOTE: groq_classifier.evaluate_policy_pages() now does its own JSON-array
# parsing internally and returns a ready-to-use 'verdicts' list (see that
# file's _parse_verdict_array). This wrapper just extracts it defensively —
# kept as a separate function so callers have one place to add fallback
# logic if groq_classifier's parse ever comes back empty.


def _get_policy_verdicts(policy_analysis: Optional[Dict]) -> List[Dict]:
    """
    Extract the parsed verdict list from a groq_classifier.evaluate_policy_pages()
    result. Returns [] if analysis wasn't run or parsing failed — callers
    must treat that as "unknown/unverified", never as "all rejected".
    """
    if not policy_analysis or not isinstance(policy_analysis, dict):
        return []

    verdicts = policy_analysis.get('verdicts', [])
    if not verdicts and policy_analysis.get('parse_error'):
        logger.warning(f"Groq verdict parse error: {policy_analysis['parse_error']}")

    return verdicts if isinstance(verdicts, list) else []


def _filter_policies_by_verdict(
    candidate_pages: List[Dict],
    verdicts: List[Dict],
) -> tuple:
    """
    Cross-reference crawled candidate pages with Groq's per-URL verdicts.

    Returns (confirmed, rejected, unverified):
      confirmed  — Groq said is_policy_page: true, merged with Groq's data
      rejected   — Groq said is_policy_page: false
      unverified — no matching verdict found (e.g. Groq call failed,
                   or this page was beyond the top-5 sent for analysis)

    Matching is done by URL since that's the only stable join key Groq
    is asked to echo back.
    """
    verdict_by_url = {v.get('url', '').rstrip('/'): v for v in verdicts if v.get('url')}

    confirmed, rejected, unverified = [], [], []

    for page in candidate_pages:
        url_key = page.get('url', '').rstrip('/')
        verdict = verdict_by_url.get(url_key)

        if verdict is None:
            # Try a loose match (Groq sometimes normalises trailing slashes
            # or query strings differently)
            for vurl, v in verdict_by_url.items():
                if vurl and (vurl in url_key or url_key in vurl):
                    verdict = v
                    break

        if verdict is None:
            unverified.append(page)
            continue

        merged = {**page, 'groq_verdict': verdict}
        if verdict.get('is_policy_page') is True:
            confirmed.append(merged)
        elif verdict.get('is_policy_page') is False:
            rejected.append(merged)
        else:
            unverified.append(merged)

    return confirmed, rejected, unverified


# ---------------------------------------------------------------------------
# Transparency scoring
# ---------------------------------------------------------------------------

def compute_transparency_score(result: Dict) -> int:
    """
    Compute a 0-100 transparency score from multiple signals.

    Now driven by PARSED Groq verdicts (result['confirmed_policies'], each
    carrying its own groq_verdict.transparency_score) rather than a broken
    substring search on raw text. A page only counts here if Groq itself
    confirmed it's a real policy page.
    """
    score = 0
    confirmed = result.get('confirmed_policies', [])
    rejected = result.get('rejected_candidates', [])
    org_class = result.get('org_classification') or {}
    ai_analysis = result.get('ai_usage_analysis') or {}

    # Base: website found
    if result.get('org_website'):
        score += 10

    # Confirmed policy pages — this is the main signal now
    if confirmed:
        # Use Groq's own transparency_score per page (0-100), averaged,
        # scaled into a 0-50 contribution
        groq_scores = [
            p.get('groq_verdict', {}).get('transparency_score', 0)
            for p in confirmed
        ]
        groq_scores = [s for s in groq_scores if isinstance(s, (int, float))]
        if groq_scores:
            avg_groq_score = sum(groq_scores) / len(groq_scores)
            score += int(avg_groq_score * 0.5)  # 0-50 pts

        # Bonus per confirmed page found (rewards having multiple), capped
        score += min(len(confirmed) * 5, 15)

    # Org classification suggests they SHOULD have a policy (0-10 pts)
    likelihood = org_class.get('likelihood_has_ai_policy', 0)
    score += int(likelihood / 10)

    # Penalty: candidates were found and crawled but ALL rejected by Groq —
    # this means the org has policy-adjacent pages but nothing AI-specific
    if rejected and not confirmed:
        score -= 5

    # Penalty: high-risk AI usage with zero confirmed policies
    if ai_analysis.get('risk_level') == 'high' and not confirmed:
        score -= 10

    return max(0, min(score, 100))


# ---------------------------------------------------------------------------
# Main scanner class
# ---------------------------------------------------------------------------

class AIDisclosureScanner:
    """Coordinates the full AI disclosure scanning workflow."""

    def __init__(
        self,
        sample_file: str = '../sample_100.json',
        output_file: str = 'ai_disclosure_results.json',
        groq_api_key: Optional[str] = None,
        github_token: Optional[str] = None,
        max_workers: int = 1,   # kept for API compatibility; crawls are sequential
    ):
        self.sample_file = sample_file
        self.output_file = output_file
        self.github_token = github_token or os.getenv('GITHUB_TOKEN')

        try:
            self.classifier = GroqAIClassifier(api_key=groq_api_key)
        except ValueError as e:
            logger.error(f"Groq init error: {e}")
            self.classifier = None

        self.results = {
            'scan_timestamp': datetime.now().isoformat(),
            'total_repositories': 0,
            'organizations_scanned': 0,
            'policies_found': 0,
            'scan_results': [],
            'summary': {},
        }
        self.processed_orgs: Dict[str, Dict] = {}
        self.repository_data: List[Dict] = []

        logger.info(f"AIDisclosureScanner ready. Output → {output_file}")

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_repositories(self) -> bool:
        try:
            with open(self.sample_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.repository_data = data.get('repositories', [])
            self.results['total_repositories'] = len(self.repository_data)
            logger.info(f"Loaded {len(self.repository_data)} repositories")
            return True
        except FileNotFoundError:
            logger.error(f"File not found: {self.sample_file}")
            return False
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON: {self.sample_file}")
            return False

    # ------------------------------------------------------------------
    # Website lookup
    # ------------------------------------------------------------------

    def get_org_website(self, repo: Dict) -> Optional[str]:
        owner = repo.get('owner', '')
        if not owner:
            return None
        if owner in self.processed_orgs:
            return self.processed_orgs[owner].get('org_website')
        website = _find_org_website(owner, self.github_token)
        logger.debug(f"Website for {owner}: {website}")
        return website

    # ------------------------------------------------------------------
    # Scan one repository
    # ------------------------------------------------------------------

    def scan_repository(self, repo: Dict) -> Dict:
        owner = repo.get('owner', 'Unknown')
        repo_name = repo.get('name', 'Unknown')

        logger.info(f"Scanning: {repo_name} by {owner}")

        result = {
            'repository': repo_name,
            'owner': owner,
            'owner_type': repo.get('owner_type', 'Unknown'),
            'repo_url': repo.get('url', ''),
            'github_url': f'https://github.com/{owner}',
            'language': repo.get('language', 'Unknown'),
            'stars': repo.get('stars', 0),
            'org_classification': None,
            'org_website': None,
            'crawl_results': None,
            'policy_analysis': None,
            'ai_usage_analysis': None,
            'candidate_pages': [],      # everything the crawler scored >= threshold
            'confirmed_policies': [],   # Groq verified is_policy_page: true
            'rejected_candidates': [],  # Groq verified is_policy_page: false
            'policies_found': [],       # kept for backward compatibility = confirmed_policies
            'disclosure_required': False,
            'transparency_score': 0,
            'summary': '',
            'scan_status': 'pending',
        }

        try:
            # ── Dedup: reuse crawl + Groq policy analysis for repeat orgs ────
            if owner in self.processed_orgs:
                prev = self.processed_orgs[owner]
                result.update({
                    'org_website':         prev.get('org_website'),
                    'org_classification':  prev.get('org_classification'),
                    'crawl_results':       prev.get('crawl_results'),
                    'candidate_pages':     prev.get('candidate_pages', []),
                    'confirmed_policies':  prev.get('confirmed_policies', []),
                    'rejected_candidates': prev.get('rejected_candidates', []),
                    'policy_analysis':     prev.get('policy_analysis'),
                })
                result['policies_found'] = result['confirmed_policies']

                if self.classifier:
                    result['ai_usage_analysis'] = self.classifier.analyze_repository_ai_usage(
                        repo_name,
                        repo.get('description', repo_name),
                        [repo.get('language', '')],
                        repo.get('topics', []),
                    )
                    if result['ai_usage_analysis'].get('requires_disclosure'):
                        result['disclosure_required'] = True
                result['transparency_score'] = compute_transparency_score(result)
                result['scan_status'] = 'completed_from_cache'
                return result

            # ── Step 1: Classify organisation ────────────────────────────────
            if self.classifier:
                logger.info(f"  → Classifying organisation…")
                result['org_classification'] = self.classifier.classify_organization_type(
                    owner,
                    repo.get('description', repo.get('name', '')),
                )

            # ── Step 2: Find website ─────────────────────────────────────────
            logger.info(f"  → Finding website…")
            result['org_website'] = self.get_org_website(repo)

            # ── Step 3: Crawl (direct probe + Scrapy) ───────────────────────
            if result['org_website']:
                logger.info(f"  → Crawling {result['org_website']}…")
                crawl = crawl_organization_sync(result['org_website'], owner)
                result['crawl_results'] = {
                    'org_url':       crawl.get('org_url'),
                    'org_name':      crawl.get('org_name'),
                    'pages_crawled': crawl.get('pages_crawled', 0),
                    'error':         crawl.get('error'),
                }
                candidates = crawl.get('policies', [])
                result['candidate_pages'] = candidates
                logger.info(
                    f"  → Found {len(candidates)} candidate page(s) "
                    f"({crawl.get('pages_crawled', 0)} pages crawled) "
                    f"— sending to Groq for verification"
                )

                # ── Step 4: Groq verifies candidates, THEN we filter ─────────
                if self.classifier and candidates:
                    result['policy_analysis'] = self.classifier.evaluate_policy_pages(candidates)
                    verdicts = _get_policy_verdicts(result['policy_analysis'])

                    if verdicts:
                        confirmed, rejected, unverified = _filter_policies_by_verdict(
                            candidates, verdicts
                        )
                        result['confirmed_policies'] = confirmed
                        result['rejected_candidates'] = rejected
                        # Unverified (e.g. beyond top-5 sent to Groq) are kept
                        # as low-confidence candidates, not counted as found
                        logger.info(
                            f"  → Groq verdict: {len(confirmed)} confirmed, "
                            f"{len(rejected)} rejected, {len(unverified)} unverified"
                        )
                    else:
                        # Groq call failed or returned unparseable text —
                        # don't silently treat candidates as confirmed
                        logger.warning(
                            f"  → Could not parse Groq verdicts for {owner}; "
                            f"treating {len(candidates)} candidate(s) as unverified"
                        )
                        result['rejected_candidates'] = []
                        result['confirmed_policies'] = []

                result['policies_found'] = result['confirmed_policies']
            else:
                logger.info(f"  → No website found for {owner}, skipping crawl")
                result['crawl_results'] = {
                    'org_url': None, 'org_name': owner,
                    'pages_crawled': 0, 'error': 'No website found',
                }

            # ── Step 5: Repo AI usage analysis ──────────────────────────────
            if self.classifier:
                logger.info(f"  → Analysing repo AI usage…")
                result['ai_usage_analysis'] = self.classifier.analyze_repository_ai_usage(
                    repo_name,
                    repo.get('description', repo_name),
                    [repo.get('language', '')],
                    repo.get('topics', []),
                )
                if result['ai_usage_analysis'].get('requires_disclosure'):
                    result['disclosure_required'] = True

            # ── Step 6: Generate Groq summary ───────────────────────────────
            if self.classifier and (result['confirmed_policies'] or result['ai_usage_analysis']):
                result['summary'] = self.classifier.summarize_disclosure_findings({
                    'owner': owner,
                    'confirmed_policies_found': len(result['confirmed_policies']),
                    'rejected_candidates': len(result['rejected_candidates']),
                    'ai_usage': result.get('ai_usage_analysis'),
                    'org_type': (result.get('org_classification') or {}).get('org_type'),
                })

            # ── Step 7: Compute transparency score from parsed verdicts ──────
            result['transparency_score'] = compute_transparency_score(result)
            result['scan_status'] = 'completed'

            self.processed_orgs[owner] = result

        except Exception as e:
            logger.error(f"Error scanning {repo_name}: {e}", exc_info=True)
            result['scan_status'] = 'error'
            result['error'] = str(e)
            result['transparency_score'] = compute_transparency_score(result)

        return result

    # ------------------------------------------------------------------
    # Scan all
    # ------------------------------------------------------------------

    def scan_all_repositories(self, limit: Optional[int] = None) -> None:
        if not self.repository_data:
            logger.warning("No repositories loaded")
            return

        repos = self.repository_data[:limit] if limit else self.repository_data
        logger.info(f"Starting scan of {len(repos)} repositories…")

        for i, repo in enumerate(repos, 1):
            try:
                result = self.scan_repository(repo)
                self.results['scan_results'].append(result)

                policy_count = len(result.get('confirmed_policies', []))
                if policy_count:
                    self.results['policies_found'] += policy_count

                logger.info(
                    f"Progress {i}/{len(repos)} — {result['repository']} "
                    f"[confirmed={policy_count}, "
                    f"rejected={len(result.get('rejected_candidates', []))}, "
                    f"score={result['transparency_score']}]"
                )

                self._save_incremental()

            except Exception as e:
                logger.error(f"Unexpected error on repo {i}: {e}", exc_info=True)

        self.results['organizations_scanned'] = len(self.processed_orgs)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_incremental(self) -> None:
        try:
            tmp = self.output_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.output_file)
        except Exception as e:
            logger.debug(f"Incremental save failed: {e}")

    def generate_summary(self) -> Dict:
        results = self.results['scan_results']
        return {
            'total_scanned': len(results),
            'with_confirmed_policies': len(
                [r for r in results if r.get('confirmed_policies')]
            ),
            'with_rejected_candidates_only': len(
                [r for r in results
                 if r.get('rejected_candidates') and not r.get('confirmed_policies')]
            ),
            'requiring_disclosure': len([r for r in results if r.get('disclosure_required')]),
            'avg_transparency_score': (
                sum(r.get('transparency_score', 0) for r in results) / len(results)
                if results else 0
            ),
            'organizations_found': self.results['organizations_scanned'],
            'total_confirmed_policies': self.results['policies_found'],
            'scan_completion_status': 'completed',
            'timestamp': datetime.now().isoformat(),
        }

    def save_results(self) -> bool:
        try:
            self.results['summary'] = self.generate_summary()
            with open(self.output_file, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            logger.info(f"Results saved to {self.output_file}")

            print("\n" + "=" * 60)
            print("AI DISCLOSURE SCAN SUMMARY")
            print("=" * 60)
            summary = self.results['summary']
            print(f"  Repos scanned:              {summary['total_scanned']}")
            print(f"  Orgs with confirmed policy:  {summary['with_confirmed_policies']}")
            print(f"  Orgs with rejected-only:     {summary['with_rejected_candidates_only']}")
            print(f"  Requiring disclosure:        {summary['requiring_disclosure']}")
            print(f"  Avg transparency score:      {summary['avg_transparency_score']:.1f}/100")
            print(f"  Total confirmed policies:    {summary['total_confirmed_policies']}")
            print("=" * 60 + "\n")
            return True
        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return False

    def run(self, limit: Optional[int] = None) -> bool:
        logger.info("Starting AI Disclosure Scanner…")
        if not self.load_repositories():
            return False
        self.scan_all_repositories(limit=limit)
        return self.save_results()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='AI Disclosure Scanner — find AI policies in GitHub organisations'
    )
    parser.add_argument('--input',  default='../sample_100.json')
    parser.add_argument('--output', default='ai_disclosure_results.json')
    parser.add_argument('--limit',  type=int, default=None)
    parser.add_argument('--groq-api-key', default=None)
    parser.add_argument('--workers', type=int, default=1,
                        help='Kept for compatibility; crawls are sequential')
    args = parser.parse_args()

    if not os.getenv('GROQ_API_KEY') and not args.groq_api_key:
        logger.error("GROQ_API_KEY not set. Export it or pass --groq-api-key.")
        return False

    scanner = AIDisclosureScanner(
        sample_file=args.input,
        output_file=args.output,
        groq_api_key=args.groq_api_key,
    )
    return scanner.run(limit=args.limit)


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
