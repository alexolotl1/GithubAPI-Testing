"""
GitHub Repository Star Distribution Analyzer

Collects ~4000-6000 GitHub repositories using pseudo-random sampling
across different creation dates and languages, then categorizes by star count.
"""

import requests
import time
import matplotlib.pyplot as plt
from typing import Dict, List, Set
import sys
import random

GITHUB_TOKEN = "API_TOKEN_HERE"

GITHUB_API_BASE = "https://api.github.com"
SEARCH_ENDPOINT = f"{GITHUB_API_BASE}/search/repositories"
RATE_LIMIT_STATUS_ENDPOINT = f"{GITHUB_API_BASE}/rate_limit"

# Target collection
TARGET_REPOS = 5000
RESULTS_PER_PAGE = 100
MAX_RESULTS_PER_QUERY = 1000  # GitHub limits pagination to ~1000 results

# Rate limiting
BASE_REQUEST_DELAY = 1.5  # seconds between requests
SECONDARY_RATE_LIMIT_DELAY = 75  # seconds to wait for secondary rate limit

# Sampling configuration
YEARS_TO_SAMPLE = [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022]
LANGUAGES = ["Python", "JavaScript", "Java", "Go", "Rust", "C++", "TypeScript"]
GENERAL_QUERIES = ["stars:>0", "stars:>10"]


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


def generate_queries() -> List[str]:
    """
    Generate diverse search queries to simulate random sampling.
    
    Uses varied creation dates and languages to avoid biasing toward
    high-star or recently created repositories.
    """
    queries = []
    
    # Date-based queries across different years
    for year in YEARS_TO_SAMPLE:
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        queries.append(f"created:{start}..{end}")
    
    # Language-specific queries
    for lang in LANGUAGES:
        queries.append(f"language:{lang}")
    
    # General open-ended queries
    queries.extend(GENERAL_QUERIES)
    
    return queries


def validate_token() -> bool:
    """Validate that a GitHub token is set."""
    if GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE":
        print("ERROR: Please set your GitHub Personal Access Token in GITHUB_TOKEN")
        return False
    return True


def fetch_repositories(query: str, max_repos: int = 1000) -> List[Dict]:
    """
    Fetch repositories for a single query with randomized pagination.
    
    Handles rate limiting and returns parsed repository data.
    Randomizes page selection to avoid bias toward high-star repos.
    
    Args:
        query: GitHub search query string
        max_repos: Maximum repos to fetch per query (API limits to ~1000)
    
    Returns:
        List of repository dictionaries with id and stargazers_count
    """
    repos = []
    headers = create_headers()
    pages_to_fetch = (max_repos // RESULTS_PER_PAGE) + 1
    
    # Randomize page order to avoid always fetching top-ranked results
    page_order = random.sample(range(1, pages_to_fetch + 1), k=pages_to_fetch)
    
    for page in page_order:
        try:
            time.sleep(BASE_REQUEST_DELAY)
            
            params = {
                "q": query,
                "sort": "updated",  
                "per_page": RESULTS_PER_PAGE,
                "page": page
            }
            
            response = requests.get(SEARCH_ENDPOINT, headers=headers, params=params)
            
            if response.status_code == 403:
                # Secondary rate limit or quota exceeded
                print(f"    [Rate limit hit] Pausing 75 seconds...")
                time.sleep(SECONDARY_RATE_LIMIT_DELAY)
                continue
            elif response.status_code == 422:
                # Invalid query
                return repos
            elif response.status_code != 200:
                print(f"    [HTTP {response.status_code}] Stopping this query")
                return repos
            
            data = response.json()
            items = data.get("items", [])
            
            for item in items:
                repos.append({
                    "id": item.get("id"),
                    "stars": item.get("stargazers_count", 0),
                })
            
            if len(items) < RESULTS_PER_PAGE:
                break  
        
        except requests.exceptions.RequestException:
            return repos
        except (KeyError, ValueError):
            return repos
    
    return repos


def bucket_repositories(repos: List[Dict]) -> Dict[str, int]:
    """
    Categorize repositories into star ranges.
    
    Args:
        repos: List of repository dicts with 'stars' key
    
    Returns:
        Dictionary mapping star range labels to counts
    """
    buckets = {
        "0-100": 0,
        "100-500": 0,
        "500-1K": 0,
        "1K-5K": 0,
        "5K+": 0,
    }
    
    for repo in repos:
        stars = repo.get("stars", 0)
        
        if stars < 100:
            buckets["0-100"] += 1
        elif stars < 500:
            buckets["100-500"] += 1
        elif stars < 1000:
            buckets["500-1K"] += 1
        elif stars < 5000:
            buckets["1K-5K"] += 1
        else:
            buckets["5K+"] += 1
    
    return buckets


def plot_results(buckets: Dict[str, int]) -> None:
    """
    Create and save a bar chart visualization of star distribution.
    
    Args:
        buckets: Dictionary mapping star ranges to repository counts
    """
    labels = list(buckets.keys())
    counts = list(buckets.values())
    
    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(labels, counts, color='steelblue', edgecolor='navy', linewidth=1.5)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(height):,}',
                ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    ax.set_xlabel('Star Range', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of Repositories', fontsize=12, fontweight='bold')
    ax.set_title('GitHub Repository Distribution by Star Count\n(Pseudo-random sample)',
                 fontsize=14, fontweight='bold', pad=20)
    
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x):,}'))
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    ax.set_axisbelow(True)
    
    plt.tight_layout()
    plt.savefig("star_distribution.png", dpi=300, bbox_inches='tight')
    print("\n✓ Graph saved as 'star_distribution.png'")
    plt.show()


def main():
    """Main entry point for the analysis."""
    if not validate_token():
        sys.exit(1)
    
    print("\n" + "="*70)
    print("GitHub Repository Star Distribution Analyzer")
    print("="*70)

    rate_limit = check_rate_limit()
    remaining = rate_limit.get('remaining', 0)
    limit = rate_limit.get('limit', 0)
    print(f"Rate limit: {remaining}/{limit} requests available\n")
    
    if remaining < 20:
        print("⚠️  Rate limit too low. Please wait before running again.")
        sys.exit(1)

    queries = generate_queries()
    print(f"Generating queries... ({len(queries)} queries)")
    print(f"Target: ~{TARGET_REPOS} repositories\n")
    
    all_repos = []
    seen_ids: Set[int] = set()
    repos_per_query = TARGET_REPOS // len(queries)
    
    try:
        for i, query in enumerate(queries, 1):
            print(f"[{i}/{len(queries)}] Query: {query}")
            
            repos = fetch_repositories(query, max_repos=repos_per_query)
            
            for repo in repos:
                repo_id = repo.get("id")
                if repo_id not in seen_ids:
                    all_repos.append(repo)
                    seen_ids.add(repo_id)
            
            print(f"  Collected: {len(repos)} repos (unique: {len(all_repos)})")
            
            if len(all_repos) >= TARGET_REPOS:
                print(f"\nTarget reached! ({len(all_repos)} repos)")
                break
        
        print(f"\nTotal repositories collected: {len(all_repos)}")
        
        buckets = bucket_repositories(all_repos)
        
        # Print summary
        print("\n" + "="*70)
        print("Results")
        print("="*70)
        for label, count in buckets.items():
            percentage = (count / len(all_repos) * 100) if all_repos else 0
            print(f"  {label:10} {count:6,} repos ({percentage:5.1f}%)")
        print("="*70)
        
        plot_results(buckets)
        print("✓ Analysis complete!")
        
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
