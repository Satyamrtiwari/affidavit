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

    @property
    def clean_through(self) -> Optional[str]:
        if not self.through:
            return None
        import re
        t = self.through.strip().rstrip('.')
        if re.match(r'^through\s+', t, re.IGNORECASE):
            t = t[7:].strip()
        return f"Through {t}"

    @property
    def clean_address(self) -> Optional[str]:
        if not self.address:
            return None
        return self.address.strip().rstrip('.')


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

    @property
    def clean_through(self) -> Optional[str]:
        if not self.through:
            return None
        import re
        t = self.through.strip().rstrip('.')
        if re.match(r'^through\s+', t, re.IGNORECASE):
            t = t[7:].strip()
        return f"Through {t}"

    @property
    def clean_address(self) -> Optional[str]:
        if not self.address:
            return None
        return self.address.strip().rstrip('.')


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

    @property
    def clean_address(self) -> str:
        return self.address.strip().rstrip('.')


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

    @property
    def clean_content(self) -> str:
        """
        Clean content by stripping leading 'I say that' and ensuring proper capitalization.
        """
        import re
        c = self.content.strip().rstrip(".")
        c = re.sub(r"^I\s+say\s+that,?\s*", "", c, flags=re.IGNORECASE).strip()
        if c:
            c = c[0].upper() + c[1:]
        return c

    @property
    def is_exhibit_already_annexed(self) -> bool:
        """Check if content already mentions 'annexed and marked as' or 'marked as EXHIBIT'."""
        import re
        return bool(re.search(r"(?:annexed\s+(?:hereto\s+)?and\s+marked\s+as|marked\s+as)\s+EXHIBIT", self.content, re.IGNORECASE))

    def format_for_display(self, entities: "CaseEntities") -> str:
        """
        Format the reply point cleanly:
        - If the content is already drafted as a complete first-person legal paragraph, preserve it faithfully.
        - Avoid unnecessary rule-based overwriting of case-specific facts.
        - If the content is a raw summary/stub, expand it into standard Bombay High Court phrasing.
        - Ensure proper exhibit annexure without duplicate mentions.
        """
        import re
        content = self.content.strip().rstrip('.')

        # Check if content already contains an exhibit marking
        has_exhibit_mention = bool(re.search(
            r'(?:annexed\s+(?:hereto\s+)?and\s+marked\s+as|marked\s+as)\s+EXHIBIT',
            content,
            re.IGNORECASE
        ))

        # Check if point already has full drafted text
        if self.move_type == "IDENTITY_AND_PERUSAL":
            if ("well acquainted" in content.lower() or "well conversant" in content.lower()) and ("competent" in content.lower() or "read the contents" in content.lower() or "perused" in content.lower()):
                text = content
            else:
                if entities.deponent.is_organisation_representative:
                    text = f"I say that I am the {entities.deponent.designation} of the Respondent No.{entities.filing_respondent_number} in the above {entities.case_type} and am well acquainted with the facts and circumstances of the case. I have perused the Petition and the documents annexed thereto and am competent to affirm this Affidavit in Reply."
                else:
                    text = f"I say that I am the Respondent No.{entities.filing_respondent_number} in the above {entities.case_type} and am well acquainted with the facts and circumstances of the case. I have perused the Petition and the documents annexed thereto and am competent to affirm this Affidavit in Reply."

        elif self.move_type == "BLANKET_DENIAL":
            if "save and except" in content.lower() and ("traversed" in content.lower() or "dismissed" in content.lower() or "specifically admitted" in content.lower()):
                text = content
            else:
                text = f"At the outset, I deny each and every allegation, contention and submission made in the {entities.case_type}, save and except those specifically admitted herein. I say that the Petition is misconceived, devoid of merits and is liable to be dismissed in limine."

        elif self.move_type == "CLOSING":
            if "dismissed" in content.lower() and ("article 226" in content.lower() or "in view of" in content.lower() or "premises" in content.lower()):
                text = content
            else:
                text = f"In the premises aforesaid, I say that the {entities.case_type} deserves to be dismissed with costs."

        elif self.move_type == "DOCUMENT_REFERENCE":
            if not re.match(r'^(I\s+say\s+that|I\s+crave\s+leave|Respondent\s+No)', content, re.IGNORECASE):
                c_text = content
                if c_text.startswith("The "):
                    c_text = "the " + c_text[4:]
                text = f"I say that {c_text}"
            else:
                text = content

        else:
            # Substantive Answer or Preliminary Position
            if re.match(r'^(I\s+say\s+that|I\s+deny|I\s+submit|With\s+reference\s+to)', content, re.IGNORECASE):
                text = content
            else:
                c_text = content
                if c_text.startswith("The "):
                    c_text = "the " + c_text[4:]
                text = f"I say that {c_text}"

        # Exhibit annexure
        if self.exhibit and not has_exhibit_mention:
            desc = self.exhibit.description
            for pfx in ('Copy of the ', 'Copy of ', 'copy of the ', 'copy of '):
                if desc.startswith(pfx):
                    desc = desc[len(pfx):]
            text = f"{text.rstrip('.')}. Hereto annexed and marked as {self.exhibit.label} is a copy of the {desc}."
        else:
            text = f"{text.rstrip('.')}."

        return text


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
        Strips preambles, converts third-person phrasing into direct action verbs,
        ensures lowercase initial letters, and prevents duplicate relief clauses.
        """
        import re
        cleaned = []
        for p in self.prayer_points:
            pt = p.strip().rstrip(".;")
            # Strip preambles like "It is respectfully prayed that this Hon'ble Court may be pleased to"
            pt = re.sub(
                r"^(?:It\s+is\s+respectfully\s+prayed\s+that\s+this\s+Hon'?ble\s+Court\s+may\s+be\s+pleased\s+to\s+)",
                "",
                pt,
                flags=re.IGNORECASE
            ).strip()

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

            if pt:
                # Lowercase first character for clean sub-clause formatting
                pt = pt[0].lower() + pt[1:]
                cleaned.append(pt)

        # Append standard High Court prayers if not already present
        has_interim = any("interim" in c.lower() for c in cleaned)
        has_general = any("other and further" in c.lower() or "fit and proper" in c.lower() for c in cleaned)

        if not has_interim:
            cleaned.append("refuse any interim or ad-interim relief sought by the Petitioner")
        if not has_general:
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

