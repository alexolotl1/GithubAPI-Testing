"""
Configuration Examples & Quick Reference

Copy-paste snippets for customizing the AI Disclosure Scanner. Every example
is a function — importing this file has no side effects.
"""

import csv
import json
import time
from datetime import datetime
from typing import Dict, List

from ai_disclosure_scanner import AIDisclosureScanner
from groq_classifier import GroqAIClassifier


# EXAMPLE 1: Basic scanning

def example_basic_scan():
    scanner = AIDisclosureScanner()
    scanner.run()


# EXAMPLE 2: Limited scan with custom output

def example_limited_scan():
    scanner = AIDisclosureScanner(
        sample_file='../HeuristicScanner/sample.json',
        output_file='results_20_repos.json',
        max_workers=5,
    )
    scanner.run(limit=20)


# EXAMPLE 3: Hardcoded org -> website map, for orgs _find_org_website()
# (ai_disclosure_scanner.py) can't resolve via GitHub fields or domain-guessing

KNOWN_ORG_WEBSITES = {
    'facebook': 'https://opensource.fb.com',
    'google': 'https://opensource.google',
    'microsoft': 'https://microsoft.com/powershell',
    'openai': 'https://openai.com',
    'anthropic': 'https://anthropic.com',
}


# EXAMPLE 4: Custom Groq analysis method

class CustomClassifier(GroqAIClassifier):
    def analyze_disclosure_requirement(self, org_name: str, industry: str) -> dict:
        """Score (0-100) how likely an org is legally required to disclose AI usage."""
        prompt = f"""
        Industry: {industry}
        Organization: {org_name}

        Score (0-100) the likelihood this org must have AI disclosure policies
        based on regulatory requirements in their industry/regions.

        Return JSON: {{"score": 0-100, "regulations": ["GDPR", "EU AI Act", ...]}}
        """
        response = self._complete(prompt, temperature=0.2, max_tokens=300)
        text = response.choices[0].message.content
        json_start = text.find('{')
        json_end = text.rfind('}') + 1
        return json.loads(text[json_start:json_end])


# EXAMPLE 5: Scrapy spider settings reference (merge into SPIDER_SCRIPT's
# custom_settings in web_crawler.py — settings aren't passed at runtime)

EXAMPLE_SCRAPY_SETTINGS = {
    'CONCURRENT_REQUESTS': 16,
    'CONCURRENT_REQUESTS_PER_DOMAIN': 4,
    'DOWNLOAD_TIMEOUT': 15,
    'RETRY_TIMES': 3,
    'RETRY_HTTP_CODES': [500, 502, 503, 504],
    'USER_AGENT': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'ROBOTSTXT_OBEY': False,
    'DOWNLOAD_DELAY': 1,
    'LOG_LEVEL': 'WARNING',
}

# EXAMPLE 6: Batch processing large repo lists

def batch_scan_repositories(repo_file: str = '../HeuristicScanner/sample.json',
                             batch_size: int = 50) -> Dict:
    """Scan a large repo list in batches, one output file per batch."""
    with open(repo_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    repos = data['repositories']
    total_batches = (len(repos) + batch_size - 1) // batch_size

    all_results = {
        'batches': [],
        'total_repos': len(repos),
        'total_policies_found': 0,
    }

    for batch_num, i in enumerate(range(0, len(repos), batch_size)):
        batch = repos[i:i + batch_size]
        batch_name = f"batch_{batch_num + 1}_of_{total_batches}"
        print(f"\n{'=' * 60}\nProcessing {batch_name}\n{'=' * 60}")

        scanner = AIDisclosureScanner(output_file=f'results_{batch_name}.json')
        scanner.repository_data = batch
        scanner.results['total_repositories'] = len(batch)
        scanner.scan_all_repositories()
        scanner.save_results()

        all_results['batches'].append({
            'batch': batch_name,
            'repos_scanned': len(batch),
            'policies_found': scanner.results['policies_found'],
            'output_file': f'results_{batch_name}.json',
        })
        all_results['total_policies_found'] += scanner.results['policies_found']

    with open('batch_summary.json', 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2)
    return all_results


# EXAMPLE 7: Filter results & print a report

def generate_report(results_file: str = 'ai_disclosure_results.json'):
    with open(results_file, 'r', encoding='utf-8') as f:
        results = json.load(f)

    summary = results.get('summary', {})
    print(f"\n{'=' * 70}\nAI DISCLOSURE SCAN REPORT\n{'=' * 70}")
    print(f"Scan date: {results.get('scan_timestamp')}")
    print(f"Repos scanned: {summary.get('total_scanned')}")
    print(f"Orgs with confirmed policy: {summary.get('with_confirmed_policies')}")
    print(f"Requiring disclosure: {summary.get('requiring_disclosure')}")
    print(f"Avg transparency score: {summary.get('avg_transparency_score', 0):.1f}/100")

    print(f"\n{'-' * 70}\nREPOS WITH CONFIRMED AI DISCLOSURE POLICIES\n{'-' * 70}")
    with_policies = [r for r in results.get('scan_results', []) if r.get('confirmed_policies')]
    if with_policies:
        for repo in with_policies:
            print(f"\n{repo['owner']}/{repo['repository']}")
            print(f"  Website: {repo.get('org_website', 'N/A')}")
            print(f"  Transparency: {repo.get('transparency_score')}/100")
            for policy in repo['confirmed_policies']:
                print(f"  - {policy.get('title', 'Policy')}: {policy.get('url')}")
    else:
        print("None found.")

    print(f"\n{'-' * 70}\nREPOS REQUIRING AI DISCLOSURE\n{'-' * 70}")
    requires_disclosure = [r for r in results.get('scan_results', []) if r.get('disclosure_required')]
    if requires_disclosure:
        for repo in requires_disclosure[:10]:
            print(f"\n{repo['owner']}/{repo['repository']} ({repo.get('language')}, {repo.get('stars')} stars)")
            ai_analysis = repo.get('ai_usage_analysis')
            if ai_analysis:
                print(f"  AI type: {ai_analysis.get('ai_type', [])}")
    else:
        print("None found.")


# EXAMPLE 8: Export results to CSV

def export_to_csv(json_file: str, csv_file: str = 'results.csv'):
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rows = [{
        'Repository': r['repository'],
        'Owner': r['owner'],
        'Owner Type': r['owner_type'],
        'Language': r['language'],
        'Stars': r['stars'],
        'Has Website': 'Yes' if r.get('org_website') else 'No',
        'Confirmed Policies': len(r.get('confirmed_policies', [])),
        'Requires Disclosure': r.get('disclosure_required', False),
        'Transparency Score': r.get('transparency_score', 0),
        'Scan Status': r.get('scan_status'),
    } for r in data['scan_results']]

    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} rows to {csv_file}")


# EXAMPLE 9: Poll a running scan's progress

def monitor_scan(results_file: str = 'ai_disclosure_results.json', interval: int = 30):
    while True:
        try:
            with open(results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)
            scanned = len(results.get('scan_results', []))
            total = results.get('total_repositories', 0)
            progress = (scanned / total * 100) if total else 0
            print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
                  f"{scanned}/{total} ({progress:.1f}%) | "
                  f"policies found: {results.get('policies_found', 0)}", end='')
            time.sleep(interval)
        except FileNotFoundError:
            print("Waiting for results file...")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


# EXAMPLE 10: Most/least transparent orgs

def analyze_compliance(results_file: str = 'ai_disclosure_results.json', top_n: int = 10):
    with open(results_file, 'r', encoding='utf-8') as f:
        results = json.load(f)

    sorted_results = sorted(
        results.get('scan_results', []),
        key=lambda x: x.get('transparency_score', 0),
        reverse=True,
    )

    print(f"\n{'=' * 70}\nTOP {top_n} MOST TRANSPARENT\n{'=' * 70}")
    for i, repo in enumerate(sorted_results[:top_n], 1):
        print(f"{i}. {repo['owner']} — {repo.get('transparency_score', 0)}/100, "
              f"{len(repo.get('confirmed_policies', []))} confirmed polic(y/ies)")

    print(f"\n{'=' * 70}\nBOTTOM {top_n} LEAST TRANSPARENT\n{'=' * 70}")
    for i, repo in enumerate(sorted_results[-top_n:], 1):
        print(f"{i}. {repo['owner']} — {repo.get('transparency_score', 0)}/100, "
              f"status={repo.get('scan_status')}")


if __name__ == '__main__':
    try:
        generate_report()
    except FileNotFoundError:
        print("Results file not found. Run the scanner first:")
        print("  python ai_disclosure_scanner.py")
