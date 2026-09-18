#!/usr/bin/env python3
"""
Debug script: probe a single org's known policy paths and print what
scored, what didn't, and why. Imports the live scoring/path logic from
web_crawler.py so it can't drift out of sync with it.

Usage:
    python debug_crawler.py <url> [org_name]
    python debug_crawler.py                      # runs the default test_cases below
"""
import sys

from web_crawler import (
    probe_known_paths, _clean_soup_for_scoring, _score_content, MIN_SCORE,
    KNOWN_POLICY_PATHS, _PROBE_HEADERS,
)
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse


def test_website(base_url: str, org_name: str):
    """Probe one org's site and report what was found, plus a diagnostic
    score of the homepage even if it's below threshold."""
    print(f"\n{'='*60}")
    print(f"Testing: {org_name} ({base_url})")
    print(f"{'='*60}")

    try:
        print(f"\n1. Testing main domain reachability...")
        r = requests.head(base_url, timeout=5, allow_redirects=True, headers=_PROBE_HEADERS)
        print(f"   Status: {r.status_code}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return

    print(f"\n2. Probing known policy paths + docs/handbook subdomains "
          f"({len(KNOWN_POLICY_PATHS)} main-domain paths)...")
    hits = probe_known_paths(base_url)
    if hits:
        for h in sorted(hits, key=lambda x: x['policy_score'], reverse=True):
            print(f"   HIT  {h['url']}  score={h['policy_score']}  "
                  f"via={h['found_via']}")
            print(f"        matched: {h['matched_signals']}")
    else:
        print("   No candidates above MIN_SCORE from direct probing.")

    print(f"\n3. Diagnostic: scoring the homepage itself (even if below threshold)...")
    try:
        r = requests.get(base_url, timeout=10, headers=_PROBE_HEADERS)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            soup = _clean_soup_for_scoring(soup)
            text = soup.get_text(separator=' ', strip=True)
            score, matched = _score_content(text.lower(), r.url)
            print(f"   Content length: {len(text)} chars")
            print(f"   Score: {score} (threshold: {MIN_SCORE})")
            if matched:
                print(f"   Matches: {matched}")
            else:
                print(f"   No matches. First 300 chars: {text[:300]}...")
        else:
            print(f"   Failed: {r.status_code}")
    except Exception as e:
        print(f"   ERROR: {e}")


DEFAULT_TEST_CASES = [
    ("https://www.aionui.com", "iOfficeAI"),
    ("https://www.zephyrproject.org", "zephyrproject-rtos"),
    ("https://opensource.fb.com", "facebook"),
    ("https://yeasy.github.io", "yeasy"),
    ("https://www.highcharts.com", "highcharts"),
]

if __name__ == '__main__':
    if len(sys.argv) > 1:
        url = sys.argv[1]
        name = sys.argv[2] if len(sys.argv) > 2 else urlparse(url).netloc
        test_website(url, name)
    else:
        for url, name in DEFAULT_TEST_CASES:
            test_website(url, name)

    print(f"\n{'='*60}\nDone.\n")
