"""
Fetches and parses melissawm/open-source-ai-contribution-policies' README
table into policies.json/.csv — the input list for disclosure_scanner.py.

The source repo tracks, per project: whether AI/LLM use is allowed, whether
disclosure is required, whether a copyright statement is required, and
whether a human must review the output. We care specifically about projects
where AI is allowed AND disclosure is required — those are the ones where
"did contributors actually disclose it in their commits" is a meaningful
question to ask.
"""

import csv
import json
import os
import re
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SOURCE_README = (
    "https://raw.githubusercontent.com/melissawm/"
    "open-source-ai-contribution-policies/main/README.md"
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
GITHUB_REPO_RE = re.compile(r'github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)')

# Well-known projects whose table links point at their own domain (numpy.org,
# curl.se, ...) rather than github.com. Resolved by hand to avoid guessing
# wrong; foundations with no single canonical repo are left unresolved.
KNOWN_GITHUB_REPOS = {
    'Apache Kvrocks': ('apache', 'kvrocks'),
    'Arrow': ('apache', 'arrow'),
    'CCExtractor': ('CCExtractor', 'ccextractor'),
    'curl': ('curl', 'curl'),
    'Django': ('django', 'django'),
    'GDAL': ('OSGeo', 'gdal'),
    'IREE (Intermediate Representation Execution Environment)': ('iree-org', 'iree'),
    'Joomla': ('joomla', 'joomla-cms'),
    'Kubernetes': ('kubernetes', 'kubernetes'),
    'Linux Kernel': ('torvalds', 'linux'),  # read-only mirror; kernel.org git is canonical
    'LLVM': ('llvm', 'llvm-project'),
    'Matplotlib': ('matplotlib', 'matplotlib'),
    'napari': ('napari', 'napari'),
    'NumPy': ('numpy', 'numpy'),
    'Pandas': ('pandas-dev', 'pandas'),
    'Rust': ('rust-lang', 'rust'),
    'scikit-image': ('scikit-image', 'scikit-image'),
    'scikit-learn': ('scikit-learn', 'scikit-learn'),
    'SciPy': ('scipy', 'scipy'),
    'SymPy': ('sympy', 'sympy'),
    'Wagtail': ('wagtail', 'wagtail'),
}


def _first_link(cell: str):
    m = LINK_RE.search(cell)
    return m.group(2) if m else None


def _project_name(cell: str):
    m = LINK_RE.search(cell)
    return m.group(1) if m else cell.strip()


def _extract_github_repo(*urls):
    for url in urls:
        if not url:
            continue
        m = GITHUB_REPO_RE.search(url)
        if m:
            owner, repo = m.group(1), m.group(2)
            if repo.endswith('.git'):
                repo = repo[:-4]
            return owner, repo
    return None, None


def fetch_readme() -> str:
    req = urllib.request.Request(SOURCE_README, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode('utf-8')


def parse_table(readme_text: str) -> list:
    lines = readme_text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if l.startswith('Project |'):
            start = i + 2  # skip header + '---' separator row
            break
    if start is None:
        raise ValueError("Couldn't find the 'Project |' table header in the source README")

    rows = []
    for l in lines[start:]:
        if not l.strip() or l.startswith('#'):
            break
        rows.append(l)

    parsed = []
    for l in rows:
        parts = [p.strip() for p in l.split('|')]
        if len(parts) < 4:
            continue
        project_cell, policy_cell, allowed, disclosure = parts[0], parts[1], parts[2], parts[3]
        copyright_stmt = parts[4] if len(parts) > 4 else ''
        human_loop = parts[5] if len(parts) > 5 else ''
        notes = parts[6] if len(parts) > 6 else ''

        name = _project_name(project_cell)
        project_url = _first_link(project_cell)
        policy_url = _first_link(policy_cell)
        owner, repo = _extract_github_repo(project_url, policy_url)
        resolved_by_hand = False
        if not owner and name in KNOWN_GITHUB_REPOS:
            owner, repo = KNOWN_GITHUB_REPOS[name]
            resolved_by_hand = True

        parsed.append({
            'project': name,
            'project_url': project_url,
            'policy_url': policy_url,
            'ai_llms_allowed': allowed,
            'disclosure_required': disclosure,
            'copyright_statement': copyright_stmt,
            'human_in_loop': human_loop,
            'notes': notes,
            'github_owner': owner,
            'github_repo': repo,
            'is_github': owner is not None,
            'resolved_by_hand': resolved_by_hand,
        })
    return parsed


def main():
    print(f"Fetching {SOURCE_README} ...")
    readme_text = fetch_readme()
    parsed = parse_table(readme_text)
    print(f"Parsed {len(parsed)} projects")

    qualifying = [
        p for p in parsed
        if p['ai_llms_allowed'].strip().lower() == 'yes'
        and p['disclosure_required'].strip().lower() == 'yes'
    ]
    resolved = [p for p in qualifying if p['is_github']]
    print(f"Qualifying (AI allowed=Yes AND disclosure required=Yes): {len(qualifying)}")
    print(f"  -> resolved to a github.com repo: {len(resolved)}")
    print(f"  -> not resolvable (no single canonical GitHub repo): {len(qualifying) - len(resolved)}")

    with open(os.path.join(BASE_DIR, 'policies.json'), 'w', encoding='utf-8') as f:
        json.dump({'all_projects': parsed, 'qualifying_projects': qualifying}, f, indent=2, ensure_ascii=False)

    with open(os.path.join(BASE_DIR, 'policies.csv'), 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(parsed[0].keys()))
        writer.writeheader()
        writer.writerows(parsed)

    print(f"Wrote policies.json and policies.csv to {BASE_DIR}")


if __name__ == '__main__':
    main()
