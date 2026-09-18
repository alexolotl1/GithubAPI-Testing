"""
GitHub Repository Star Distribution Analyzer & Exporter

Collects GitHub repositories by star count ranges and exports them to JSON files.
Includes detailed repo information: names, links, stars, and commit counts.
"""

import requests
import time
import json
import os
from typing import Dict, List, Set
import sys
import random
from pathlib import Path
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent

# Shared secrets live in WebCrawling/.env (single .env for the whole repo,
# gitignored) rather than pasted directly into source here.
load_dotenv(REPO_DIR / 'WebCrawling' / '.env')

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

GITHUB_API_BASE = "https://api.github.com"
SEARCH_ENDPOINT = f"{GITHUB_API_BASE}/search/repositories"
RATE_LIMIT_STATUS_ENDPOINT = f"{GITHUB_API_BASE}/rate_limit"
GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
CHECKPOINT_FILE = BASE_DIR / "checkpoint.json"

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
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }


def check_rate_limit() -> Dict:
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
    """Varied creation dates + languages, to avoid biasing toward high-star
    or recently created repositories."""
    queries = []
    
    for year in YEARS_TO_SAMPLE:
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        queries.append(f"created:{start}..{end}")

    for lang in LANGUAGES:
        queries.append(f"language:{lang}")
  
    queries.extend(GENERAL_QUERIES)
    
    return queries


def validate_token() -> bool:
    if not GITHUB_TOKEN:
        print("ERROR: Set GITHUB_TOKEN in WebCrawling/.env (see WebCrawling/.env.example)")
        return False
    return True


def save_checkpoint(repos: List[Dict], seen_ids: Set[int]) -> None:
    """Save progress checkpoint to resume after interruption."""
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump({"repos": repos, "seen_ids": list(seen_ids)}, f)


def load_checkpoint():
    """Load progress checkpoint if it exists."""
    if Path(CHECKPOINT_FILE).exists():
        try:
            with open(CHECKPOINT_FILE) as f:
                data = json.load(f)
                return data.get("repos", []), set(data.get("seen_ids", []))
        except:
            pass
    return [], set()


def fetch_repos_graphql(repo_list: List[Dict]) -> List[Dict]:
    """Batch fetch repo details via GraphQL (up to 100 per request) —
    much faster than individual REST calls."""
    if not repo_list:
        return []

    try:
        aliases = []
        for i, repo in enumerate(repo_list):
            owner = repo.get("owner", "")
            name = repo.get("name", "")
            aliases.append(f"""
            repo{i}: repository(owner: \"{owner}\", name: \"{name}\") {{
                nameWithOwner
                url
                stargazerCount
                forkCount
                primaryLanguage {{ name }}
                createdAt
            }}
            """)
        
        query = "{ " + "\n".join(aliases) + " }"
        
        response = requests.post(
            GRAPHQL_ENDPOINT,
            json={"query": query},
            headers=create_headers()
        )
        time.sleep(0.5)  # Rate limiting
        
        data = response.json().get("data", {})
        results = []
        
        for i in range(len(repo_list)):
            r = data.get(f"repo{i}")
            if r:
                results.append({
                    "name": r.get("nameWithOwner", "unknown"),
                    "owner": repo_list[i].get("owner", "unknown"),
                    "owner_type": repo_list[i].get("owner_type", "User"),
                    "url": r.get("url", ""),
                    "stars": r.get("stargazerCount", 0),
                    "forks": r.get("forkCount", 0),
                    "language": r.get("primaryLanguage", {}).get("name") if r.get("primaryLanguage") else None,
                    "created_at": r.get("createdAt", "")
                })
        
        return results
    except Exception as e:
        print(f"    GraphQL error: {str(e)}")
        return []


def fetch_repositories(query: str, max_repos: int = 100) -> List[Dict]:
    """Randomizes page selection to avoid bias toward high-star repos."""
    repos = []
    headers = create_headers()
    pages_to_fetch = (max_repos // RESULTS_PER_PAGE) + 1
    
    # Randomize page order to avoid bias toward high-star repos
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
                repos.append(item)
            
            if len(items) < RESULTS_PER_PAGE:
                break  
        
        except requests.exceptions.RequestException:
            return repos
        except (KeyError, ValueError):
            return repos
    
    return repos


def bucket_repositories(repos: List[Dict]) -> Dict[str, int]:
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


def fetch_repo_details(repo_data: Dict) -> Dict:
    """Extract owner/name from a search API result for GraphQL batching."""
    try:
        owner_obj = repo_data.get("owner", {})
        owner = owner_obj.get("login", "unknown")
        owner_type = owner_obj.get("type", "User")  # "User" or "Organization"
        
        return {
            "owner": owner,
            "owner_type": owner_type,
            "name": repo_data.get("name", "unknown")
        }
    except:
        return None


def export_to_json_files(repos: List[Dict]) -> None:
    """Writes star_maps/*.json (5 files by star range), merging with
    existing data instead of overwriting."""
    output_dir = BASE_DIR / "star_maps"
    output_dir.mkdir(exist_ok=True)
    
    # Define star categories (use None instead of float('inf') for JSON serialization)
    buckets = {
        "stars_0_100":   (0,    100,  "0-100"),
        "stars_100_500": (100,  500,  "100-500"),
        "stars_500_1k":  (500,  1000, "500-1K"),
        "stars_1k_5k":   (1000, 5000, "1K-5K"),
        "stars_5k_plus": (5000, None, "5K+"),
    }
    
    # Organize new repos by star count
    sorted_buckets = {key: [] for key in buckets}
    for repo in repos:
        stars = repo.get("stars", 0)
        for key, (low, high, _) in buckets.items():
            if high is None and stars >= low:
                sorted_buckets[key].append(repo)
                break
            elif high is not None and low <= stars < high:
                sorted_buckets[key].append(repo)
                break
    
    print("\n" + "="*60)
    print("Exporting to JSON Files")
    print("="*60)
    
    for key, (low, high, label) in buckets.items():
        file_path = output_dir / f"{key}.json"
        new_repos = sorted_buckets[key]
        
        existing_repos = []
        if file_path.exists():
            try:
                with open(file_path) as f:
                    data = json.load(f)
                    existing_repos = data.get("repositories", [])
            except:
                pass

        existing_names = {r["name"] for r in existing_repos}
        merged = existing_repos + [r for r in new_repos if r["name"] not in existing_names]

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({
                "category": label,
                "count": len(merged),
                "repositories": merged
            }, f, indent=2, ensure_ascii=False)
        
        print(f"  {key}.json — {len(merged):5} repos ({label:6} stars, +{len(new_repos):4} new)")
    
    print("="*60)
    print(f"✓ Saved to '{output_dir}/'")
    print("="*60)


def main():
    if not validate_token():
        sys.exit(1)
    
    print("\n" + "="*70)
    print("GitHub Repository Star Maps Exporter (Optimized)")
    print("="*70)

    rate_limit = check_rate_limit()
    remaining = rate_limit.get('remaining', 0)
    limit = rate_limit.get('limit', 0)
    print(f"Rate limit: {remaining}/{limit} requests available\n")
    
    if remaining < 20:
        print("⚠️  Rate limit too low. Please wait before running again.")
        sys.exit(1)

    all_repos, seen_ids = load_checkpoint()
    if all_repos:
        print(f"✓ Resuming from checkpoint: {len(all_repos)} repos already collected\n")

    queries = generate_queries()
    print(f"Generating queries... ({len(queries)} queries)")
    print(f"Target: ~{TARGET_REPOS} repositories\n")
    
    repos_per_query = TARGET_REPOS // len(queries)
    
    try:
        for i, query in enumerate(queries, 1):
            print(f"[{i}/{len(queries)}] Query: {query}")
            
            raw_repos = fetch_repositories(query, max_repos=repos_per_query)

            repos_for_graphql = []
            for repo in raw_repos:
                repo_id = repo.get("id")
                if repo_id not in seen_ids:
                    basic_info = fetch_repo_details(repo)
                    if basic_info:
                        repos_for_graphql.append(basic_info)
                        seen_ids.add(repo_id)

            for batch_start in range(0, len(repos_for_graphql), 100):
                batch = repos_for_graphql[batch_start:batch_start + 100]
                enriched_batch = fetch_repos_graphql(batch)
                all_repos.extend(enriched_batch)

            print(f"  Collected: {len(raw_repos)} repos (unique: {len(all_repos)})")
            save_checkpoint(all_repos, seen_ids)

            if len(all_repos) >= TARGET_REPOS:
                print(f"\nTarget reached! ({len(all_repos)} repos)")
                break

        print(f"\nTotal repositories collected: {len(all_repos)}")
        export_to_json_files(all_repos)

        if Path(CHECKPOINT_FILE).exists():
            Path(CHECKPOINT_FILE).unlink()
        
        print("\n✓ Export complete!")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted - checkpoint saved. Run again to resume.")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"\n⚠️  Checkpoint saved at {CHECKPOINT_FILE} - run again to resume")
        sys.exit(1)


if __name__ == "__main__":
    main()
