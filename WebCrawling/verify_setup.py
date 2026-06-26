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
from typing import Tuple, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Color codes for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text: str):
    """Print section header."""
    print(f"\n{BLUE}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{RESET}")

def print_ok(text: str):
    """Print success message."""
    print(f"{GREEN}✓{RESET} {text}")

def print_error(text: str):
    """Print error message."""
    print(f"{RED}✗{RESET} {text}")

def print_warning(text: str):
    """Print warning message."""
    print(f"{YELLOW}⚠{RESET} {text}")

def verify_python_version() -> bool:
    """Check Python version (3.8+)."""
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
    """Check if all required packages are installed."""
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
        print("  pip install -r requirements.txt")
    
    return all_ok

def verify_groq_api_key() -> bool:
    """Check Groq API key configuration."""
    print_header("3. Groq API Configuration")
    
    api_key = os.getenv('GROQ_API_KEY')
    
    if api_key:
        # Mask key for security
        masked = api_key[:10] + '...' + api_key[-5:]
        print_ok(f"GROQ_API_KEY found: {masked}")
        
        # Test connection
        try:
            from groq import Groq
            client = Groq(api_key=api_key)
            
            # Try a simple API call
            response = client.messages.create(
                model="mixtral-8x7b-32768",
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
        print_warning("Set with: export GROQ_API_KEY='your-key-here'")
        print_warning("Or create .env file with: GROQ_API_KEY=your-key-here")
        return False

def verify_github_api_key() -> bool:
    """Check GitHub API token (optional)."""
    print_header("4. GitHub API Configuration (Optional)")
    
    token = os.getenv('GITHUB_TOKEN')
    
    if token:
        masked = token[:10] + '...' + token[-5:]
        print_ok(f"GITHUB_TOKEN found: {masked}")
        
        # Test connection
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
                print_warning(f"GitHub API returned status {response.status_code}")
                return False
        
        except Exception as e:
            print_warning(f"GitHub API connection failed: {e}")
            return False
    else:
        print_warning("GITHUB_TOKEN not set (optional - API calls will be slower)")
        return None  # Optional

def verify_input_files() -> bool:
    """Check if input files exist."""
    print_header("5. Input Files Check")
    
    required_files = {
        '../sample_100.json': 'Repository list',
        '../requirements.txt': 'Dependencies list',
    }
    
    all_ok = True
    for filename, description in required_files.items():
        if os.path.exists(filename):
            size = os.path.getsize(filename)
            print_ok(f"{filename:<20} ({size:,} bytes) - {description}")
        else:
            print_error(f"{filename:<20} - NOT FOUND")
            all_ok = False
    
    if os.path.exists('../sample_100.json'):
        try:
            with open('../sample_100.json', 'r') as f:
                data = json.load(f)
            repo_count = len(data.get('repositories', []))
            print_ok(f"Contains {repo_count} repositories")
        except json.JSONDecodeError:
            print_error("../sample_100.json is invalid JSON")
            all_ok = False
    
    return all_ok

def verify_output_directory() -> bool:
    """Check if output directory is writable."""
    print_header("6. Output Directory Check")
    
    try:
        # Try to write a test file
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
    """Verify custom modules can be imported."""
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
    """Print verification summary."""
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
        
        # Provide fix suggestions
        if not results.get('dependencies'):
            print("\nFix: Install dependencies with:")
            print("  pip install -r requirements.txt")
        
        if not results.get('groq_api'):
            print("\nFix: Set Groq API key:")
            print("  export GROQ_API_KEY='your-key-here'")
        
        if not results.get('input_files'):
            print("\nFix: Ensure ../sample_100.json and ../requirements.txt exist")

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
    
    # Return exit code
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
