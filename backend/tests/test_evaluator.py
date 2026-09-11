"""
Unit tests for Evaluator service (deterministic validation checks & scoring).
"""

import pytest
from app.models.entities import CaseEntities
from app.services.document_generator import generate_text
from app.services.evaluator import (
    evaluate_document,
    check_respondent_number_consistency,
    check_paragraph_range,
    check_required_sections,
    check_verb_agreement,
    check_deponent_type_match,
    check_entity_accuracy,
    check_template_fidelity,
)


def test_evaluator_all_pass(sample_entities: CaseEntities):
    text = generate_text(sample_entities)
    report = evaluate_document(text, sample_entities)

    assert report.overall_score >= 85.0
    assert report.checks_passed == 7
    assert report.checks_failed == 0
    assert len(report.issues_detected) == 0


def test_check_respondent_number_consistency_failure(sample_entities: CaseEntities):
    text = generate_text(sample_entities)
    # Corrupt respondent number in body
    corrupted_text = text.replace("Respondent No.2", "Respondent No.3")
    res = check_respondent_number_consistency(corrupted_text, sample_entities)
    assert res.passed is False
    assert res.severity == "HIGH"


def test_check_paragraph_range_failure(sample_entities: CaseEntities):
    text = generate_text(sample_entities)
    # Corrupt paragraph range in verification
    corrupted_text = text.replace("paragraphs 1 to 6", "paragraphs 1 to 15")
    res = check_paragraph_range(corrupted_text, sample_entities)
    assert res.passed is False
    assert res.severity == "HIGH"


def test_check_verb_agreement_failure(sample_entities: CaseEntities):
    text = generate_text(sample_entities)
    # Jurat mismatch: change Solemnly affirmed to Sworn
    corrupted_text = text.replace("Solemnly affirmed at", "Sworn at")
    res = check_verb_agreement(corrupted_text, sample_entities)
    assert res.passed is False
    assert res.severity == "HIGH"


def test_check_required_sections_failure(sample_entities: CaseEntities):
    text = generate_text(sample_entities)
    # Remove VERIFICATION section
    corrupted_text = text.replace("VERIFICATION", "SOME_OTHER_TITLE")
    res = check_required_sections(corrupted_text, sample_entities)
    assert res.passed is False
    assert "Verification" in res.message
