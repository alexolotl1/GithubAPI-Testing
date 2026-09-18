"""
Groq AI Classifier for AI Disclosure Analysis

Wraps Groq's chat completions API for four tasks: org classification,
policy-page verification, repo AI-usage analysis, and plain-English
summaries. Setup: set GROQ_API_KEY.
"""

import os
import json
import logging
import time
from typing import Dict, Optional, List
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

MAX_PAGES_PER_CALL = 5  # per-org candidate cap sent to evaluate_policy_pages


class GroqAIClassifier:
    """Classify and analyze AI disclosure-related content using Groq."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('GROQ_API_KEY')

        if not self.api_key:
            raise ValueError(
                "GROQ_API_KEY not found. Set environment variable or pass api_key."
            )

        self.client = Groq(api_key=self.api_key)
        self.model = "openai/gpt-oss-120b"  # current largest general-purpose Groq model
        logger.info("Groq classifier initialized")

    def _complete(self, prompt: str, temperature: float, max_tokens: int, retries: int = 1):
        """chat.completions.create with one retry on transient failure."""
        last_error = None
        for attempt in range(retries + 1):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                last_error = e
                if attempt < retries:
                    time.sleep(2)
        raise last_error

    def classify_organization_type(self, org_name: str, repo_description: str) -> Dict:
        """Classify org type + likelihood of having an AI policy (0-100)."""
        prompt = f"""Analyze this GitHub organization and determine:
1. Organization type (company/university/research/open-source/individual)
2. Likelihood they have AI policies (0-100 score)
3. Whether they likely use AI in their products/services
4. Key industries they operate in

Organization: {org_name}
Repository domain: {repo_description}

Respond in JSON format:
{{
    "org_type": "company|university|research|open-source|individual",
    "likelihood_has_ai_policy": 0-100,
    "likely_uses_ai": true|false,
    "industries": ["industry1", "industry2"],
    "reasoning": "brief explanation"
}}"""

        try:
            response = self._complete(prompt, temperature=0.3, max_tokens=500)
            text = response.choices[0].message.content
            json_start = text.find('{')
            json_end = text.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                return json.loads(text[json_start:json_end])
            return {"error": "Could not parse response", "raw": text}

        except Exception as e:
            logger.error(f"Error classifying organization: {e}")
            return {"error": str(e)}

    def evaluate_policy_pages(self, pages: List[Dict]) -> Dict:
        """
        Verify which candidate pages are real AI disclosure policies.

        Sends up to MAX_PAGES_PER_CALL pages in one prompt, gets back a
        strict JSON array (each object echoes the page's 'url' for joining
        back to candidates). Two genres both count: corporate "responsible
        AI" governance, and contributor-facing commit-disclosure policies.

        Returns {'analysis', 'verdicts', 'pages_analyzed', 'parse_error'}.
        """
        if not pages:
            return {"pages_analyzed": 0, "verdicts": [], "analysis": "", "parse_error": None}

        pages_to_send = pages[:MAX_PAGES_PER_CALL]
        if len(pages) > MAX_PAGES_PER_CALL:
            logger.warning(
                f"evaluate_policy_pages: {len(pages)} candidates found, only "
                f"verifying the top {MAX_PAGES_PER_CALL} by crawler score — "
                f"{len(pages) - MAX_PAGES_PER_CALL} left unverified this call."
            )

        page_blocks = []
        for i, p in enumerate(pages_to_send):
            snippet = p.get('text_snippet', '').strip()
            snippet_truncated = snippet[:700] + '…' if len(snippet) > 700 else snippet
            matched = p.get('matched_signals', [])
            block = (
                f"PAGE {i + 1}\n"
                f"URL: {p.get('url', 'N/A')}\n"
                f"Title: {p.get('title', 'N/A')}\n"
                f"Crawler score: {p.get('policy_score', 0)} "
                f"(matched signals: {', '.join(matched) if matched else 'none'})\n"
                f"Content excerpt:\n{snippet_truncated}"
            )
            page_blocks.append(block)
        pages_summary = "\n\n---\n\n".join(page_blocks)

        prompt = f"""You are auditing candidate web pages found by an automated crawler that
may produce false positives (e.g. conference pages, generic governance
documents, product pages that happen to contain words like "policy" or
"trusted"). Be skeptical by default — only mark is_policy_page true if the
page GENUINELY addresses AI disclosure or AI usage policy.

There are TWO distinct legitimate categories — a page matching EITHER counts
as is_policy_page true:

1. "Corporate/governance" — the organization's own policy on its use or
   development of AI (responsible AI principles, AI ethics, AI governance).
2. "Contribution/commit disclosure" — a policy telling CONTRIBUTORS whether
   and how they may use AI tools (e.g. LLMs, coding assistants) when
   authoring contributions to the project's code or docs, including
   commit-message disclosure conventions such as "Assisted-by:" or
   "Generated-by:" trailers, AI-generated-content attribution requirements,
   or similar contributor-facing AI usage/disclosure rules. This category is
   common on open-source foundation sites (e.g. Apache, Linux Foundation
   style projects) and is just as valid a match as category 1 even though it
   doesn't mention "ethics" or "governance".

For each of the {len(pages_to_send)} pages below, output ONE JSON object. A page
is NOT a policy page just because it contains the word "AI" somewhere, or
generic terms like "governance"/"safety"/"trust" without AI being the actual
subject. Reject conference/event pages, product pages, generic legal/code-of-
conduct pages, and documentation indexes unless they specifically address one
of the two categories above.

Pages to evaluate:

{pages_summary}

Respond with ONLY a JSON array (no prose, no markdown fences, no commentary
before or after), one object per page, in the same order, each with this
exact shape:
[
  {{
    "url": "<echo the exact URL from the page above>",
    "is_policy_page": true or false,
    "policy_type": "AI disclosure|AI ethics|AI governance|responsible AI|ai usage in contributions|none",
    "transparency_score": 0-100,
    "key_commitments": ["..."],
    "disclosure_requirement": true or false,
    "summary": "1-2 sentence justification for your is_policy_page decision"
  }}
]"""

        try:
            response = self._complete(prompt, temperature=0.1, max_tokens=2000)
            text = response.choices[0].message.content
            verdicts, parse_error = self._parse_verdict_array(text, len(pages_to_send))

            return {
                "analysis": text,
                "verdicts": verdicts,
                "pages_analyzed": len(pages),
                "parse_error": parse_error,
            }

        except Exception as e:
            logger.error(f"Error evaluating policy pages: {e}")
            return {
                "error": str(e),
                "verdicts": [],
                "analysis": "",
                "pages_analyzed": len(pages),
                "parse_error": str(e),
            }

    @staticmethod
    def _parse_verdict_array(text: str, expected_count: int) -> tuple:
        """Parse Groq's JSON array reply. Tries the whole response, then the
        first [...] substring, then trailing-comma repair. An empty list
        with a parse_error means "unknown," never "all rejected"."""
        candidates_to_try = [text.strip()]

        array_start = text.find('[')
        array_end = text.rfind(']') + 1
        if array_start >= 0 and array_end > array_start:
            candidates_to_try.append(text[array_start:array_end])

        for candidate in candidates_to_try:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return parsed, None
            except json.JSONDecodeError:
                try:
                    import re as _re
                    repaired = _re.sub(r',\s*([}\]])', r'\1', candidate)
                    parsed = json.loads(repaired)
                    if isinstance(parsed, list):
                        return parsed, None
                except Exception:
                    continue

        return [], f"Could not parse JSON array from response (expected {expected_count} verdicts)"

    def analyze_repository_ai_usage(self, repo_name: str, repo_description: str,
                                    languages: List[str], topics: List[str]) -> Dict:
        """Assess whether a repo likely uses/develops AI and what it should disclose."""
        prompt = f"""Analyze this GitHub repository for AI-related usage:

Repository: {repo_name}
Description: {repo_description}
Languages: {', '.join(languages)}
Topics: {', '.join(topics)}

Determine:
1. Does this repo likely use or develop AI/ML?
2. If so, what kind of AI? (LLM, computer vision, etc.)
3. Should they have an AI disclosure policy?
4. What should they disclose?

Respond in JSON:
{{
    "uses_ai": true|false,
    "ai_type": ["LLM", "computer vision", "NLP", "ML", "other"],
    "requires_disclosure": true|false,
    "disclosure_topics": ["training data sources", "model limitations", "bias testing", ...],
    "risk_level": "low|medium|high",
    "recommendation": "suggested policy focus areas",
    "confidence": 0-100
}}"""

        try:
            response = self._complete(prompt, temperature=0.3, max_tokens=600)
            text = response.choices[0].message.content
            json_start = text.find('{')
            json_end = text.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                return json.loads(text[json_start:json_end])
            return {"error": "Could not parse response", "raw": text}

        except Exception as e:
            logger.error(f"Error analyzing repository: {e}")
            return {"error": str(e)}

    def summarize_disclosure_findings(self, findings: Dict) -> str:
        """Generate a short human-readable summary of a scan_repository() result."""
        prompt = f"""Based on these AI disclosure findings, provide a brief executive summary:

Findings:
{json.dumps(findings, indent=2)[:2000]}

Summary should include:
1. Whether org has AI disclosure policies
2. What policies were found
3. Transparency level assessment
4. Key recommendations for improvement

Keep to 100-150 words."""

        try:
            response = self._complete(prompt, temperature=0.4, max_tokens=300)
            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return f"Error generating summary: {e}"


if __name__ == "__main__":
    try:
        classifier = GroqAIClassifier()
        org_result = classifier.classify_organization_type(
            "OpenAI",
            "AI/LLM development and deployment"
        )
        print("Organization Classification:")
        print(json.dumps(org_result, indent=2))
    except ValueError as e:
        print(f"Setup required: {e}")
