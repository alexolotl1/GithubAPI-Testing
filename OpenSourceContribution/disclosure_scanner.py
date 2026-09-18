"""
AI Disclosure Compliance Scanner

Answers: for open-source projects whose own policy says "AI/LLM use is
allowed AND you must disclose it," how often do contributors actually
disclose AI use in their commits (Co-Authored-By:/Assisted-by: trailers)?

Reads policies.json (from fetch_policies.py) for the qualifying project
list, then scans each resolved GitHub repo's recent commits for the same
AI-trailer convention HeuristicScanner/heuristic.py looks for — this is the
"do policies match practice" comparison the paper's third leg is about.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv(os.path.join(BASE_DIR, '..', 'WebCrawling', '.env'))


def _sanitize_github_token(token: str) -> str:
    """Treat placeholder/malformed tokens as absent — a bad token 401s
    every request, worse than sending none."""
    token = (token or "").strip()
    return token if len(token) >= 20 else ""


GITHUB_TOKEN = _sanitize_github_token(os.environ.get("GITHUB_TOKEN", ""))

# Same trailer convention as HeuristicScanner/heuristic.py's AI_TRAILER_RE.
AI_TRAILER_RE = re.compile(
    r'(?:co-authored-by|assisted-by|generated-by|reviewed-by)\s*:\s*'
    r'[^\n<]*?(claude|copilot|cursor(?:\s*agent)?|codex|devin|gemini|chatgpt|'
    r'gpt-\d|anthropic|openai|codewhisperer|amazon\s*q|tabnine|windsurf)',
    re.IGNORECASE,
)

COMMITS_PER_REPO = 300   # 3 pages of 100 — recent activity, not full history
MAX_WORKERS = 5


class DisclosureScanner:
    def __init__(self, policies_file: str, output_file: str):
        self.policies_file = policies_file
        self.output_file = output_file
        self.results = []
        self.request_count = 0
        self._lock = threading.Lock()
        self._exhausted = threading.Event()
        self._token_invalid = threading.Event()

    def _api_get(self, url: str, allow_token: bool = True):
        """Fast-fails once the rate limit is confirmed exhausted; falls back
        to unauthenticated once on a bad token instead of failing silently."""
        if self._exhausted.is_set():
            return None

        use_token = bool(GITHUB_TOKEN) and allow_token and not self._token_invalid.is_set()
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if use_token:
            req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")

        with self._lock:
            self.request_count += 1

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 401 and use_token:
                self._report_invalid_token()
                return self._api_get(url, allow_token=False)
            if e.code == 403:
                remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
                if remaining == "0":
                    self._report_exhausted()
                return None
            return None
        except Exception:
            return None

    def _report_exhausted(self):
        with self._lock:
            if self._exhausted.is_set():
                return
            self._exhausted.set()
            print(f"\n{'!' * 60}\nRate limit exhausted after {self.request_count} "
                  f"requests — remaining repos will be skipped.\n{'!' * 60}\n")

    def _report_invalid_token(self):
        with self._lock:
            if self._token_invalid.is_set():
                return
            self._token_invalid.set()
            print(f"\n{'!' * 60}\nGITHUB_TOKEN rejected (401) — falling back to "
                  f"unauthenticated requests.\n{'!' * 60}\n")

    def scan_repo(self, project: dict) -> dict:
        owner, repo = project['github_owner'], project['github_repo']
        full_name = f"{owner}/{repo}"

        result = {
            'project': project['project'],
            'github': full_name,
            'policy_url': project.get('policy_url'),
            'commits_scanned': 0,
            'commits_with_trailer': 0,
            'trailer_rate_pct': 0.0,
            'trailer_tools_seen': [],
            'sample_disclosed_commits': [],
            'scan_status': 'pending',
        }

        if self._exhausted.is_set():
            result['scan_status'] = 'skipped_rate_limited'
            return result

        all_messages = []
        for page in range(1, (COMMITS_PER_REPO // 100) + 1):
            if self._exhausted.is_set():
                break
            url = f"https://api.github.com/repos/{full_name}/commits?per_page=100&page={page}"
            data = self._api_get(url)
            if not data or not isinstance(data, list):
                break
            for c in data:
                sha = c.get('sha', '')[:8]
                msg = c.get('commit', {}).get('message', '')
                all_messages.append((sha, msg))
            if len(data) < 100:
                break  # last page

        if not all_messages and self._exhausted.is_set():
            result['scan_status'] = 'incomplete_rate_limited'
            return result
        if not all_messages:
            result['scan_status'] = 'no_commits_found'
            return result

        tools_seen = set()
        disclosed = 0
        samples = []
        for sha, msg in all_messages:
            matches = AI_TRAILER_RE.findall(msg)
            if matches:
                disclosed += 1
                tools_seen.update(m.lower() for m in matches)
                if len(samples) < 3:
                    trailer_line = next(
                        (line for line in msg.splitlines()
                         if AI_TRAILER_RE.search(line)), msg.splitlines()[0] if msg else ''
                    )
                    samples.append({'sha': sha, 'trailer': trailer_line.strip()})

        result.update({
            'commits_scanned': len(all_messages),
            'commits_with_trailer': disclosed,
            'trailer_rate_pct': round(disclosed / len(all_messages) * 100, 2),
            'trailer_tools_seen': sorted(tools_seen),
            'sample_disclosed_commits': samples,
            'scan_status': 'completed',
        })
        return result

    def _save_incremental(self):
        try:
            tmp = self.output_file + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._build_output(), f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.output_file)
        except Exception as e:
            print(f"  (incremental save failed: {e})")

    def _build_output(self) -> dict:
        completed = [r for r in self.results if r['scan_status'] == 'completed']
        total_commits = sum(r['commits_scanned'] for r in completed)
        total_disclosed = sum(r['commits_with_trailer'] for r in completed)
        repos_with_any = sum(1 for r in completed if r['commits_with_trailer'] > 0)

        return {
            'scan_metadata': {
                'source': 'melissawm/open-source-ai-contribution-policies',
                'filter': 'ai_llms_allowed == Yes AND disclosure_required == Yes',
                'commits_per_repo_limit': COMMITS_PER_REPO,
                'repos_attempted': len(self.results),
                'repos_completed': len(completed),
                'total_api_requests': self.request_count,
                'rate_limited': self._exhausted.is_set(),
                'token_invalid': self._token_invalid.is_set(),
            },
            'aggregate': {
                'repos_with_any_disclosed_commit': repos_with_any,
                'repos_with_any_disclosed_commit_pct': (
                    round(repos_with_any / len(completed) * 100, 2) if completed else 0
                ),
                'total_commits_scanned': total_commits,
                'total_commits_with_trailer': total_disclosed,
                'overall_trailer_rate_pct': (
                    round(total_disclosed / total_commits * 100, 2) if total_commits else 0
                ),
            },
            'repos': sorted(self.results, key=lambda r: r['trailer_rate_pct'], reverse=True),
        }

    def run(self, limit: Optional[int] = None):
        with open(self.policies_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        qualifying = [p for p in data['qualifying_projects'] if p['is_github']]
        if limit:
            qualifying = qualifying[:limit]

        print(f"Scanning {len(qualifying)} GitHub-hosted repos with "
              f"AI-allowed + disclosure-required policies "
              f"({MAX_WORKERS} concurrent, up to {COMMITS_PER_REPO} commits each)...\n")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self.scan_repo, p): p for p in qualifying}
            for future in as_completed(futures):
                p = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    print(f"  Error scanning {p.get('project')}: {e}")
                    continue
                with self._lock:
                    self.results.append(result)
                    self._save_incremental()
                status = "✓" if result['scan_status'] == 'completed' else f"[{result['scan_status']}]"
                print(f"  {status} {result['project']} ({result['github']}): "
                      f"{result['commits_with_trailer']}/{result['commits_scanned']} commits "
                      f"disclosed ({result['trailer_rate_pct']}%)")

        output = self._build_output()
        self._save_incremental()

        agg = output['aggregate']
        print(f"\n{'=' * 60}\nDISCLOSURE COMPLIANCE SUMMARY\n{'=' * 60}")
        print(f"Repos scanned:                    {output['scan_metadata']['repos_completed']}")
        print(f"Repos with >=1 disclosed commit:   {agg['repos_with_any_disclosed_commit']} "
              f"({agg['repos_with_any_disclosed_commit_pct']}%)")
        print(f"Total commits scanned:             {agg['total_commits_scanned']}")
        print(f"Total commits with AI trailer:     {agg['total_commits_with_trailer']}")
        print(f"Overall trailer rate:              {agg['overall_trailer_rate_pct']}%")
        print(f"Results saved to: {self.output_file}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=os.path.join(BASE_DIR, 'policies.json'))
    parser.add_argument('--output', default=os.path.join(BASE_DIR, 'disclosure_compliance_results.json'))
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found — run fetch_policies.py first.")
        sys.exit(1)

    scanner = DisclosureScanner(args.input, args.output)
    scanner.run(limit=args.limit)
