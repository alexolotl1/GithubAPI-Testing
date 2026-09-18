"""
AI Disclosure Scanner — Main Orchestrator

Pipeline per repo: classify the org with Groq -> find its real website ->
crawl for policy pages (web_crawler.py) -> verify each candidate with Groq
-> compute a 0-100 transparency score. Orgs are scanned concurrently
(MAX_ORG_WORKERS at a time); repos sharing an owner reuse that owner's
crawl+verification instead of repeating it.
"""

import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set
from datetime import datetime
import requests
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, '.env'))

try:
    from groq_classifier import GroqAIClassifier
except ImportError as e:
    GroqAIClassifier = None
    GROQ_IMPORT_ERROR = e
else:
    GROQ_IMPORT_ERROR = None
from web_crawler import crawl_organization_sync

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Orgs scanned concurrently. Kept modest since each org's crawl already
# fans out its own concurrency internally (probe_known_paths uses 20 workers).
MAX_ORG_WORKERS = 5


# GitHub website finder

def _sanitize_github_token(token: Optional[str]) -> Optional[str]:
    """Treat placeholder/malformed tokens as absent — a bad token 401s
    every call, worse than sending none at all."""
    if not token or len(token.strip()) < 20:
        return None
    return token.strip()


def _find_org_website(owner: str, github_token: Optional[str] = None) -> Optional[str]:
    """Find an org's real website via the GitHub API (blog/website/homepage
    fields), falling back to common domain-guess patterns. Works for both
    orgs and individual users."""
    github_token = _sanitize_github_token(github_token)
    headers = {}
    if github_token:
        headers['Authorization'] = f'token {github_token}'

    for endpoint in (
        f'https://api.github.com/orgs/{owner}',
        f'https://api.github.com/users/{owner}',
    ):
        try:
            resp = requests.get(endpoint, headers=headers, timeout=6)
            if resp.status_code == 401 and headers:
                # Bad/expired token — retry unauthenticated rather than
                # losing this endpoint for the rest of the scan.
                logger.warning(f"GitHub token rejected (401) for {owner}; retrying unauthenticated")
                resp = requests.get(endpoint, timeout=6)
            if resp.status_code != 200:
                continue
            data = resp.json()

            for url in (data.get('blog', ''), data.get('website', ''), data.get('homepage', '')):
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
        f'https://{owner}.com', f'https://www.{owner}.com',
        f'https://{owner}.io', f'https://{owner}.org', f'https://{owner}.dev',
        f'https://{clean}.com', f'https://www.{clean}.com',
    ]
    for url in patterns:
        if _url_reachable(url):
            return url

    return None


def _url_reachable(url: str, timeout: int = 5) -> bool:
    # A bare 'Mozilla/5.0' UA gets a flat 403 from some WAFs (e.g.
    # microsoft.com) — use a realistic full UA string instead.
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        )
    }
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if r.status_code < 400:
            return True
        if r.status_code not in (403, 405):
            return False
    except Exception:
        pass

    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers=headers, stream=True)
        return r.status_code < 400
    except Exception:
        return False


# Groq verdict handling

def _get_policy_verdicts(policy_analysis: Optional[Dict]) -> List[Dict]:
    """Extract the parsed verdict list from evaluate_policy_pages()'s result.
    Empty means "unknown/unverified" (e.g. Groq call failed) — never treat
    it as "all rejected"."""
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
    """Join crawled candidates to Groq's per-URL verdicts. 'unverified' means
    no matching verdict came back — must not be counted as confirmed or rejected."""
    verdict_by_url = {v.get('url', '').rstrip('/'): v for v in verdicts if v.get('url')}

    confirmed, rejected, unverified = [], [], []

    for page in candidate_pages:
        url_key = page.get('url', '').rstrip('/')
        verdict = verdict_by_url.get(url_key)

        if verdict is None:
            # Loose match — Groq sometimes normalises trailing slashes/query strings differently.
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


def _is_invalid_api_key_error(result: Optional[Dict]) -> bool:
    if not isinstance(result, dict):
        return False
    error = str(result.get('error', '')).lower()
    return 'invalid api key' in error or 'invalid_api_key' in error


# Transparency scoring

def compute_transparency_score(result: Dict) -> int:
    """0-100 score from Groq-confirmed policies (using Groq's own per-page
    score), org classification, and risk signals."""
    score = 0
    confirmed = result.get('confirmed_policies', [])
    rejected = result.get('rejected_candidates', [])
    org_class = result.get('org_classification') or {}
    ai_analysis = result.get('ai_usage_analysis') or {}

    if result.get('org_website'):
        score += 10

    if confirmed:
        groq_scores = [
            p.get('groq_verdict', {}).get('transparency_score', 0)
            for p in confirmed
        ]
        groq_scores = [s for s in groq_scores if isinstance(s, (int, float))]
        if groq_scores:
            avg_groq_score = sum(groq_scores) / len(groq_scores)
            score += int(avg_groq_score * 0.5)  # 0-50 pts

        score += min(len(confirmed) * 5, 15)  # bonus for multiple confirmed pages, capped

    likelihood = org_class.get('likelihood_has_ai_policy', 0)
    score += int(likelihood / 10)  # 0-10 pts

    # Candidates were found and crawled but ALL rejected — policy-adjacent
    # pages exist but nothing AI-specific.
    if rejected and not confirmed:
        score -= 5

    if ai_analysis.get('risk_level') == 'high' and not confirmed:
        score -= 10

    return max(0, min(score, 100))


# Main scanner class

class AIDisclosureScanner:
    """Coordinates the full AI disclosure scanning workflow."""

    def __init__(
        self,
        sample_file: str = None,
        output_file: str = None,
        groq_api_key: Optional[str] = None,
        github_token: Optional[str] = None,
        max_workers: int = MAX_ORG_WORKERS,
    ):
        self.sample_file = sample_file or os.path.join(REPO_DIR, 'HeuristicScanner', 'sample.json')
        self.output_file = output_file or os.path.join(BASE_DIR, 'ai_disclosure_results.json')
        raw_token = github_token or os.getenv('GITHUB_TOKEN')
        self.github_token = _sanitize_github_token(raw_token)
        self.max_workers = max(1, max_workers)

        if GroqAIClassifier is None:
            logger.warning(f"Groq classifier unavailable: {GROQ_IMPORT_ERROR}")
            self.classifier = None
        else:
            try:
                self.classifier = GroqAIClassifier(api_key=groq_api_key)
            except ValueError as e:
                logger.warning(f"Groq disabled: {e}")
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
        self._lock = threading.Lock()  # guards results/processed_orgs/incremental save

        logger.info(f"AIDisclosureScanner ready. Output → {output_file}")

    # Load

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

    # Website lookup

    def get_org_website(self, repo: Dict) -> Optional[str]:
        owner = repo.get('owner', '')
        if not owner:
            return None
        with self._lock:
            if owner in self.processed_orgs:
                return self.processed_orgs[owner].get('org_website')
        website = _find_org_website(owner, self.github_token)
        logger.debug(f"Website for {owner}: {website}")
        return website

    # Scan one repository

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
            with self._lock:
                cached = self.processed_orgs.get(owner)
            if cached:
                # Repeat owner within this run — reuse crawl + Groq policy
                # analysis instead of re-crawling.
                result.update({
                    'org_website':         cached.get('org_website'),
                    'org_classification':  cached.get('org_classification'),
                    'crawl_results':       cached.get('crawl_results'),
                    'candidate_pages':     cached.get('candidate_pages', []),
                    'confirmed_policies':  cached.get('confirmed_policies', []),
                    'rejected_candidates': cached.get('rejected_candidates', []),
                    'policy_analysis':     cached.get('policy_analysis'),
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

            # Step 1: Classify organisation
            if self.classifier:
                logger.info(f"  → Classifying organisation…")
                result['org_classification'] = self.classifier.classify_organization_type(
                    owner,
                    repo.get('description', repo.get('name', '')),
                )
                if _is_invalid_api_key_error(result['org_classification']):
                    logger.warning("Groq API key is invalid; disabling Groq for the rest of this run")
                    self.classifier = None
                    result['org_classification'] = None

            # Step 2: Find website
            logger.info(f"  → Finding website…")
            result['org_website'] = self.get_org_website(repo)

            # Step 3: Crawl (direct probe + Scrapy)
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

                # Step 4: Groq verifies candidates, THEN we filter
                if self.classifier and candidates:
                    result['policy_analysis'] = self.classifier.evaluate_policy_pages(candidates)
                    verdicts = _get_policy_verdicts(result['policy_analysis'])

                    if verdicts:
                        confirmed, rejected, unverified = _filter_policies_by_verdict(
                            candidates, verdicts
                        )
                        result['confirmed_policies'] = confirmed
                        result['rejected_candidates'] = rejected
                        logger.info(
                            f"  → Groq verdict: {len(confirmed)} confirmed, "
                            f"{len(rejected)} rejected, {len(unverified)} unverified"
                        )
                    else:
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

            # Step 5: Repo AI usage analysis
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

            # Step 6: Generate Groq summary
            if self.classifier and (result['confirmed_policies'] or result['ai_usage_analysis']):
                result['summary'] = self.classifier.summarize_disclosure_findings({
                    'owner': owner,
                    'confirmed_policies_found': len(result['confirmed_policies']),
                    'rejected_candidates': len(result['rejected_candidates']),
                    'ai_usage': result.get('ai_usage_analysis'),
                    'org_type': (result.get('org_classification') or {}).get('org_type'),
                })

            # Step 7: Compute transparency score from parsed verdicts
            result['transparency_score'] = compute_transparency_score(result)
            result['scan_status'] = 'completed'

            with self._lock:
                self.processed_orgs[owner] = result

        except Exception as e:
            logger.error(f"Error scanning {repo_name}: {e}", exc_info=True)
            result['scan_status'] = 'error'
            result['error'] = str(e)
            result['transparency_score'] = compute_transparency_score(result)

        return result

    # Scan all

    def _scan_owner_group(self, repos_for_owner: List[Dict]) -> List[Dict]:
        """Scan every repo for one owner, sequentially within the group, so
        the dedup cache in scan_repository() never races with itself for
        this owner. Different owners' groups run concurrently against each
        other (see scan_all_repositories)."""
        return [self.scan_repository(repo) for repo in repos_for_owner]

    def scan_all_repositories(self, limit: Optional[int] = None) -> None:
        if not self.repository_data:
            logger.warning("No repositories loaded")
            return

        repos = self.repository_data[:limit] if limit else self.repository_data
        total = len(repos)

        by_owner: Dict[str, List[Dict]] = {}
        for repo in repos:
            by_owner.setdefault(repo.get('owner', ''), []).append(repo)

        logger.info(
            f"Starting scan of {total} repositories across {len(by_owner)} "
            f"organizations ({self.max_workers} concurrent)…"
        )

        completed = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._scan_owner_group, group): owner
                for owner, group in by_owner.items()
            }
            for future in as_completed(futures):
                owner = futures[future]
                try:
                    owner_results = future.result()
                except Exception as e:
                    logger.error(f"Unexpected error scanning owner {owner}: {e}", exc_info=True)
                    continue

                with self._lock:
                    for result in owner_results:
                        completed += 1
                        self.results['scan_results'].append(result)
                        policy_count = len(result.get('confirmed_policies', []))
                        if policy_count:
                            self.results['policies_found'] += policy_count
                        logger.info(
                            f"Progress {completed}/{total} — {result['repository']} "
                            f"[confirmed={policy_count}, "
                            f"rejected={len(result.get('rejected_candidates', []))}, "
                            f"score={result['transparency_score']}]"
                        )
                    self._save_incremental()

        self.results['organizations_scanned'] = len(self.processed_orgs)

    # Save

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


# CLI entry point

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='AI Disclosure Scanner — find AI policies in GitHub organisations'
    )
    parser.add_argument('--input',  default=None)
    parser.add_argument('--output', default=None)
    parser.add_argument('--limit',  type=int, default=None)
    parser.add_argument('--groq-api-key', default=None)
    parser.add_argument('--workers', type=int, default=MAX_ORG_WORKERS,
                        help=f'Organizations to scan concurrently (default {MAX_ORG_WORKERS})')
    args = parser.parse_args()

    if not os.getenv('GROQ_API_KEY') and not args.groq_api_key:
        logger.warning(
            "GROQ_API_KEY not set; running crawler-only scan. "
            "Candidates will not be AI-verified until a key is provided."
        )

    scanner = AIDisclosureScanner(
        sample_file=args.input,
        output_file=args.output,
        groq_api_key=args.groq_api_key,
        max_workers=args.workers,
    )
    return scanner.run(limit=args.limit)


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
