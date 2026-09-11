"""
Pytest configuration and shared fixtures.
"""

import sys
from pathlib import Path
import pytest

# Ensure backend is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.entities import (
    CaseEntities,
    PartyInfo,
    RespondentInfo,
    DeponentInfo,
    ReplyPoint,
    ExhibitInfo,
)


@pytest.fixture
def sample_entities() -> CaseEntities:
    """Fixture returning a standard CaseEntities model for testing."""
    return CaseEntities(
        forum_city="BOMBAY",
        jurisdiction_type="ORDINARY ORIGINAL CIVIL JURISDICTION",
        case_type="WRIT PETITION",
        case_number="1847",
        year="2026",
        petitioner=PartyInfo(name="Sunrise Housing Private Limited"),
        respondents=[
            RespondentInfo(name="State of Maharashtra", respondent_number=1, is_organisation=True),
            RespondentInfo(
                name="Mumbai Metropolitan Region Development Authority",
                respondent_number=2,
                is_organisation=True,
            ),
        ],
        filing_respondent_number=2,
        deponent=DeponentInfo(
            name="Arvind Rajan",
            is_organisation_representative=True,
            designation="Deputy Metropolitan Commissioner",
            organisation="Mumbai Metropolitan Region Development Authority",
            address="Bandra East, Mumbai, Maharashtra",
            verification_verb="solemnly affirm",
        ),
        reply_points=[
            ReplyPoint(
                point_number=1,
                move_type="IDENTITY_AND_PERUSAL",
                title="Filing of Affidavit",
                content="I say that I am the Deputy Metropolitan Commissioner of the Respondent No.2...",
            ),
            ReplyPoint(
                point_number=2,
                move_type="BLANKET_DENIAL",
                title="General Denial",
                content="At the outset, I deny each and every allegation...",
            ),
            ReplyPoint(
                point_number=3,
                move_type="PRELIMINARY_POSITION",
                title="Preliminary Position",
                content="The Writ Petition is misconceived and devoid of merits",
            ),
            ReplyPoint(
                point_number=4,
                move_type="SUBSTANTIVE_ANSWER",
                title="Denial Regarding Communication",
                content="Respondent No. 2 denies that the communication was issued without authority",
            ),
            ReplyPoint(
                point_number=5,
                move_type="SUBSTANTIVE_ANSWER",
                title="Authority for Communication",
                content="The communication was issued pursuant to redevelopment procedure",
            ),
            ReplyPoint(
                point_number=6,
                move_type="DOCUMENT_REFERENCE",
                title="Document Relied Upon",
                content="Respondent No. 2 relies upon the communication dated 15 July 2026",
                exhibit=ExhibitInfo(
                    label="EXHIBIT-'A'",
                    description="communication dated 15 July 2026",
                ),
            ),
        ],
        prayer_points=[
            "Respondent No. 2 prays that the Writ Petition be dismissed with costs."
        ],
        attestation_place="Mumbai",
        attestation_date="5 September 2026",
        advocate_firm="Rajan & Associates",
        advocate_for="Respondent No. 2",
    )
