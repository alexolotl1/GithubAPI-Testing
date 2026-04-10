"""
GitHub Repository Star Distribution Analyzer

Analyzes the distribution of GitHub repositories across star ranges
using the GitHub REST API and generates a visualization with matplotlib.
"""

import requests
import time
import matplotlib.pyplot as plt
from typing import Dict
import sys

GITHUB_TOKEN = "YOUR_GITHUB_TOKEN_HERE"

GITHUB_API_BASE = "https://api.github.com"
SEARCH_ENDPOINT = f"{GITHUB_API_BASE}/search/repositories"
RATE_LIMIT_STATUS_ENDPOINT = f"{GITHUB_API_BASE}/rate_limit"

STAR_RANGES = [
    "stars:0..100",
    "stars:100..500",
    "stars:500..1000",
    "stars:1000..5000",
    "stars:>=5000"
]

MAX_PAGES_PER_RANGE = 10
RESULTS_PER_PAGE = 100
RATE_LIMIT_DELAY = 5


def create_headers() -> Dict[str, str]:
    """Create request headers with GitHub authentication."""
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }


def check_rate_limit() -> Dict:
    """Check current GitHub API rate limit status."""
    headers = create_headers()
    try:
        response = requests.get(RATE_LIMIT_STATUS_ENDPOINT, headers=headers)
        if response.status_code == 200:
            data = response.json()
            search_limit = data.get("resources", {}).get("search", {})
            return {
                "limit": search_limit.get("limit", 0),
                "remaining": search_limit.get("remaining", 0),
                "reset": search_limit.get("reset", 0)
            }
    except:
        pass
    return {"limit": 0, "remaining": 0, "reset": 0}


def fetch_repositories_for_range(star_range: str) -> int:
    """
    Fetch repositories for a specific star range.
    
    Queries the GitHub Search API with pagination, respecting rate limits.
    
    Args:
        star_range: Query string for star range (e.g., "stars:0..100")
        
    Returns:
        Total count of repositories retrieved for this range
    """
    total_count = 0
    headers = create_headers()
    
    for page in range(1, MAX_PAGES_PER_RANGE + 1):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            
            params = {
                "q": star_range,
                "sort": "stars",
                "order": "desc",
                "per_page": RESULTS_PER_PAGE,
                "page": page
            }
            
            response = requests.get(SEARCH_ENDPOINT, headers=headers, params=params)
            rate_limit_remaining = response.headers.get("X-RateLimit-Remaining", "?")
            
            if response.status_code == 403:
                try:
                    error_data = response.json()
                    message = error_data.get("message", "Rate limit exceeded")
                    print(f"  ⚠️  {message}")
                except:
                    print(f"  ⚠️  Rate limit exceeded on page {page}")
                break
            elif response.status_code == 422:
                print(f"  ⚠️  Invalid query on page {page}")
                break
            elif response.status_code != 200:
                print(f"  ⚠️  HTTP Error {response.status_code}")
                break
            
            data = response.json()
            items = data.get("items", [])
            total_count += len(items)
            
            if len(items) < RESULTS_PER_PAGE:
                print(f"  ✓ Page {page}: {len(items)} repos (end of results)")
                break
            else:
                print(f"  ✓ Page {page}: {len(items)} repos (remaining: {rate_limit_remaining})")
                
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Network error: {str(e)}")
            break
        except (KeyError, ValueError) as e:
            print(f"  ✗ Parse error: {str(e)}")
            break
    
    return total_count


def validate_token() -> bool:
    """Validate that a GitHub token is set."""
    if GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE":
        print("ERROR: Please set your GitHub Personal Access Token in GITHUB_TOKEN")
        print("Create one at: https://github.com/settings/tokens")
        return False
    return True


def analyze_star_distribution() -> Dict[str, int]:
    """Query each star range and count repositories."""
    star_counts = {}
    
    print("\n" + "="*70)
    print("GitHub Repository Star Distribution Analysis")
    print("="*70 + "\n")
    
    for i, star_range in enumerate(STAR_RANGES, 1):
        print(f"[{i}/{len(STAR_RANGES)}] Querying range: {star_range}")
        
        try:
            count = fetch_repositories_for_range(star_range)
            star_counts[star_range] = count
            print(f"  Total for range: {count} repositories\n")
        except Exception as e:
            print(f"  ✗ Unexpected error: {str(e)}\n")
            star_counts[star_range] = 0
    
    return star_counts


def create_visualization(star_counts: Dict[str, int]) -> None:
    """Create and save a bar chart visualization of star distribution."""
    counts = list(star_counts.values())
    
    formatted_labels = [
        "0-100",
        "100-500",
        "500-1K",
        "1K-5K",
        "5K+"
    ]
    
    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(formatted_labels, counts, color='steelblue', edgecolor='navy', linewidth=1.5)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(height):,}',
                ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    ax.set_xlabel('Star Range', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of Repositories', fontsize=12, fontweight='bold')
    ax.set_title('GitHub Repository Distribution by Star Count\n(Sampled up to 1000 repos per range)',
                 fontsize=14, fontweight='bold', pad=20)
    
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x):,}'))
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    ax.set_axisbelow(True)
    
    plt.tight_layout()
    plt.savefig("star_distribution.png", dpi=300, bbox_inches='tight')
    print(f"✓ Graph saved as 'star_distribution.png'")
    plt.show()


def print_summary(star_counts: Dict[str, int]) -> None:
    """Print a summary table of results."""
    total_repos = sum(star_counts.values())
    
    print("\n" + "="*70)
    print("Summary Results")
    print("="*70)
    print(f"\nTotal repositories sampled: {total_repos:,}\n")
    
    for star_range, count in star_counts.items():
        percentage = (count / total_repos * 100) if total_repos > 0 else 0
        print(f"  {star_range:20} {count:6,} repos ({percentage:5.1f}%)")
    
    print("\n" + "="*70 + "\n")


def main():
    """Main entry point."""
    if not validate_token():
        sys.exit(1)
    
    print("\n" + "="*70)
    print("GitHub API Rate Limit Status")
    print("="*70)
    rate_limit = check_rate_limit()
    remaining = rate_limit.get('remaining', 0)
    limit = rate_limit.get('limit', 0)
    
    print(f"Search API: {remaining}/{limit} requests remaining")
    print("="*70)
    
    if remaining < 10:
        print("⚠️  Rate limit is very low. Please wait before running again.")
        sys.exit(1)
    
    try:
        star_counts = analyze_star_distribution()
        print_summary(star_counts)
        create_visualization(star_counts)
        print("✓ Analysis complete!")
        
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
