"""
Configuration Examples & Quick Reference

This file contains example configurations and quick snippets
for customizing the AI Disclosure Scanner.
"""

# ==============================================================================
# EXAMPLE 1: Basic Scanning
# ==============================================================================

from ai_disclosure_scanner import AIDisclosureScanner

# Create scanner with defaults
scanner = AIDisclosureScanner()

# Run on all repos
scanner.run()


# ==============================================================================
# EXAMPLE 2: Limited Scanning with Custom Output
# ==============================================================================

# Scan only first 20 repos, save to custom output file
scanner = AIDisclosureScanner(
    sample_file='../sample_100.json',
    output_file='results_20_repos.json',
    max_workers=5
)

scanner.run(limit=20)


# ==============================================================================
# EXAMPLE 3: Custom Organization Website Finder
# ==============================================================================

def custom_org_url_finder(owner: str) -> str:
    """
    Custom logic for finding organization URLs.
    Useful for specific organization patterns.
    """
    
    # Map of known organizations to their URLs
    org_urls = {
        'facebook': 'https://www.meta.com',
        'google': 'https://google.com',
        'microsoft': 'https://microsoft.com',
        'openai': 'https://openai.com',
        'anthropic': 'https://anthropic.com',
    }
    
    # Check known orgs first
    if owner.lower() in org_urls:
        return org_urls[owner.lower()]
    
    # Try common domain patterns
    for pattern in [
        f'https://{owner}.com',
        f'https://www.{owner}.com',
        f'https://{owner}.io',
        f'https://{owner}.org',
    ]:
        # Could add URL validation here
        pass
    
    return None


# ==============================================================================
# EXAMPLE 4: Custom Groq Prompts for Analysis
# ==============================================================================

from WebCrawling.groq_classifier import GroqAIClassifier

classifier = GroqAIClassifier()

# Custom analysis prompt
custom_prompt = """
Analyze this organization for AI disclosure requirements.
Consider:
- Are they a consumer-facing company?
- Do they likely use LLMs in products?
- What regions do they operate in (GDPR, etc)?

Organization: {org_name}

Provide score 0-100 for "must_have_ai_disclosure"
"""

# You could extend GroqAIClassifier with custom methods:
class CustomClassifier(GroqAIClassifier):
    def analyze_disclosure_requirement(self, org_name: str, industry: str) -> dict:
        """Custom analysis for specific requirements."""
        prompt = f"""
        Industry: {industry}
        Organization: {org_name}
        
        Score (0-100) the likelihood this org must have AI disclosure policies
        based on regulatory requirements in their industry/regions.
        
        Return JSON: {{"score": 0-100, "regulations": ["GDPR", "EU AI Act", ...]}}
        """
        
        response = self.client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300
        )
        
        import json
        return json.loads(response.content[0].text)


# ==============================================================================
# EXAMPLE 5: Custom Scrapy Spider Settings
# ==============================================================================

scrapy_settings = {
    # Concurrency
    'CONCURRENT_REQUESTS': 16,           # Faster crawling
    'CONCURRENT_REQUESTS_PER_DOMAIN': 4,
    
    # Timeouts
    'DOWNLOAD_TIMEOUT': 15,              # Seconds
    'SPIDER_MIDDLEWARES_BASE': {},
    
    # Retries
    'RETRY_TIMES': 3,
    'RETRY_HTTP_CODES': [500, 502, 503, 504],
    
    # User agent
    'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/91.0.4472.124 Safari/537.36',
    
    # Robotics & Politeness
    'ROBOTSTXT_OBEY': False,             # Skip robots.txt
    'DOWNLOAD_DELAY': 1,                 # 1 second between requests
    
    # Logging
    'LOG_LEVEL': 'WARNING',              # Less verbose
    
    # Cookies
    'COOKIES_ENABLED': True,
    'COOKIES_DEBUG': False,
}

# Use in web_crawler.py:
# runner = CrawlerRunner(settings=scrapy_settings)


# ==============================================================================
# EXAMPLE 6: Batch Processing Large Repo Lists
# ==============================================================================

from WebCrawling.ai_disclosure_scanner import AIDisclosureScanner
import json

def batch_scan_repositories(repo_file: str = '../sample_100.json', batch_size: int = 20):
    """
    Process large repository lists in batches.
    Useful for scanning 1000+ repos.
    """
    
    with open(repo_file, 'r') as f:
        data = json.load(f)
    
    repos = data['repositories']
    total_batches = (len(repos) + batch_size - 1) // batch_size
    
    all_results = {
        'scan_timestamp': data.get('scan_timestamp', 'unknown'),
        'batches': [],
        'total_repos': len(repos),
        'total_policies_found': 0,
    }
    
    for batch_num, i in enumerate(range(0, len(repos), batch_size)):
        batch = repos[i:i+batch_size]
        batch_name = f"batch_{batch_num + 1}_of_{total_batches}"
        
        print(f"\n{'='*60}")
        print(f"Processing {batch_name}")
        print(f"{'='*60}")
        
        scanner = AIDisclosureScanner(
            output_file=f'results_{batch_name}.json',
            max_workers=3  # Conservative for large runs
        )
        
        # Temporarily override repo data
        scanner.repository_data = batch
        scanner.results['total_repositories'] = len(batch)
        
        scanner.scan_all_repositories()
        scanner.save_results()
        
        batch_results = {
            'batch': batch_name,
            'repos_scanned': len(batch),
            'policies_found': scanner.results['policies_found'],
            'output_file': f'results_{batch_name}.json'
        }
        
        all_results['batches'].append(batch_results)
        all_results['total_policies_found'] += scanner.results['policies_found']
    
    # Save batch summary
    with open('batch_summary.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    
    return all_results


# ==============================================================================
# EXAMPLE 7: Filter Results & Generate Report
# ==============================================================================

import json
from typing import List, Dict

def generate_report(results_file: str = 'ai_disclosure_results.json'):
    """Generate human-readable report from scan results."""
    
    with open(results_file, 'r') as f:
        results = json.load(f)
    
    print("\n" + "="*70)
    print("AI DISCLOSURE SCAN REPORT")
    print("="*70)
    
    summary = results.get('summary', {})
    
    print(f"\nScan Date: {results.get('scan_timestamp')}")
    print(f"Total Repositories Scanned: {summary.get('total_scanned')}")
    print(f"Organizations Found: {summary.get('organizations_found')}")
    print(f"With AI Policies: {summary.get('with_policies')}")
    print(f"Requiring Disclosure: {summary.get('requiring_disclosure')}")
    print(f"Average Transparency Score: {summary.get('avg_transparency_score'):.1f}/100")
    
    # Find repos with policies
    print("\n" + "-"*70)
    print("REPOSITORIES WITH AI DISCLOSURE POLICIES")
    print("-"*70)
    
    repos_with_policies = [
        r for r in results.get('scan_results', [])
        if r.get('policies_found')
    ]
    
    if repos_with_policies:
        for repo in repos_with_policies:
            print(f"\n{repo['owner']}/{repo['repository']}")
            print(f"  Website: {repo.get('org_website', 'N/A')}")
            print(f"  Transparency: {repo.get('transparency_score')}/100")
            for policy in repo['policies_found']:
                print(f"  - {policy.get('title', 'Policy')}: {policy.get('url')}")
    else:
        print("No repositories with AI policies found.")
    
    # Find repos requiring disclosure
    print("\n" + "-"*70)
    print("REPOSITORIES REQUIRING AI DISCLOSURE")
    print("-"*70)
    
    requires_disclosure = [
        r for r in results.get('scan_results', [])
        if r.get('disclosure_required')
    ]
    
    if requires_disclosure:
        for repo in requires_disclosure[:10]:
            print(f"\n{repo['owner']}/{repo['repository']}")
            print(f"  Language: {repo.get('language')}")
            print(f"  Stars: {repo.get('stars')}")
            if repo.get('ai_usage_analysis'):
                ai_analysis = repo['ai_usage_analysis']
                print(f"  AI Type: {ai_analysis.get('ai_type', [])}")
    else:
        print("No repositories identified as requiring disclosure.")
    
    print("\n" + "="*70)


# ==============================================================================
# EXAMPLE 8: Export Results to Different Formats
# ==============================================================================

import json
import csv

def export_to_csv(json_file: str, csv_file: str = 'results.csv'):
    """Convert JSON results to CSV for spreadsheet analysis."""
    
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    rows = []
    for result in data['scan_results']:
        rows.append({
            'Repository': result['repository'],
            'Owner': result['owner'],
            'Owner Type': result['owner_type'],
            'Language': result['language'],
            'Stars': result['stars'],
            'Has Website': 'Yes' if result.get('org_website') else 'No',
            'Has Policies': len(result.get('policies_found', [])) > 0,
            'Requires Disclosure': result.get('disclosure_required', False),
            'Transparency Score': result.get('transparency_score', 0),
            'Scan Status': result.get('scan_status'),
        })
    
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    
        print(f"Results exported to {csv_file}")


# ==============================================================================
# EXAMPLE 9: Monitor Scan Progress
# ==============================================================================

import time
from datetime import datetime

def monitor_scan(results_file: str = 'ai_disclosure_results.json', 
                 interval: int = 30):
    """Monitor scan progress in real-time."""
    
    while True:
        try:
            with open(results_file, 'r') as f:
                results = json.load(f)
            
            scanned = len(results.get('scan_results', []))
            total = results.get('total_repositories', 0)
            policies = results.get('policies_found', 0)
            
            progress = (scanned / total * 100) if total > 0 else 0
            
            print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
                  f"Progress: {scanned}/{total} ({progress:.1f}%) | "
                  f"Policies Found: {policies}", end='')
            
            time.sleep(interval)
        
        except FileNotFoundError:
            print("Waiting for results file...")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            break


# ==============================================================================
# EXAMPLE 10: Custom Analysis - Find Most Compliant Organizations
# ==============================================================================

def analyze_compliance(results_file: str = 'ai_disclosure_results.json',
                      top_n: int = 10):
    """Find most and least transparent organizations."""
    
    with open(results_file, 'r') as f:
        results = json.load(f)
    
    scan_results = results.get('scan_results', [])
    
    # Sort by transparency score
    sorted_results = sorted(
        scan_results,
        key=lambda x: x.get('transparency_score', 0),
        reverse=True
    )
    
    print("\n" + "="*70)
    print(f"TOP {top_n} MOST TRANSPARENT ORGANIZATIONS")
    print("="*70)
    
    for i, repo in enumerate(sorted_results[:top_n], 1):
        print(f"\n{i}. {repo['owner']} ({repo['organization_name']})")
        print(f"   Transparency Score: {repo.get('transparency_score', 0)}/100")
        print(f"   Policies Found: {len(repo.get('policies_found', []))}")
        if repo.get('policies_found'):
            for policy in repo['policies_found'][:3]:
                print(f"   - {policy.get('title', 'Policy')}")
    
    print("\n" + "="*70)
    print(f"TOP {top_n} LEAST TRANSPARENT ORGANIZATIONS")
    print("="*70)
    
    for i, repo in enumerate(sorted_results[-top_n:], 1):
        print(f"\n{i}. {repo['owner']}")
        print(f"   Transparency Score: {repo.get('transparency_score', 0)}/100")
        print(f"   Status: {repo.get('scan_status')}")


if __name__ == '__main__':
    # Example: Generate report from existing results
    try:
        generate_report()
    except FileNotFoundError:
        print("Results file not found. Run scanner first:")
        print("  python ai_disclosure_scanner.py")
