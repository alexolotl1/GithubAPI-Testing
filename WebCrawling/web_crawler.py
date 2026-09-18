"""
Scrapy-based Web Crawler for AI Disclosure Policies

CrawlerProcess can only start once per Python process, so each org's crawl
runs in its own subprocess (see _run_spider_subprocess).
"""

import sys
import json
import logging
import subprocess
import tempfile
import os
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import validators
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Keyword / phrase config

# High-precision phrases that almost only appear on real AI policy pages,
# matched as substrings on normalised (lowercased) text.
HIGH_VALUE_PHRASES: List[Tuple[str, int]] = [
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

    # Contribution/commit disclosure phrases — distinct from the corporate
    # governance phrases above (Assisted-by:/Generated-by: trailers, etc.).
    ('assisted-by',                 9),
    ('generated-by',                6),
    ('ai-generated content',        7),
    ('ai generated content',        7),
    ('ai-assisted contributions',   8),
    ('ai-assisted contribution',    8),
    ('generative ai tooling',       8),
    ('generative ai tool',          7),
    ('generative tooling',          6),
    ('tooling-provenance',          8),
    ('ai tools output',             6),
    ("ai tool's output",            6),
    ('policy for ai generated content', 9),
    ('ai contribution policy',      8),
    ('ai usage disclosure',         9),
    ('disclosing ai use',           8),
    ('disclose the use of ai',      8),
    ('machine-generated work',      6),
    ('computer generated work',     5),
]

# Only count if they co-occur near an AI-identifying term — too common alone.
GENERIC_PROXIMITY_TERMS = [
    'disclosure', 'transparency', 'governance', 'accountability',
    'fairness', 'bias', 'explainability', 'interpretability',
    'principles', 'guidelines', 'compliance', 'risk management',
    'safety', 'oversight', 'audit', 'human review', 'human oversight',
    'commit message', 'commit messages', 'source control', 'attribution',
    'contribution guidelines', 'contributor', 'copyrightable',
]

# Deliberately narrow so we don't match "training" (HR) or "model" (fashion).
AI_IDENTIFIER_TERMS = [
    'artificial intelligence', ' ai ', ' ai.', ' ai,', ' ai)', 'ai-',
    'machine learning', 'generative ai', 'large language model', ' llm',
    'neural network', 'foundation model', 'chatbot', 'genai',
]

PROXIMITY_WINDOW = 100  # chars on either side to search for an AI identifier

# URL/path fragments that are strong signals for a policy page
POLICY_URL_SIGNALS = [
    'ai-policy', 'ai_policy', 'aipolicy',
    'ai-ethics', 'ai_ethics', 'aiethics',
    'responsible-ai', 'responsible_ai', 'responsibleai',
    'ai-governance', 'ai_governance',
    'ai-principles', 'ai-guidelines', 'ai-transparency',
    'ai-disclosure', 'ai-safety', 'ai-acceptable-use',
    'generative-ai-policy', 'algorithmic-transparency',
    'generative-tooling', 'ai-generated', 'ai-assisted',
    'assisted-by', 'generated-by', 'ai-contribution',
]

# Paths to probe directly before crawling.
KNOWN_POLICY_PATHS = [
    '/ai-policy', '/ai-ethics', '/responsible-ai', '/ai-governance',
    '/ai-principles', '/ai-guidelines', '/ai-transparency',
    '/ai-disclosure', '/ai-safety', '/ai-acceptable-use',
    '/responsible-ai-principles', '/our-approach-to-ai',
    '/ethics', '/ethics/ai', '/about/ethics', '/about/ai-ethics',
    '/policies/ai', '/policy/ai', '/legal/ai', '/legal/ai-policy',
    '/governance', '/governance/ai', '/ai-governance-framework',
    '/trust', '/trust-center', '/trust-and-safety', '/trustcenter',
    '/compliance', '/compliance/ai',
    '/responsible-technology', '/responsible-tech',
    '/docs/responsible-ai', '/docs/ethics', '/docs/ai-policy',
    '/blog/responsible-ai', '/blog/ai-ethics', '/blog/our-approach-to-ai',
    '/about/responsible-ai', '/about/ai', '/about/ai-policy',
    '/newsroom/ai-policy', '/sustainability/ai',
    '/en/policies', '/en/ethics', '/en/ai-policy',
    '/safety', '/ai-safety-policy', '/security/ai',
    '/legal/acceptable-use', '/acceptable-use-policy',
    '/research/responsible-ai', '/company/ai-principles',

    # Contribution/commit disclosure paths.
    '/legal/generative-tooling.html', '/legal/generative-tooling',
    '/aipolicy', '/ai-policy/', '/legal/ai-policy',
    '/legal/ai-generated-content', '/legal/ai-contribution-policy',
    '/contributing/ai', '/contributing/ai-policy',
    '/docs/contributing/ai', '/community/ai-policy',
    '/policy/ai-generated-content', '/ai-contribution-policy',

    # More known-good paths on the org's MAIN domain (see
    # testing/known_policy_pages.json; DOC_SUBDOMAIN_PREFIXES below covers
    # the docs/handbook-subdomain cases).
    '/legal/generative-ai', '/docs/AIToolPolicy.html',
    '/docs/latest/contribute/ai-policy', '/contribute/ai-policy',
    '/devdocs/dev/ai_policy.html', '/doc/stable/dev/ai_policy.html',
    '/dev/ai_policy.html', '/our-policies/ai-policy/',
    '/projects/guidelines/genai/',
]

# Several real policies live on a docs/handbook subdomain rather than the
# main domain (e.g. docs.fedoraproject.org). Tried with a smaller path list
# than KNOWN_POLICY_PATHS to keep request count down.
DOC_SUBDOMAIN_PREFIXES = ['docs', 'devguide', 'handbook', 'dev', 'guides', 'make']

SUBDOMAIN_PROBE_PATHS = [
    '/en-US/council/policy/ai-contribution-policy/',
    '/council/policy/ai-contribution-policy/',
    '/guides/contribute/ai-contribution-policy/',
    '/contribute/ai-contribution-policy/',
    '/getting-started/ai-tools/',
    '/ai-tools/',
    '/ai/handbook/ai-guidelines/',
    '/handbook/ai-guidelines/',
    '/tools-and-tips/ai/',
    '/ai-policy', '/ai-contribution-policy', '/ai-guidelines',
    '/contribute/ai', '/contributing/ai',
]

# Text normalisation + scoring

_WHITESPACE_RE = re.compile(r'\s+')


def _clean_soup_for_scoring(soup: BeautifulSoup) -> BeautifulSoup:
    """Strip nav/header/footer before scoring so site-wide boilerplate links
    don't pollute the body-content score."""
    for tag_name in ('nav', 'header', 'footer'):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    # Common class/id patterns for nav/menu blocks that aren't semantic <nav>
    for selector in (
        {'class': re.compile(r'\b(nav|navbar|menu|footer|header|sidebar)\b', re.I)},
        {'id': re.compile(r'\b(nav|navbar|menu|footer|header|sidebar)\b', re.I)},
    ):
        for tag in soup.find_all(attrs=selector):
            tag.decompose()
    return soup


def _normalise(text: str) -> str:
    return _WHITESPACE_RE.sub(' ', text).strip().lower()


def _score_content(raw_text_lower: str, url: str) -> Tuple[int, List[str]]:
    """Score a page for AI policy relevance: weighted phrase matching plus a
    proximity-gated generic-term check. Returns (score, matched_phrases)."""
    text = _normalise(raw_text_lower)
    score = 0
    matched: List[str] = []

    for phrase, weight in HIGH_VALUE_PHRASES:
        if phrase in text:
            score += weight
            matched.append(phrase)

    for term in GENERIC_PROXIMITY_TERMS:
        idx = text.find(term)
        if idx == -1:
            continue
        window_start = max(0, idx - PROXIMITY_WINDOW)
        window_end = min(len(text), idx + len(term) + PROXIMITY_WINDOW)
        window = text[window_start:window_end]
        if any(ident in window for ident in AI_IDENTIFIER_TERMS):
            score += 3
            matched.append(f'{term} (near AI term)')

    # URL path signals are deliberate, worth more than body text matches
    url_lower = url.lower()
    url_hits = [s for s in POLICY_URL_SIGNALS if s in url_lower]
    if url_hits:
        score += min(len(url_hits) * 5, 10)
        matched.extend(f'url:{h}' for h in url_hits)

    return score, matched


MIN_SCORE = 8  # raised from 2 — requires real signal, not incidental mentions


def _extract_key_sections(soup: BeautifulSoup) -> List[str]:
    """Extract headings that contain genuine AI-policy terms."""
    sections = []
    strong_terms = {
        'responsible ai', 'ai ethics', 'ai governance', 'ai policy',
        'ai principles', 'ai disclosure', 'ai transparency',
        'artificial intelligence', 'algorithmic', 'trustworthy ai',
    }
    for heading in soup.find_all(['h1', 'h2', 'h3']):
        text = _normalise(heading.get_text(strip=True))
        if any(term in text for term in strong_terms):
            sections.append(heading.get_text(strip=True))
    return sections[:8]


def _extract_policy_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Extract hrefs that look like they lead to policy pages."""
    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        link_text = _normalise(a.get_text(strip=True))
        href_lower = href.lower()
        if any(s in link_text or s in href_lower for s in POLICY_URL_SIGNALS):
            full = urljoin(base_url, href)
            if full not in links:
                links.append(full)
    return links[:15]


# Direct path prober (no Scrapy needed — fast requests)

_PROBE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    )
}

PROBE_MAX_WORKERS = 20  # concurrent path probes per org


def _probe_one_path(origin: str, path: str, timeout: int) -> Optional[Dict]:
    """GET one known policy path and score it. Returns None on failure or
    sub-threshold score. Retries once on 429 (bursting many requests at one
    host can trip rate limiting even when the org overall isn't blocking)."""
    url = origin + path
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers=_PROBE_HEADERS)
        if resp.status_code == 429:
            time.sleep(1.5)
            resp = requests.get(url, timeout=timeout, allow_redirects=True,
                                headers=_PROBE_HEADERS)
        if resp.status_code >= 400:
            return None
        content_type = resp.headers.get('Content-Type', '')
        if content_type and 'html' not in content_type.lower():
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        soup = _clean_soup_for_scoring(soup)
        text = soup.get_text(separator=' ', strip=True)
        text_lower = text.lower()

        score, matched = _score_content(text_lower, resp.url)
        if score < MIN_SCORE:
            return None

        title = soup.title.string.strip() if soup.title and soup.title.string else path
        snippet = text[:1800].strip()

        logger.info(f"Direct probe hit: {resp.url} (score={score})")
        return {
            'url': resp.url,
            'title': title,
            'policy_score': score,
            'matched_signals': matched,
            'text_snippet': snippet,
            'key_sections': _extract_key_sections(soup),
            'links_to_policies': _extract_policy_links(soup, resp.url),
            'found_via': 'direct_probe',
        }

    except requests.exceptions.Timeout:
        logger.debug(f"Timeout probing {url}")
    except requests.exceptions.ConnectionError:
        logger.debug(f"Connection error probing {url}")
    except Exception as e:
        logger.debug(f"Error probing {url}: {e}")
    return None


def probe_known_paths(base_url: str, timeout: int = 10) -> List[Dict]:
    """GET each known policy path on the org's site, concurrently, plus a
    smaller path list against common docs/handbook subdomains."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    root_domain = parsed.netloc[4:] if parsed.netloc.startswith('www.') else parsed.netloc

    jobs = [(origin, path) for path in KNOWN_POLICY_PATHS]
    for prefix in DOC_SUBDOMAIN_PREFIXES:
        sub_origin = f"{parsed.scheme}://{prefix}.{root_domain}"
        if sub_origin == origin:
            continue
        jobs.extend((sub_origin, path) for path in SUBDOMAIN_PROBE_PATHS)

    found = []
    with ThreadPoolExecutor(max_workers=PROBE_MAX_WORKERS) as executor:
        futures = [
            executor.submit(_probe_one_path, o, path, timeout)
            for o, path in jobs
        ]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                found.append(result)

    return found


# Scrapy spider (runs inside a subprocess — never imported directly)

SPIDER_SCRIPT = '''
import sys
import json
import re
import logging

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.linkextractors import LinkExtractor
from scrapy.utils.log import configure_logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

logging.disable(logging.CRITICAL)

ORG_URL  = {org_url!r}
ORG_NAME = {org_name!r}
OUTPUT   = {output_path!r}

HIGH_VALUE_PHRASES = {high_value_phrases!r}
GENERIC_PROXIMITY_TERMS = {generic_terms!r}
AI_IDENTIFIER_TERMS = {ai_identifiers!r}
POLICY_URL_SIGNALS = {policy_signals!r}
PROXIMITY_WINDOW = {proximity_window!r}
MIN_SCORE = {min_score!r}

WS_RE = re.compile(r"\\s+")

def normalise(t):
    return WS_RE.sub(" ", t).strip().lower()

def clean_soup(soup):
    for tag_name in ("nav", "header", "footer"):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for selector in (
        {{"class": re.compile(r"\\b(nav|navbar|menu|footer|header|sidebar)\\b", re.I)}},
        {{"id": re.compile(r"\\b(nav|navbar|menu|footer|header|sidebar)\\b", re.I)}},
    ):
        for tag in soup.find_all(attrs=selector):
            tag.decompose()
    return soup

def score(raw_text_lower, url):
    text = normalise(raw_text_lower)
    s = 0
    matched = []
    for phrase, weight in HIGH_VALUE_PHRASES:
        if phrase in text:
            s += weight
            matched.append(phrase)
    for term in GENERIC_PROXIMITY_TERMS:
        idx = text.find(term)
        if idx == -1:
            continue
        w0 = max(0, idx - PROXIMITY_WINDOW)
        w1 = min(len(text), idx + len(term) + PROXIMITY_WINDOW)
        window = text[w0:w1]
        if any(ident in window for ident in AI_IDENTIFIER_TERMS):
            s += 3
            matched.append(term + " (near AI term)")
    ul = url.lower()
    url_hits = [x for x in POLICY_URL_SIGNALS if x in ul]
    if url_hits:
        s += min(len(url_hits) * 5, 10)
        matched.extend("url:" + h for h in url_hits)
    return s, matched

def sections(soup):
    strong = {{"responsible ai","ai ethics","ai governance","ai policy",
               "ai principles","ai disclosure","ai transparency",
               "artificial intelligence","algorithmic","trustworthy ai"}}
    out = []
    for h in soup.find_all(["h1","h2","h3"]):
        t = normalise(h.get_text(strip=True))
        if any(term in t for term in strong):
            out.append(h.get_text(strip=True))
    return out[:8]

def policy_links(soup, base):
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href","")
        lt = normalise(a.get_text(strip=True))
        if any(s in lt or s in href.lower() for s in POLICY_URL_SIGNALS):
            links.append(urljoin(base, href))
    return links[:15]

results = []
visited = set()

class PolicySpider(scrapy.Spider):
    name = "policy"
    start_urls = [ORG_URL]
    custom_settings = {{
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 10,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 6,
        "DOWNLOAD_TIMEOUT": 15,
        "RETRY_TIMES": 1,
        "LOG_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }}

    def __init__(self):
        super().__init__()
        parsed = urlparse(ORG_URL)
        self.domain = parsed.netloc
        self.origin = f"{{parsed.scheme}}://{{parsed.netloc}}"
        self.max_pages = 100
        self.max_depth = 5

    def parse(self, response):
        url = response.url
        depth = response.meta.get("depth", 0)
        if url in visited or len(visited) >= self.max_pages:
            return
        visited.add(url)

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            soup = clean_soup(soup)
            text = soup.get_text(separator=" ", strip=True)
            text_lower = text.lower()
            title = soup.title.string.strip() if soup.title and soup.title.string else url
            s, matched = score(text_lower, url)

            if s >= MIN_SCORE:
                results.append({{
                    "url": url,
                    "title": title,
                    "policy_score": s,
                    "matched_signals": matched,
                    "text_snippet": text[:1800].strip(),
                    "key_sections": sections(soup),
                    "links_to_policies": policy_links(soup, url),
                    "found_via": "scrapy_crawl",
                }})

            if depth >= self.max_depth:
                return

            extractor = LinkExtractor(
                allow_domains=[self.domain],
                deny=[r"\\.(pdf|jpg|jpeg|png|gif|exe|zip|css|js|svg|ico|mp4|webm)$"],
            )
            # Crawl policy-relevant links first — the per-page link budget is limited
            all_links = extractor.extract_links(response)
            def link_priority(link):
                lt = normalise(getattr(link, "text", "") or "")
                href = link.url.lower()
                if any(s in lt or s in href for s in POLICY_URL_SIGNALS):
                    return 0
                return 1
            all_links.sort(key=link_priority)

            for link in all_links[:20]:
                if link.url not in visited:
                    yield scrapy.Request(
                        link.url,
                        callback=self.parse,
                        meta={{"depth": depth + 1}},
                        errback=self.err,
                    )
        except Exception:
            pass

    def err(self, failure):
        pass

configure_logging({{"LOG_ENABLED": False}})
process = CrawlerProcess()
process.crawl(PolicySpider)
process.start()

with open(OUTPUT, "w") as f:
    json.dump({{"results": results, "pages_visited": len(visited)}}, f)
'''


def _run_spider_subprocess(org_url: str, org_name: str,
                            timeout: int = 240) -> Dict:
    """Launch the Scrapy spider in a fresh subprocess."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False,
                                     mode='w') as tmp:
        output_path = tmp.name

    script = SPIDER_SCRIPT.format(
        org_url=org_url,
        org_name=org_name or '',
        output_path=output_path,
        high_value_phrases=HIGH_VALUE_PHRASES,
        generic_terms=GENERIC_PROXIMITY_TERMS,
        ai_identifiers=AI_IDENTIFIER_TERMS,
        policy_signals=POLICY_URL_SIGNALS,
        proximity_window=PROXIMITY_WINDOW,
        min_score=MIN_SCORE,
    )

    with tempfile.NamedTemporaryFile(suffix='.py', delete=False,
                                     mode='w') as sf:
        sf.write(script)
        script_path = sf.name

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            logger.warning(f"Spider subprocess stderr: {proc.stderr[:500]}")

        with open(output_path, 'r') as f:
            payload = json.load(f)
        if isinstance(payload, list):
            return {'results': payload, 'pages_visited': 0}
        return {
            'results': payload.get('results', []),
            'pages_visited': payload.get('pages_visited', 0),
        }

    except subprocess.TimeoutExpired:
        logger.warning(f"Spider timed out for {org_url} after {timeout}s")
        return {'results': [], 'pages_visited': 0}
    except json.JSONDecodeError:
        logger.warning(f"Spider produced no valid JSON for {org_url}")
        return {'results': [], 'pages_visited': 0}
    except Exception as e:
        logger.error(f"Spider subprocess error for {org_url}: {e}")
        return {'results': [], 'pages_visited': 0}
    finally:
        for path in (script_path, output_path):
            try:
                os.unlink(path)
            except Exception:
                pass


# Public interface

def crawl_organization_sync(org_url: str, org_name: str = None) -> Dict:
    """Find AI disclosure policies on an org's website: direct path probe,
    then a Scrapy crawl in a subprocess, both scored the same way.

    Returns {'org_url', 'org_name', 'policies': [...], 'error', 'pages_crawled'}."""
    if not org_url:
        return _empty_result(org_url, org_name, 'No URL provided')

    org_url = org_url.strip()
    if not org_url.startswith(('http://', 'https://')):
        org_url = 'https://' + org_url

    try:
        validators.url(org_url)
    except Exception:
        return _empty_result(org_url, org_name, f'Invalid URL: {org_url}')

    logger.info(f"Crawling {org_url} for {org_name or 'unknown'}")
    all_policies: List[Dict] = []
    seen_urls: Set[str] = set()

    logger.info(f"  → Probing {len(KNOWN_POLICY_PATHS)} known policy paths...")
    direct_hits = probe_known_paths(org_url)
    for hit in direct_hits:
        if hit['url'] not in seen_urls:
            seen_urls.add(hit['url'])
            all_policies.append(hit)
    logger.info(f"  → Direct probe found {len(direct_hits)} candidate pages")

    logger.info(f"  → Starting Scrapy crawl (subprocess, depth=5, max 100 pages)...")
    spider_payload = _run_spider_subprocess(org_url, org_name)
    spider_results = spider_payload.get('results', [])
    for hit in spider_results:
        if hit['url'] not in seen_urls:
            seen_urls.add(hit['url'])
            all_policies.append(hit)
    logger.info(f"  → Scrapy found {len(spider_results)} additional candidates")

    all_policies.sort(key=lambda x: x.get('policy_score', 0), reverse=True)

    return {
        'org_url': org_url,
        'org_name': org_name,
        'policies': all_policies,
        'error': None,
        'pages_crawled': spider_payload.get('pages_visited', 0),
    }


def _empty_result(org_url, org_name, error):
    return {
        'org_url': org_url,
        'org_name': org_name,
        'policies': [],
        'error': error,
        'pages_crawled': 0,
    }


# Standalone test

if __name__ == '__main__':
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.microsoft.com'
    print(f"Testing crawl on {test_url} …")
    result = crawl_organization_sync(test_url, 'test-org')
    print(json.dumps(result, indent=2))
