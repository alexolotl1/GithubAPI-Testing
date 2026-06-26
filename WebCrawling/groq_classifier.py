"""
Groq AI Classifier for AI Disclosure Analysis

Uses Groq's powerful LLM to analyze and classify:
- Whether an organization has AI policies
- Whether found pages are legitimate AI disclosures
- AI policy details and requirements
- Organization's AI stance and transparency level

Groq provides fast inference via Mixtral-8x7B model.
Setup: Set GROQ_API_KEY environment variable
"""

import os
import json
import logging
from typing import Dict, Optional, List
from dotenv import load_dotenv
from groq import Groq

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


class GroqAIClassifier:
    """Classify and analyze AI disclosure-related content using Groq."""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Groq classifier.
        
        Args:
            api_key: Groq API key (uses GROQ_API_KEY env var if not provided)
        """
        self.api_key = api_key or os.getenv('GROQ_API_KEY')
        
        if not self.api_key:
            raise ValueError(
                "GROQ_API_KEY not found. Set environment variable or pass api_key."
            )
        
        self.client = Groq(api_key=self.api_key)
        self.model = "llama-3.3-70b-versatile"  # Fast, capable model
        logger.info("Groq classifier initialized")
    
    def classify_organization_type(self, org_name: str, repo_description: str) -> Dict:
        """
        Classify organization type and likelihood to have AI policies.
        
        Args:
            org_name: Organization name
            repo_description: Repository description/topic
            
        Returns:
            Classification result with scores and reasoning
        """
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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )
            
            # Parse response
            text = response.choices[0].message.content
            # Extract JSON from response
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
            else:
                result = {"error": "Could not parse response", "raw": text}
            
            return result
        
        except Exception as e:
            logger.error(f"Error classifying organization: {e}")
            return {"error": str(e)}
    
    def evaluate_policy_pages(self, pages: List[Dict]) -> Dict:
        """
        Evaluate whether found pages are legitimate AI disclosure policies.

        IMPORTANT CHANGE: previously this asked Groq for one ```json fence
        per page, which it didn't reliably produce (inconsistent fencing,
        prose between blocks), so downstream code couldn't parse Groq's
        verdicts and silently ignored them — every crawled "candidate" was
        treated as a confirmed policy regardless of what Groq actually said.

        Now asks for ONE strict JSON array covering all pages, with each
        page's 'url' echoed back verbatim so the caller can join verdicts
        back to the original crawled candidates by URL. The result dict
        includes a pre-parsed 'verdicts' list — callers should use that
        directly rather than re-parsing 'analysis'.

        Args:
            pages: List of page data with titles, URLs, content snippets

        Returns:
            {
                'analysis': str,            # raw model text (for logging/debug)
                'verdicts': List[Dict],      # parsed array, each with url,
                                              # is_policy_page, transparency_score, …
                'pages_analyzed': int,
                'parse_error': str | None,   # set if JSON parsing failed
            }
        """
        if not pages:
            return {"pages_analyzed": 0, "verdicts": [], "analysis": "", "parse_error": None}

        # Build a rich summary that includes the actual page text so Groq
        # can make a real judgement rather than guessing from a URL alone.
        page_blocks = []
        for i, p in enumerate(pages[:5]):
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
page is GENUINELY about the organization's policy, principles, or
disclosures regarding their own use or development of AI.

For each of the {len(pages[:5])} pages below, output ONE JSON object. A page
is NOT a policy page just because it contains the word "AI" somewhere, or
generic terms like "governance"/"safety"/"trust" without AI being the actual
subject. Reject conference/event pages, product pages, generic legal/code-of-
conduct pages, and documentation indexes unless they specifically address AI
disclosure, AI ethics, or responsible AI use.

Pages to evaluate:

{pages_summary}

Respond with ONLY a JSON array (no prose, no markdown fences, no commentary
before or after), one object per page, in the same order, each with this
exact shape:
[
  {{
    "url": "<echo the exact URL from the page above>",
    "is_policy_page": true or false,
    "policy_type": "AI disclosure|AI ethics|AI governance|responsible AI|none",
    "transparency_score": 0-100,
    "key_commitments": ["..."],
    "disclosure_requirement": true or false,
    "summary": "1-2 sentence justification for your is_policy_page decision"
  }}
]"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000
            )

            text = response.choices[0].message.content
            verdicts, parse_error = self._parse_verdict_array(text, len(pages[:5]))

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
        """
        Parse Groq's JSON array response into a list of verdict dicts.

        Tries, in order:
          1. The whole response as a JSON array
          2. The first [...] substring found (in case of stray prose)
          3. Trailing-comma repair on either of the above

        Returns (verdicts, parse_error). parse_error is None on success,
        otherwise a short string describing what went wrong — callers
        should NOT treat an empty verdicts list as "all rejected"; they
        should treat it as "unknown" and leave candidates unverified.
        """
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
                # Light repair: trailing commas before ] or }
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
        """
        Analyze a repository to determine AI usage patterns.
        
        Args:
            repo_name: Repository name
            repo_description: Repository description
            languages: Programming languages used
            topics: Repository topics/tags
            
        Returns:
            Analysis of likely AI usage and disclosure needs
        """
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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=600
            )
            
            text = response.choices[0].message.content
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
            else:
                result = {"error": "Could not parse response", "raw": text}
            
            return result
        
        except Exception as e:
            logger.error(f"Error analyzing repository: {e}")
            return {"error": str(e)}
    
    def summarize_disclosure_findings(self, findings: Dict) -> str:
        """
        Generate human-readable summary of disclosure findings.
        
        Args:
            findings: Complete findings dictionary
            
        Returns:
            Summary text
        """
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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=300
            )
            
            return response.choices[0].message.content
        
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return f"Error generating summary: {e}"


# Example usage
if __name__ == "__main__":
    # Initialize classifier
    try:
        classifier = GroqAIClassifier()
        
        # Test organization classification
        org_result = classifier.classify_organization_type(
            "OpenAI",
            "AI/LLM development and deployment"
        )
        print("Organization Classification:")
        print(json.dumps(org_result, indent=2))
        
    except ValueError as e:
        print(f"Setup required: {e}")
