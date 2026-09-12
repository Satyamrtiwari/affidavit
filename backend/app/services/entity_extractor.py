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
import time
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
2. If the deponent is filing on behalf of an organisation (not themselves), set "is_organisation_representative" to true and include their designation and organisation name. If the deponent IS the respondent (individual person), set "is_organisation_representative" to false and set designation to null.
3. Reply points must be classified into these move types:
   - IDENTITY_AND_PERUSAL: First paragraph — who the deponent is, that they've read the petition/plaint
   - BLANKET_DENIAL: General denial of all allegations
   - PRELIMINARY_POSITION: Petition/Suit is misconceived, action was lawful
   - SUBSTANTIVE_ANSWER: Answering specific allegations
   - DOCUMENT_REFERENCE: Referencing/relying on specific documents/exhibits
   - CLOSING: Final paragraph — petition/suit deserves dismissal
4. Extract the verification verb exactly as stated (usually "solemnly affirm").
5. Dates should be in "DD Month YYYY" format (e.g., "5 September 2026").
6. court_name: Extract the FULL court name as it appears (e.g., "HIGH COURT OF JUDICATURE AT BOMBAY", "HIGH COURT OF DELHI", "DISTRICT COURT, NORTH DELHI"). Do NOT include "IN THE".
7. forum_city: Extract the city name from the court name (e.g., "BOMBAY", "DELHI").
8. petitioner_label: Extract the party role label — "Petitioner" for Writ Petition, "Plaintiff" for Civil Suit, "Appellant" for Appeal, "Complainant" for Complaint. Look for how the document refers to the moving party.
9. respondent_label: Extract the opposing role label — "Respondent" for Writ Petition, "Defendant" for Civil Suit, etc.
10. If designation is "Not Applicable" or "N/A", set it to null and set is_organisation_representative to false.

JSON SCHEMA:
{
  "court_name": "string — full court name, e.g. 'HIGH COURT OF JUDICATURE AT BOMBAY'",
  "forum_city": "string — city name in CAPS, e.g. BOMBAY",
  "jurisdiction_type": "string — e.g. ORDINARY ORIGINAL CIVIL",
  "case_type": "string — e.g. WRIT PETITION or COMMERCIAL SUIT or CIVIL SUIT",
  "case_number": "string — e.g. 1847",
  "year": "string — e.g. 2026",
  "petitioner_label": "string — 'Petitioner' or 'Plaintiff' or 'Appellant' or 'Complainant'",
  "respondent_label": "string — 'Respondent' or 'Defendant' or 'Opposite Party'",
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
    "designation": "string or null — set to null if 'Not Applicable' or 'N/A'",
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
  "prayer_points": ["string — each prayer point from the case info"],
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
        max_attempts = 4
        response = None
        for attempt in range(max_attempts):
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
                break
            except Exception as exc:
                err_str = str(exc)
                if ("429" in err_str or "rate_limit" in err_str or "tokens" in err_str) and attempt < max_attempts - 1:
                    wait_sec = 3 * (attempt + 1)
                    logger.warning(f"Groq rate limit reached, sleeping {wait_sec}s before retry (attempt {attempt+1}/{max_attempts})...")
                    time.sleep(wait_sec)
                    continue
                raise

        response_text = response.choices[0].message.content
        logger.info(f"Received response: {len(response_text)} chars")
        logger.debug(f"Raw response: {response_text[:500]}")

        # Parse JSON from response
        data = _parse_llm_json(response_text)

        # Normalize raw data for robustness
        data = _normalize_extracted_data(data)

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


def _normalize_extracted_data(data: dict) -> dict:
    """
    Sanitize and normalize raw JSON extracted from LLM before Pydantic validation.
    Guarantees robustness for arbitrary case PDFs and DOCX files.
    """
    if not isinstance(data, dict):
        return data

    # Ensure court and case header fields
    if not data.get("court_name"):
        data["court_name"] = "HIGH COURT OF JUDICATURE AT BOMBAY"
    if not data.get("forum_city"):
        data["forum_city"] = "BOMBAY"
    if not data.get("jurisdiction_type"):
        data["jurisdiction_type"] = "ORDINARY ORIGINAL CIVIL JURISDICTION"
    if not data.get("case_type"):
        data["case_type"] = "WRIT PETITION"
    if not data.get("case_number"):
        data["case_number"] = "1"
    else:
        data["case_number"] = str(data["case_number"])
    if not data.get("year"):
        data["year"] = "2024"
    else:
        data["year"] = str(data["year"])
    if not data.get("filing_respondent_number"):
        data["filing_respondent_number"] = 1

    # Ensure parties
    if not data.get("petitioner"):
        data["petitioner"] = {"name": "Petitioner"}
    elif isinstance(data.get("petitioner"), dict) and not data["petitioner"].get("name"):
        data["petitioner"]["name"] = "Petitioner"
    if not data.get("respondents"):
        data["respondents"] = [{"name": "Respondent No. 1", "respondent_number": 1, "is_organisation": False}]

    # Ensure reply points
    if not data.get("reply_points"):
        data["reply_points"] = [
            {"point_number": 1, "title": "Identity and Perusal", "move_type": "IDENTITY_AND_PERUSAL", "content": "I say that I am competent to affirm this Affidavit in Reply."},
            {"point_number": 2, "title": "Denial of Allegations", "move_type": "BLANKET_DENIAL", "content": "I deny each and every contention raised in the petition."},
            {"point_number": 3, "title": "Prayer for Dismissal", "move_type": "CLOSING", "content": "The petition deserves to be dismissed with costs."}
        ]
    else:
        for idx, pt in enumerate(data["reply_points"], 1):
            if isinstance(pt, dict):
                if not pt.get("title"):
                    pt["title"] = f"Reply Point {pt.get('point_number', idx)}"
                if not pt.get("point_number"):
                    pt["point_number"] = idx
                if not pt.get("content"):
                    pt["content"] = "Content not specified."

    # Ensure attestation fields
    if not data.get("attestation_place"):
        data["attestation_place"] = "Mumbai"
    if not data.get("attestation_date"):
        from datetime import datetime
        data["attestation_date"] = datetime.now().strftime("%d %B %Y")

    deponent = data.get("deponent")
    if not isinstance(deponent, dict):
        respondents = data.get("respondents") or []
        first_resp_name = respondents[0].get("name") if respondents and isinstance(respondents[0], dict) else "Deponent"
        deponent = {
            "name": first_resp_name or "Deponent",
            "is_organisation_representative": False,
            "address": "Mumbai",
            "verification_verb": "solemnly affirm",
        }
        data["deponent"] = deponent
    else:
        if not deponent.get("name"):
            respondents = data.get("respondents") or []
            first_resp_name = respondents[0].get("name") if respondents and isinstance(respondents[0], dict) else "Deponent"
            deponent["name"] = first_resp_name or "Deponent"
        if not deponent.get("address"):
            deponent["address"] = "Mumbai"
        if "is_organisation_representative" not in deponent or deponent.get("is_organisation_representative") is None:
            deponent["is_organisation_representative"] = False

        # Normalize verification_verb to allowed literals
        v = str(deponent.get("verification_verb") or "").lower()
        if "swear" in v:
            deponent["verification_verb"] = "swear and affirm"
        else:
            deponent["verification_verb"] = "solemnly affirm"

        # Clean designation if LLM puts "Not Applicable" / "N/A"
        desig = str(deponent.get("designation") or "").strip()
        if any(term in desig.lower() for term in ["not applicable", "n/a", "none"]):
            deponent["designation"] = None
            deponent["is_organisation_representative"] = False

    return data


def extract_entities_from_dict(data: dict) -> CaseEntities:
    """
    Create CaseEntities from a pre-existing dictionary.
    Useful for testing or when entities are provided directly.

    Args:
        data: Dictionary matching the CaseEntities schema

    Returns:
        CaseEntities: Validated structured entities
    """
    data = _normalize_extracted_data(data)
    return CaseEntities.model_validate(data)

