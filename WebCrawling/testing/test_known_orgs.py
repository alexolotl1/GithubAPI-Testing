"""
Regression test for web_crawler.py against known-good AI disclosure policy pages.

Loads testing/known_policy_pages.json (real orgs with a real, currently-live
AI disclosure/AI-usage-in-contributions policy page) and checks that
crawl_organization_sync() still finds each expected URL. If a future change
to HIGH_VALUE_PHRASES / KNOWN_POLICY_PATHS / MIN_SCORE regresses recall,
this fails and tells you which org broke.

Usage:
    python testing/test_known_orgs.py            # full pipeline (probe + Scrapy)
    python testing/test_known_orgs.py --quick     # direct path probe only (fast)
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_crawler import crawl_organization_sync, probe_known_paths

FIXTURE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'known_policy_pages.json')


def _normalise_url(url: str) -> str:
    return url.rstrip('/').lower()


def _found(expected_url: str, candidates) -> bool:
    expected = _normalise_url(expected_url)
    return any(_normalise_url(c.get('url', '')) == expected for c in candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--quick', action='store_true',
        help='Only run the direct known-path probe (seconds, not minutes) - '
             'skips the Scrapy deep crawl stage.'
    )
    args = parser.parse_args()

    with open(FIXTURE_FILE, 'r', encoding='utf-8') as f:
        fixture = json.load(f)

    orgs = fixture['orgs']
    print(f"Testing {len(orgs)} known orgs "
          f"({'quick: direct probe only' if args.quick else 'full pipeline: probe + Scrapy'})...\n")

    passed, failed = 0, 0

    for org in orgs:
        name = org['org_name']
        website = org['org_website']
        expected = org['expected_url']

        print(f"--- {name} ({website}) ---")
        if args.quick:
            candidates = probe_known_paths(website)
        else:
            result = crawl_organization_sync(website, name)
            candidates = result.get('policies', [])

        if _found(expected, candidates):
            hit = next(c for c in candidates if _normalise_url(c['url']) == _normalise_url(expected))
            print(f"  PASS - found {expected} (score={hit.get('policy_score')}, "
                  f"via={hit.get('found_via')})")
            passed += 1
        else:
            print(f"  FAIL - expected {expected} not in {len(candidates)} candidate(s) found")
            for c in candidates:
                print(f"    - {c.get('url')} (score={c.get('policy_score')})")
            failed += 1
        print()

    total = passed + failed
    print("=" * 60)
    print(f"RESULT: {passed}/{total} known policy pages found")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
