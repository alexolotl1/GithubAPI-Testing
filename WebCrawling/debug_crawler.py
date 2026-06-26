#!/usr/bin/env python3
"""
Debug script to test why crawler is finding 0 pages
"""
import requests
from bs4 import BeautifulSoup
import re
from typing import Tuple, List

# Copy the scoring logic from web_crawler.py
_WHITESPACE_RE = re.compile(r'\s+')

HIGH_VALUE_PHRASES = [
    ('responsible ai',              8),
    ('responsible artificial intelligence', 8),
    ('ai ethics',                   8),
    ('ai governance',               8),
    ('ai principles',               7),
    ('ai disclosure',               9),
    ('ai transparency',             8),
    ('ai usage policy',             8),
    ('ai use policy',               8),
    ('ai acceptable use',           7),
    ('generative ai policy',        8),
    ('artificial intelligence policy', 8),
    ('artificial intelligence ethics', 8),
    ('artificial intelligence governance', 8),
    ('algorithmic transparency',    7),
    ('algorithmic accountability',  7),
    ('trustworthy ai',              7),
    ('ethical ai',                  6),
    ('ai risk management',          6),
    ('ai safety policy',            7),
    ('our approach to ai',          6),
    ('how we use ai',               6),
    ('ai and machine learning policy', 7),
    ('model card',                  5),
    ('responsible ai principles',   8),
    ('ai code of conduct',          7),
    ('ai use guidelines',           6),
    ('automated decision-making',   5),
    ('automated decision making',   5),
]

GENERIC_PROXIMITY_TERMS = [
    'disclosure', 'transparency', 'governance', 'accountability',
    'fairness', 'bias', 'explainability', 'interpretability',
    'principles', 'guidelines', 'compliance', 'risk management',
    'safety', 'oversight', 'audit', 'human review', 'human oversight',
]

AI_IDENTIFIER_TERMS = [
    'artificial intelligence', ' ai ', ' ai.', ' ai,', ' ai)', 'ai-',
    'machine learning', 'generative ai', 'large language model', ' llm',
    'neural network', 'foundation model', 'chatbot', 'genai',
]

PROXIMITY_WINDOW = 100
MIN_SCORE = 8

POLICY_URL_SIGNALS = [
    'ai-policy', 'ai_policy', 'aipolicy',
    'ai-ethics', 'ai_ethics', 'aiethics',
    'responsible-ai', 'responsible_ai', 'responsibleai',
    'ai-governance', 'ai_governance',
    'ai-principles', 'ai-guidelines', 'ai-transparency',
    'ai-disclosure', 'ai-safety', 'ai-acceptable-use',
    'generative-ai-policy', 'algorithmic-transparency',
]

KNOWN_POLICY_PATHS = [
    '/ai-policy', '/ai-ethics', '/responsible-ai', '/ai-governance',
    '/ai-principles', '/ai-guidelines', '/ai-transparency',
    '/ai-disclosure', '/ai-safety', '/ai-acceptable-use',
]


def _normalise(text: str) -> str:
    return _WHITESPACE_RE.sub(' ', text).strip().lower()


def _score_content(raw_text_lower: str, url: str) -> Tuple[int, List[str]]:
    text = _normalise(raw_text_lower)
    score = 0
    matched = []

    # High-value phrases
    for phrase, weight in HIGH_VALUE_PHRASES:
        if phrase in text:
            score += weight
            matched.append(f'{phrase}(+{weight})')

    # Generic terms
    for term in GENERIC_PROXIMITY_TERMS:
        idx = text.find(term)
        if idx == -1:
            continue
        window_start = max(0, idx - PROXIMITY_WINDOW)
        window_end = min(len(text), idx + len(term) + PROXIMITY_WINDOW)
        window = text[window_start:window_end]
        if any(ident in window for ident in AI_IDENTIFIER_TERMS):
            score += 3
            matched.append(f'{term}_near_ai(+3)')

    # URL signals
    url_lower = url.lower()
    url_hits = [s for s in POLICY_URL_SIGNALS if s in url_lower]
    if url_hits:
        score += min(len(url_hits) * 5, 10)
        matched.extend(f'url:{h}(+5)' for h in url_hits)

    return score, matched


def test_website(base_url: str, org_name: str):
    """Test if a website can be crawled"""
    print(f"\n{'='*60}")
    print(f"Testing: {org_name} ({base_url})")
    print(f"{'='*60}")
    
    # Test main domain
    try:
        print(f"\n1. Testing main domain reachability...")
        r = requests.head(base_url, timeout=5, allow_redirects=True)
        print(f"   Status: {r.status_code}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return

    # Test known paths
    print(f"\n2. Testing {len(KNOWN_POLICY_PATHS[:5])} known paths...")
    from urllib.parse import urlparse
    parsed_url = urlparse(base_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    for path in KNOWN_POLICY_PATHS[:5]:
        url = origin + path
        try:
            r = requests.head(url, timeout=5, allow_redirects=True)
            if r.status_code < 400:
                print(f"   ✓ {url} → {r.status_code}")
            else:
                print(f"   ✗ {url} → {r.status_code}")
        except Exception as e:
            print(f"   ✗ {url} → ERROR: {type(e).__name__}")

    # Test main page scoring
    print(f"\n3. Testing main page content scoring...")
    try:
        r = requests.get(base_url, timeout=5)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            text_lower = text.lower()
            score, matched = _score_content(text_lower, base_url)
            print(f"   Content length: {len(text)} chars")
            print(f"   Score: {score} (threshold: {MIN_SCORE})")
            if matched:
                print(f"   Matches: {matched[:5]}")
            else:
                print(f"   No high-value phrase matches found")
                # Show some sample text
                print(f"   First 300 chars: {text[:300]}...")
        else:
            print(f"   Failed: {r.status_code}")
    except Exception as e:
        print(f"   ERROR: {e}")


# Test the 5 organizations from the results
test_cases = [
    ("https://www.aionui.com", "iOfficeAI"),
    ("https://www.zephyrproject.org", "zephyrproject-rtos"),
    ("https://opensource.fb.com", "facebook"),
    ("https://yeasy.github.io", "yeasy"),
    ("https://www.highcharts.com", "highcharts"),
]

for url, name in test_cases:
    test_website(url, name)

print(f"\n{'='*60}\nDone.\n")
