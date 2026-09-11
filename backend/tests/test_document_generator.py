"""
Unit tests for Document Generator service.
"""

import pytest
from app.services.document_generator import generate_text, generate_docx
from app.models.entities import CaseEntities, DeponentInfo


def test_generate_text_structure(sample_entities: CaseEntities):
    text = generate_text(sample_entities)

    # Check key sections are present
    assert "IN THE HIGH COURT OF JUDICATURE AT BOMBAY" in text
    assert "ORDINARY ORIGINAL CIVIL JURISDICTION" in text
    assert "WRIT PETITION NO. 1847 OF 2026" in text
    assert "Sunrise Housing Private Limited" in text
    assert "AFFIDAVIT IN REPLY ON BEHALF OF RESPONDENT NO. 2" in text
    assert "PRAYER" in text
    assert "Solemnly affirmed at Mumbai" in text
    assert "VERIFICATION" in text
    assert "Rajan & Associates" in text


def test_deponent_organisation_vs_individual(sample_entities: CaseEntities):
    # Org representative
    text_org = generate_text(sample_entities)
    assert "the Deputy Metropolitan Commissioner of the Respondent No.2 above named" in text_org

    # Individual deponent
    sample_entities.deponent.is_organisation_representative = False
    sample_entities.deponent.age = "45 years"
    sample_entities.deponent.occupation = "Business"
    text_ind = generate_text(sample_entities)
    assert "the Respondent No.2 above named" in text_ind
    assert "Age 45 years" in text_ind


def test_generate_docx(sample_entities: CaseEntities):
    buffer = generate_docx(sample_entities)
    assert buffer is not None
    assert buffer.getbuffer().nbytes > 1000
