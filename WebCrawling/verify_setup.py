"""
Setup Verification Script

Validates that all dependencies are installed and APIs are configured correctly.
Run this before running the full scanner to catch setup issues early.

Usage:
    python verify_setup.py
"""

import sys
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / '.env')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text: str):
    print(f"\n{BLUE}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{RESET}")

def print_ok(text: str):
    print(f"{GREEN}✓{RESET} {text}")

def print_error(text: str):
    print(f"{RED}✗{RESET} {text}")

def print_warning(text: str):
    print(f"{YELLOW}⚠{RESET} {text}")

def verify_python_version() -> bool:
    print_header("1. Python Version Check")
    
    version = sys.version_info
    required = (3, 8)
    
    print(f"Python: {version.major}.{version.minor}.{version.micro}")
    
    if (version.major, version.minor) >= required:
        print_ok(f"Python version {version.major}.{version.minor} meets requirement (3.8+)")
        return True
    else:
        print_error(f"Python {version.major}.{version.minor} is too old (need 3.8+)")
        return False

def verify_dependencies() -> bool:
    print_header("2. Python Dependencies Check")
    
    required_packages = [
        ('requests', 'HTTP requests'),
        ('groq', 'Groq AI API'),
        ('scrapy', 'Web crawling'),
        ('bs4', 'HTML parsing'),
        ('dotenv', 'Environment variables'),
        ('validators', 'URL validation'),
        ('aiohttp', 'Async HTTP'),
    ]
    
    all_ok = True
    for package, description in required_packages:
        try:
            __import__(package)
            print_ok(f"{package:<15} - {description}")
        except ImportError:
            print_error(f"{package:<15} - NOT INSTALLED")
            all_ok = False
    
    if not all_ok:
        print_warning("\nInstall missing packages with:")
        print("  pip install -r ../requirements.txt")
    
    return all_ok

def verify_groq_api_key() -> bool:
    print_header("3. Groq API Configuration")
    
    api_key = os.getenv('GROQ_API_KEY')
    
    if api_key:
        masked = api_key[:10] + '...' + api_key[-5:]
        print_ok(f"GROQ_API_KEY found: {masked}")

        try:
            from groq import Groq
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=10,
                temperature=0.1
            )
            
            print_ok("Groq API connection successful")
            return True
        
        except Exception as e:
            print_error(f"Groq API connection failed: {e}")
            return False
    else:
        print_error("GROQ_API_KEY not found")
        print_warning("Crawler-only mode can still run, but Groq verification will be skipped")
        print_warning("PowerShell: $env:GROQ_API_KEY='your-key-here'")
        print_warning("Or create WebCrawling\\.env with: GROQ_API_KEY=your-key-here")
        return None

def verify_github_api_key() -> bool:
    print_header("4. GitHub API Configuration (Optional)")
    
    token = os.getenv('GITHUB_TOKEN')
    
    if token:
        masked = token[:10] + '...' + token[-5:]
        print_ok(f"GITHUB_TOKEN found: {masked}")

        try:
            import requests
            headers = {'Authorization': f'token {token}'}
            response = requests.get(
                'https://api.github.com/user',
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                print_ok("GitHub API connection successful")
                return True
            else:
                print_warning(f"GitHub API returned status {response.status_code} "
                              f"(token may be invalid/expired) — optional, scanner still works")
                return None  # optional check: broken token shouldn't block a "ready to run" verdict

        except Exception as e:
            print_warning(f"GitHub API connection failed: {e} — optional, scanner still works")
            return None
    else:
        print_warning("GITHUB_TOKEN not set (optional - API calls will be slower)")
        return None  # Optional

def verify_input_files() -> bool:
    print_header("5. Input Files Check")
    
    required_files = {
        REPO_DIR / 'HeuristicScanner' / 'sample.json': 'Repository list',
        REPO_DIR / 'requirements.txt': 'Dependencies list',
    }

    all_ok = True
    for path, description in required_files.items():
        display = os.path.relpath(path, BASE_DIR)
        if path.exists():
            size = path.stat().st_size
            print_ok(f"{display:<20} ({size:,} bytes) - {description}")
        else:
            print_error(f"{display:<20} - NOT FOUND")
            all_ok = False

    sample_file = REPO_DIR / 'HeuristicScanner' / 'sample.json'
    if sample_file.exists():
        try:
            with open(sample_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            repo_count = len(data.get('repositories', []))
            print_ok(f"Contains {repo_count} repositories")
        except json.JSONDecodeError:
            print_error("../HeuristicScanner/sample.json is invalid JSON")
            all_ok = False
    
    return all_ok

def verify_output_directory() -> bool:
    print_header("6. Output Directory Check")
    
    try:
        test_file = '.write_test_temp.txt'
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        
        print_ok("Current directory is writable")
        print_ok("Results will be saved to: ai_disclosure_results.json")
        return True
    
    except Exception as e:
        print_error(f"Cannot write to directory: {e}")
        return False

def verify_module_imports() -> bool:
    print_header("7. Custom Modules Check")
    
    modules = [
        ('ai_disclosure_scanner', 'Main scanner'),
        ('web_crawler', 'Scrapy spider'),
        ('groq_classifier', 'AI classifier'),
    ]
    
    all_ok = True
    for module_name, description in modules:
        try:
            __import__(module_name)
            print_ok(f"{module_name:<25} - {description}")
        except ImportError as e:
            print_error(f"{module_name:<25} - {str(e)}")
            all_ok = False
    
    return all_ok

def print_summary(results: dict) -> None:
    print_header("Verification Summary")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v is True)
    warned = sum(1 for v in results.values() if v is None)
    
    print(f"\nResults: {passed}/{total} checks passed")
    
    if warned:
        print(f"         {warned} optional checks skipped")
    
    if passed == total:
        print_ok("\n✅ All required checks passed!")
        print("\nYou're ready to run:")
        print("  python ai_disclosure_scanner.py --limit 5    # Test (quick)")
        print("  python ai_disclosure_scanner.py              # Full scan")
    elif passed == (total - warned):
        print_warning("\n⚠️ All required checks passed, but some optional checks failed")
        print("You can still run the scanner, but some features may be limited.")
    else:
        print_error("\n❌ Some required checks failed")
        print("Please fix the issues above before running the scanner.")

        if not results.get('dependencies'):
            print("\nFix: Install dependencies with:")
            print("  pip install -r ../requirements.txt")
        
        if not results.get('groq_api'):
            print("\nOptional: Set Groq API key for verification:")
            print("  $env:GROQ_API_KEY='your-key-here'")
        
        if not results.get('input_files'):
            print("\nFix: Ensure HeuristicScanner/sample.json and requirements.txt exist")

def main():
    """Run all verification checks."""
    print(f"\n{BLUE}AI Disclosure Scanner - Setup Verification{RESET}")
    print("This script validates your setup before running the scanner\n")
    
    results = {
        'python_version': verify_python_version(),
        'dependencies': verify_dependencies(),
        'groq_api': verify_groq_api_key(),
        'github_api': verify_github_api_key(),
        'input_files': verify_input_files(),
        'output_dir': verify_output_directory(),
        'modules': verify_module_imports(),
    }
    
    print_summary(results)

    if all(v is True for k, v in results.items()):
        return 0
    elif any(v is False for k, v in results.items()):
        return 1
    else:
        return 0  # Warnings only

if __name__ == '__main__':
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Verification interrupted{RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}Unexpected error during verification: {e}{RESET}")
        sys.exit(1)
