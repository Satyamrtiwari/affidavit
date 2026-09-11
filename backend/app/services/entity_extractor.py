"""
Entity Extractor Service — Stage 3 of the pipeline.

Uses Groq API (Llama 3.3 70B) to extract structured entities from
case information text into a CaseEntities Pydantic model.

The extraction prompt is carefully engineered to:
1. Identify all parties, deponent details, reply points
2. Correctly classify organisation vs individual respondents
3. Map reply points to legal move types
4. Output valid JSON matching our Pydantic schema
"""

import json
import logging
from typing import Optional

from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS
from app.models.entities import CaseEntities

logger = logging.getLogger(__name__)

# ─── System Prompt ────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are a legal document analysis system. Your task is to extract structured entities from Indian court case information documents.

You MUST output a valid JSON object matching the exact schema below. Do NOT include any text outside the JSON.

CRITICAL RULES:
1. If the respondent is an organisation/company/authority (not a person), set "is_organisation" to true for that respondent.
2. If the deponent is filing on behalf of an organisation (not themselves), set "is_organisation_representative" to true and include their designation and organisation name.
3. Reply points must be classified into these move types:
   - IDENTITY_AND_PERUSAL: First paragraph — who the deponent is, that they've read the petition
   - BLANKET_DENIAL: General denial of all allegations
   - PRELIMINARY_POSITION: Petition is misconceived, action was lawful
   - SUBSTANTIVE_ANSWER: Answering specific allegations
   - DOCUMENT_REFERENCE: Referencing/relying on specific documents/exhibits
   - CLOSING: Final paragraph — petition deserves dismissal
4. Extract the verification verb exactly as stated (usually "solemnly affirm").
5. Dates should be in "DD Month YYYY" format (e.g., "5 September 2026").
6. Forum city should be extracted from the court name (e.g., "BOMBAY" from "HIGH COURT OF JUDICATURE AT BOMBAY").

JSON SCHEMA:
{
  "forum_city": "string — city name in CAPS, e.g. BOMBAY",
  "jurisdiction_type": "string — e.g. ORDINARY ORIGINAL CIVIL",
  "case_type": "string — e.g. WRIT PETITION",
  "case_number": "string — e.g. 1847",
  "year": "string — e.g. 2026",
  "petitioner": {
    "name": "string",
    "description": "string or null",
    "address": "string or null",
    "through": "string or null"
  },
  "respondents": [
    {
      "name": "string",
      "respondent_number": "integer",
      "is_organisation": "boolean",
      "description": "string or null",
      "address": "string or null",
      "through": "string or null"
    }
  ],
  "filing_respondent_number": "integer — which respondent this affidavit is for",
  "deponent": {
    "name": "string",
    "is_organisation_representative": "boolean",
    "designation": "string or null",
    "organisation": "string or null",
    "age": "string or null",
    "address": "string",
    "occupation": "string or null",
    "verification_verb": "solemnly affirm OR swear and affirm"
  },
  "reply_points": [
    {
      "point_number": "integer",
      "move_type": "IDENTITY_AND_PERUSAL | BLANKET_DENIAL | PRELIMINARY_POSITION | SUBSTANTIVE_ANSWER | DOCUMENT_REFERENCE | CLOSING",
      "title": "string — brief title",
      "content": "string — the substantive content to incorporate",
      "exhibit": {
        "label": "string, e.g. EXHIBIT-'A'",
        "description": "string"
      } or null
    }
  ],
  "prayer_points": ["string — each prayer point"],
  "attestation_place": "string",
  "attestation_date": "string — DD Month YYYY format",
  "advocate_firm": "string or null",
  "advocate_for": "string or null"
}"""

EXTRACTION_USER_PROMPT = """Extract all structured entities from the following case information document. 

Output ONLY valid JSON matching the schema provided. No explanations, no markdown, no code fences.

CASE INFORMATION:
{case_text}"""


def _get_groq_client() -> Groq:
    """Create and return a Groq client."""
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is not configured. "
            "Set it in your .env file."
        )
    return Groq(api_key=GROQ_API_KEY)


def _parse_llm_json(response_text: str) -> dict:
    """
    Parse JSON from LLM response, handling common issues like
    markdown code fences or extra text.
    """
    text = response_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        # Remove first line (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        # Remove trailing ```
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")


def extract_entities(case_text: str, reference_text: Optional[str] = None) -> CaseEntities:
    """
    Extract structured entities from case information text using Groq LLM.

    Args:
        case_text: Cleaned text from the case information PDF
        reference_text: Optional cleaned text from a reference document
                       (used to inform the extraction if provided)

    Returns:
        CaseEntities: Validated structured entities

    Raises:
        ValueError: If extraction fails or JSON is invalid
    """
    client = _get_groq_client()

    # Build the user prompt
    user_prompt = EXTRACTION_USER_PROMPT.format(case_text=case_text)

    # If reference text is provided, add context
    if reference_text:
        user_prompt += (
            "\n\nREFERENCE DOCUMENT (use this to understand the expected structure):\n"
            f"{reference_text}"
        )

    logger.info(f"Sending extraction request to Groq ({GROQ_MODEL})")
    logger.debug(f"Prompt length: {len(user_prompt)} chars")

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        response_text = response.choices[0].message.content
        logger.info(f"Received response: {len(response_text)} chars")
        logger.debug(f"Raw response: {response_text[:500]}")

        # Parse JSON from response
        data = _parse_llm_json(response_text)

        # Validate through Pydantic model
        entities = CaseEntities.model_validate(data)

        logger.info(
            f"Successfully extracted entities: "
            f"{len(entities.respondents)} respondents, "
            f"{len(entities.reply_points)} reply points, "
            f"filing for Respondent No.{entities.filing_respondent_number}"
        )

        return entities

    except Exception as e:
        logger.error(f"Entity extraction failed: {e}")
        raise ValueError(f"Failed to extract entities: {e}")


def extract_entities_from_dict(data: dict) -> CaseEntities:
    """
    Create CaseEntities from a pre-existing dictionary.
    Useful for testing or when entities are provided directly.

    Args:
        data: Dictionary matching the CaseEntities schema

    Returns:
        CaseEntities: Validated structured entities
    """
    return CaseEntities.model_validate(data)
