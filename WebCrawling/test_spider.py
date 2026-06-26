#!/usr/bin/env python3
"""
Test if Scrapy spider subprocess is working
"""
import subprocess
import sys
import json
import tempfile
import os

# Create a minimal test script like the one being generated
test_spider_script = '''
import sys
import json
import asyncio

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from twisted.internet import asyncioreactor
    asyncioreactor.install()
    print("OK: asyncioreactor installed", file=sys.stderr)
except Exception as e:
    print(f"FAIL: asyncioreactor error: {e}", file=sys.stderr)

try:
    import scrapy
    print(f"OK: scrapy imported: {scrapy.__version__}", file=sys.stderr)
except Exception as e:
    print(f"FAIL: scrapy error: {e}", file=sys.stderr)
    sys.exit(1)

try:
    from scrapy.crawler import CrawlerProcess
    print("OK: CrawlerProcess imported", file=sys.stderr)
except Exception as e:
    print(f"FAIL: CrawlerProcess error: {e}", file=sys.stderr)

# Create empty results
results = {"test": "success"}
print(json.dumps(results))
'''

# Write to temp file
with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w') as f:
    f.write(test_spider_script)
    script_path = f.name

print(f"Testing Scrapy subprocess with script: {script_path}")
print("=" * 60)

try:
    proc = subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        timeout=10,
    )
    
    print(f"Exit code: {proc.returncode}")
    print(f"\nStdout:\n{proc.stdout}")
    print(f"\nStderr:\n{proc.stderr}")
    
except subprocess.TimeoutExpired:
    print("ERROR: Subprocess timed out!")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    try:
        os.unlink(script_path)
    except:
        pass
