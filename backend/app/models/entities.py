"""
Structured Intermediate JSON Models — The heart of the pipeline.

These Pydantic models define the structured representation between
extraction and generation. This is the "structured intermediate JSON"
bonus requirement.

Flow: PDF text → LLM extraction → CaseEntities (this file) → Jinja2 template → .docx
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class PartyInfo(BaseModel):
    """Information about a party (petitioner or generic party)."""
    name: str = Field(..., description="Full name of the party")
    description: Optional[str] = Field(
        None,
        description="Description like 'Age 38 years, Occupation: Business'"
    )
    address: Optional[str] = Field(None, description="Address of the party")
    through: Optional[str] = Field(
        None,
        description="Representative, e.g. 'Through the Principal Secretary'"
    )


class RespondentInfo(BaseModel):
    """Information about a respondent party."""
    name: str = Field(..., description="Full name of the respondent")
    respondent_number: int = Field(..., description="Respondent number (1, 2, 3...)")
    is_organisation: bool = Field(
        False,
        description="True if the respondent is an organisation/company/authority, not an individual"
    )
    description: Optional[str] = Field(None, description="Description of the respondent")
    address: Optional[str] = Field(None, description="Address of the respondent")
    through: Optional[str] = Field(
        None,
        description="Representative, e.g. 'Through the Principal Secretary'"
    )


class DeponentInfo(BaseModel):
    """
    Information about the person swearing the affidavit.

    Critical rule: If the respondent is an organisation, the deponent is an
    officer filing ON BEHALF of the respondent. The phrasing changes:
    - Individual: "the Respondent No.2 above named"
    - Organisation officer: "the [Designation] of the Respondent No.2 above named"
    """
    name: str = Field(..., description="Full name of the deponent")
    is_organisation_representative: bool = Field(
        ...,
        description=(
            "True if the deponent is an officer/representative filing on behalf "
            "of an organisation respondent. False if the deponent IS the respondent."
        )
    )
    designation: Optional[str] = Field(
        None,
        description="Designation/title if filing for an org, e.g. 'Deputy Metropolitan Commissioner'"
    )
    organisation: Optional[str] = Field(
        None,
        description="Organisation name if filing on behalf, e.g. 'Mumbai Metropolitan Region Development Authority'"
    )
    age: Optional[str] = Field(None, description="Age of the deponent, e.g. '42 years'")
    address: str = Field(..., description="Address of the deponent")
    occupation: Optional[str] = Field(None, description="Occupation of the deponent")
    verification_verb: Literal["solemnly affirm", "swear and affirm"] = Field(
        "solemnly affirm",
        description="The verb used in the oath — must match the jurat"
    )


class ExhibitInfo(BaseModel):
    """Information about a document exhibit attached to the affidavit."""
    label: str = Field(..., description="Exhibit label, e.g. \"EXHIBIT-'A'\"")
    description: str = Field(
        ...,
        description="Description of the exhibit, e.g. 'communication dated 15 July 2026'"
    )


class ReplyPoint(BaseModel):
    """
    A single reply point from the case information.

    Each reply point becomes a numbered paragraph in the affidavit.
    The move_type determines the legal phrasing template used.
    """
    point_number: int = Field(..., description="Sequential point number")
    move_type: Literal[
        "IDENTITY_AND_PERUSAL",
        "BLANKET_DENIAL",
        "PRELIMINARY_POSITION",
        "SUBSTANTIVE_ANSWER",
        "DOCUMENT_REFERENCE",
        "CLOSING"
    ] = Field(..., description="The type of legal move this paragraph makes")
    title: str = Field(..., description="Brief title of this reply point")
    content: str = Field(
        ...,
        description="The substantive content to be incorporated into this paragraph"
    )
    exhibit: Optional[ExhibitInfo] = Field(
        None,
        description="Exhibit reference if this paragraph refers to a document"
    )


class CaseEntities(BaseModel):
    """
    THE structured intermediate representation.

    This is the complete extraction of all entities and content from the
    case information document. It serves as the single source of truth
    between the extraction stage and the generation stage.

    Every field here maps to a specific slot in the Jinja2 template.
    """

    # ─── Court & Case Details ─────────────────────────────────────────────
    forum_city: str = Field(
        ...,
        description="City where the court sits, e.g. 'BOMBAY'"
    )
    jurisdiction_type: str = Field(
        ...,
        description="Type of jurisdiction, e.g. 'ORDINARY ORIGINAL CIVIL'"
    )
    case_type: str = Field(
        ...,
        description="Type of proceeding, e.g. 'WRIT PETITION'"
    )
    case_number: str = Field(
        ...,
        description="Case number, e.g. '1847'"
    )
    year: str = Field(
        ...,
        description="Year of the case, e.g. '2026'"
    )

    # ─── Parties ──────────────────────────────────────────────────────────
    petitioner: PartyInfo = Field(..., description="The petitioner's details")
    respondents: list[RespondentInfo] = Field(
        ...,
        description="List of all respondents with their details"
    )
    filing_respondent_number: int = Field(
        ...,
        description="The respondent number on whose behalf this affidavit is filed"
    )

    # ─── Deponent ─────────────────────────────────────────────────────────
    deponent: DeponentInfo = Field(
        ...,
        description="Details of the person swearing the affidavit"
    )

    # ─── Reply Content ────────────────────────────────────────────────────
    reply_points: list[ReplyPoint] = Field(
        ...,
        description="Ordered list of reply points to be converted into numbered paragraphs"
    )
    prayer_points: list[str] = Field(
        default_factory=lambda: [
            "dismiss the present Writ Petition with costs",
            "refuse any interim or ad-interim relief sought by the Petitioner",
            "grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case"
        ],
        description="Prayer points (usually standard, but can be customised)"
    )

    # ─── Attestation ─────────────────────────────────────────────────────
    attestation_place: str = Field(..., description="Place of attestation, e.g. 'Mumbai'")
    attestation_date: str = Field(
        ...,
        description="Date of attestation in 'DD Month YYYY' format, e.g. '5 September 2026'"
    )

    # ─── Advocate ─────────────────────────────────────────────────────────
    advocate_firm: Optional[str] = Field(
        None,
        description="Name of the advocate firm, e.g. 'Rajan & Associates'"
    )
    advocate_for: Optional[str] = Field(
        None,
        description="Who the advocate acts for, e.g. 'Respondent No. 2'"
    )

    @property
    def paragraph_count(self) -> int:
        """Total number of body paragraphs (reply points). Used for verification check."""
        return len(self.reply_points)

    @property
    def filing_respondent(self) -> Optional[RespondentInfo]:
        """Get the respondent on whose behalf this affidavit is filed."""
        for r in self.respondents:
            if r.respondent_number == self.filing_respondent_number:
                return r
        return None

    @property
    def jurat_verb(self) -> str:
        """
        Past tense of the verification verb for the jurat section.
        'solemnly affirm' → 'Solemnly affirmed'
        'swear and affirm' → 'Sworn'
        """
        verb_map = {
            "solemnly affirm": "Solemnly affirmed",
            "swear and affirm": "Sworn",
        }
        return verb_map.get(self.deponent.verification_verb, "Solemnly affirmed")

    @property
    def clean_jurisdiction(self) -> str:
        """Ensure jurisdiction format does not duplicate 'JURISDICTION'."""
        j = self.jurisdiction_type.strip().upper()
        if j.endswith("JURISDICTION"):
            return j
        return f"{j} JURISDICTION"

    @property
    def clean_prayer_points(self) -> list[str]:
        """
        Normalize prayer points to follow 'may be pleased to: (a) ...' syntax.
        Converts third-person phrasing ('Respondent No. 2 prays that the Writ Petition be dismissed with costs')
        into direct prayer clauses ('dismiss the present Writ Petition with costs').
        """
        import re
        cleaned = []
        for p in self.prayer_points:
            pt = p.strip().rstrip(".;")
            m = re.match(r"^(?:(?:the\s+)?Respondent\s+No\.?\s*\d+\s+)?prays\s+that\s+(.*)", pt, re.IGNORECASE)
            if m:
                rest = m.group(1).strip()
                if re.search(r"be\s+dismissed", rest, re.IGNORECASE):
                    pt = re.sub(
                        r"(?:the\s+)?(?:Writ\s+Petition|Petition)\s+be\s+dismissed(?:\s+with\s+costs)?",
                        "dismiss the present Writ Petition with costs",
                        rest,
                        flags=re.IGNORECASE
                    )
                else:
                    pt = rest
            cleaned.append(pt)

        if len(cleaned) == 1 and "dismiss" in cleaned[0].lower():
            cleaned.append("refuse any interim or ad-interim relief sought by the Petitioner")
            cleaned.append("grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case")

        return cleaned

    @property
    def ordinal_date(self) -> str:
        """
        Convert date to ordinal format for the jurat.
        '5 September 2026' → '5th day of September 2026'
        """
        parts = self.attestation_date.split()
        if len(parts) >= 3:
            day = parts[0].rstrip("thstndrd")
            suffix = "th"
            if day in ("1", "21", "31"):
                suffix = "st"
            elif day in ("2", "22"):
                suffix = "nd"
            elif day in ("3", "23"):
                suffix = "rd"
            return f"{day}{suffix} day of {parts[1]} {parts[2]}"
        return self.attestation_date

