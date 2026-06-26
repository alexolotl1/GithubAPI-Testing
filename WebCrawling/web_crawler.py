"""
Scrapy-based Web Crawler for AI Disclosure Policies

SCRAPY BASICS (for reference):
  - Spiders: define how to crawl a site
  - Requests: HTTP requests yielded by the spider
  - Responses: parsed HTML handed back to callbacks
  - CrawlerProcess: runs spiders; can only be started ONCE per Python process
    → we work around this by spawning a subprocess for each org
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
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import validators
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword / phrase config
# ---------------------------------------------------------------------------

# HIGH-PRECISION PHRASES — these almost only appear on real AI policy pages.
# Each one found is worth a lot. Matched as exact substrings on normalised
# (lowercased, whitespace-collapsed) text, so word order matters.
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
]

# GENERIC TERMS — only count if they co-occur near an AI-identifying term.
# On their own these are far too common (every legal/governance page on the
# internet says "policy", "governance", "compliance", "safety").
GENERIC_PROXIMITY_TERMS = [
    'disclosure', 'transparency', 'governance', 'accountability',
    'fairness', 'bias', 'explainability', 'interpretability',
    'principles', 'guidelines', 'compliance', 'risk management',
    'safety', 'oversight', 'audit', 'human review', 'human oversight',
]

# Terms that identify the page is actually *about* AI, used for the
# proximity check above. Deliberately narrow so we don't match "training"
# (HR training) or "model" (fashion model, business model) etc.
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
]

# Paths to probe directly before crawling — expanded with more real-world
# variants seen across enterprise/legal/trust-center site structures.
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
]

# ---------------------------------------------------------------------------
# Text normalisation + scoring
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r'\s+')


def _clean_soup_for_scoring(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Strip nav/header/footer before scoring so site-wide boilerplate links
    (e.g. a footer with 'Antitrust Policy', 'Governance Documents') don't
    pollute the body-content score. This is the single biggest fix for the
    false positives seen on sites like zephyrproject.org.
    """
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
    """
    Score a page for AI policy relevance using weighted phrase matching
    plus a proximity-gated generic-term check.

    Returns (score, matched_phrases) where matched_phrases is for debugging
    / transparency in the output JSON.

    A page must score >= MIN_SCORE (8) to be kept — high enough that a
    page needs either one strong phrase hit or several proximity-confirmed
    generic terms, not just scattered boilerplate words.
    """
    text = _normalise(raw_text_lower)
    score = 0
    matched: List[str] = []

    # 1) High-value phrases — direct substring match, heavily weighted
    for phrase, weight in HIGH_VALUE_PHRASES:
        if phrase in text:
            score += weight
            matched.append(phrase)

    # 2) Generic terms — only count if an AI identifier appears nearby
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

    # 3) URL signals — strong, deliberate signal (someone built this path on
    #    purpose), worth more than body text matches
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


# ---------------------------------------------------------------------------
# Direct path prober (no Scrapy needed — fast requests)
# ---------------------------------------------------------------------------

def probe_known_paths(base_url: str, timeout: int = 10) -> List[Dict]:
    """
    HEAD/GET each known policy path directly on the org's site.

    Highest-yield, lowest-noise step: a deliberately-named path like
    /responsible-ai is a strong signal on its own, so these get the URL
    bonus on top of content scoring.
    """
    found = []
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    session = requests.Session()
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        )
    })

    for path in KNOWN_POLICY_PATHS:
        url = origin + path
        try:
            head = session.head(url, timeout=timeout, allow_redirects=True)
            if head.status_code >= 400:
                continue

            resp = session.get(url, timeout=timeout, allow_redirects=True)
            if resp.status_code >= 400:
                continue

            soup = BeautifulSoup(resp.text, 'html.parser')
            soup = _clean_soup_for_scoring(soup)
            text = soup.get_text(separator=' ', strip=True)
            text_lower = text.lower()

            score, matched = _score_content(text_lower, resp.url)
            if score < MIN_SCORE:
                continue

            title = soup.title.string.strip() if soup.title and soup.title.string else path
            snippet = text[:1800].strip()

            page_data = {
                'url': resp.url,
                'title': title,
                'policy_score': score,
                'matched_signals': matched,
                'text_snippet': snippet,
                'key_sections': _extract_key_sections(soup),
                'links_to_policies': _extract_policy_links(soup, resp.url),
                'found_via': 'direct_probe',
            }
            found.append(page_data)
            logger.info(f"Direct probe hit: {resp.url} (score={score})")

        except requests.exceptions.Timeout:
            logger.debug(f"Timeout probing {url}")
        except requests.exceptions.ConnectionError:
            logger.debug(f"Connection error probing {url}")
        except Exception as e:
            logger.debug(f"Error probing {url}: {e}")

        time.sleep(0.25)

    return found


# ---------------------------------------------------------------------------
# Scrapy spider (runs inside a subprocess — never imported directly)
# ---------------------------------------------------------------------------
# Depth/page/timeout limits raised in this version:
#   max_depth   4  -> 5
#   max_pages   60 -> 100
#   timeout     12 -> 15s
#   links/page  15 -> 20
#   total subprocess timeout 120 -> 240s (longer scan budget per org)

SPIDER_SCRIPT = '''
import sys
import json
import re
import asyncio
import logging

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if "twisted.internet.reactor" not in sys.modules:
    from twisted.internet import asyncioreactor
    asyncioreactor.install()

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

    def start_requests(self):
        yield scrapy.Request(ORG_URL, callback=self.parse_page,
                             meta={{"depth": 0}}, errback=self.err)

    def parse_page(self, response):
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
            # Prioritise links whose anchor text/href look policy-relevant so
            # the limited per-page link budget is spent productively
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
                        callback=self.parse_page,
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
    json.dump(results, f)
'''


def _run_spider_subprocess(org_url: str, org_name: str,
                            timeout: int = 240) -> List[Dict]:
    """
    Launch the Scrapy spider in a fresh subprocess.

    Timeout raised from 120s -> 240s to allow the deeper/wider crawl
    (max_depth 5, max_pages 100) to actually complete on larger sites.
    """
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
            results = json.load(f)
        return results

    except subprocess.TimeoutExpired:
        logger.warning(f"Spider timed out for {org_url} after {timeout}s")
        return []
    except json.JSONDecodeError:
        logger.warning(f"Spider produced no valid JSON for {org_url}")
        return []
    except Exception as e:
        logger.error(f"Spider subprocess error for {org_url}: {e}")
        return []
    finally:
        for path in (script_path, output_path):
            try:
                os.unlink(path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def crawl_organization_sync(org_url: str, org_name: str = None) -> Dict:
    """
    Find AI disclosure policies on an organisation's website.

    Strategy:
      1. Probe known policy paths directly with requests (fast, precise)
      2. Run a deeper/wider Scrapy crawl in a subprocess for coverage

    Both stages use the same weighted-phrase + proximity scoring, with a
    raised minimum score (8) so generic governance/legal pages that merely
    mention "policy" or "safety" without AI context are excluded.

    Returns:
        {
            'org_url': str, 'org_name': str,
            'policies': List[Dict],   # url, title, policy_score,
                                      # matched_signals, text_snippet, …
            'error': str | None,
            'pages_crawled': int,
        }
    """
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
    spider_results = _run_spider_subprocess(org_url, org_name)
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
        'pages_crawled': len(seen_urls),
    }


def _empty_result(org_url, org_name, error):
    return {
        'org_url': org_url,
        'org_name': org_name,
        'policies': [],
        'error': error,
        'pages_crawled': 0,
    }


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.microsoft.com'
    print(f"Testing crawl on {test_url} …")
    result = crawl_organization_sync(test_url, 'test-org')
    print(json.dumps(result, indent=2))
