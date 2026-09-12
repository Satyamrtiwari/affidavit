"""
Evaluator Service — Stage 5 of the pipeline.

Runs 6 deterministic validation checks (NO LLM dependency) on the
generated affidavit text and produces a scored evaluation report.

These checks are the CORE requirement of the assignment:
"Runs at least three deterministic checks that do not depend on an LLM."

We implement 6 checks for thoroughness:
1. Respondent Number Consistency
2. Paragraph Range Match (Verification section)
3. Required Sections Present
4. Verb Agreement (Deponent clause ↔ Jurat)
5. Deponent-Respondent Type Match
6. Entity Accuracy (names, dates, case numbers)
"""

import re
import logging
from typing import Optional

from app.models.entities import CaseEntities
from app.models.evaluation import (
    ValidationResult,
    DimensionScore,
    EvaluationReport,
)

logger = logging.getLogger(__name__)

# ─── Scoring Configuration ────────────────────────────────────────────────────

SCORING_WEIGHTS = {
    "entity_accuracy": 20,
    "completeness": 20,
    "structure": 20,
    "consistency": 15,
    "template_fidelity": 15,
    "hallucination_check": 10,
}

# ─── Dynamic Required Sections ────────────────────────────────────────────────

def get_required_sections(entities: CaseEntities) -> list[tuple[str, str]]:
    """Build required section patterns dynamically from entities."""
    court_pattern = re.escape(entities.court_name)
    pet_label = re.escape(entities.petitioner_label)
    resp_label = re.escape(entities.respondent_label)
    return [
        ("Forum Heading", rf"IN THE {court_pattern}"),
        ("Jurisdiction", r"JURISDICTION"),
        ("Case Number", r"NO\.\s*\d+\s*OF\s*\d{4}"),
        ("Cause Title (Moving Party)", rf"\.\.\.{pet_label}"),
        ("Cause Title (VERSUS)", r"VERSUS"),
        ("Cause Title (Responding Party)", rf"\.\.\.{resp_label}"),
        ("Affidavit Title", rf"AFFIDAVIT IN REPLY ON BEHALF OF {resp_label.upper()} NO"),
        ("Deponent Clause", r"do hereby .+ and state as under"),
        ("Prayer", r"PRAYER"),
        ("Jurat", r"(Solemnly affirmed|Sworn) at"),
        ("Verification", r"VERIFICATION"),
    ]

# ─── Dynamic Fixed Legal Phrases ───────────────────────────────────────────

def get_required_phrases(entities: CaseEntities) -> list[tuple[str, ...]]:
    """Build required phrase checks dynamically from entities."""
    case_type = entities.case_type
    return [
        ("well acquainted with the facts and circumstances of the case", "well conversant with the facts", "well acquainted with the facts"),
        (f"perused the {case_type}", "perused the plaint", "read the contents of the", "perused a copy of the", "perused the Petition", "perused the"),
        ("competent to affirm this Affidavit in Reply", "duly authorised and well conversant", "competent to affirm", "competent and authorised", "competent to swear"),
        ("deny each and every allegation, contention and submission", "deny each and every allegation", "all allegations, averments, and submissions", "all allegations", "denied as false", "denied as if set out herein seriatim"),
        ("save and except those specifically admitted herein", "save and except what is specifically admitted", "save and except what is expressly admitted", "specifically traversed", "save and except"),
        ("misconceived, devoid of merits", "misconceived, barred by law, and devoid of merit", "not maintainable and is liable to be dismissed", "dismissed at the threshold", "devoid of merits", "devoid of merit", "misconceived"),
        ("In the premises aforesaid", "In view of the foregoing", "In view of the facts", "premises aforesaid", "in view of"),
        ("deserves to be dismissed with costs", "dismissed with costs", "dismissed with exemplary costs", "dismiss the above suit with costs", "dismiss the writ petition with costs", "dismissed in limine", "with costs"),
        ("true and correct to my knowledge and belief", "true and correct to my knowledge"),
        ("nothing material has been concealed therefrom", "nothing material has been concealed"),
    ]


# ─── Check 1: Respondent Number Consistency ───────────────────────────────────

def check_respondent_number_consistency(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that the respondent number is consistent throughout the document.
    All filing declarations (title, deponent clause, advocate footer) must use
    the filing respondent number, and all respondent mentions must correspond
    to valid respondents in the matter.
    """
    expected_number = entities.filing_respondent_number
    valid_respondents = {r.respondent_number for r in entities.respondents}
    valid_respondents.add(expected_number)

    errors = []

    # 1. Check Affidavit Title
    title_match = re.search(r'AFFIDAVIT\s+IN\s+REPLY\s+ON\s+BEHALF\s+OF\s+RESPONDENT\s+NO\.?\s*(\d+)', generated_text, re.IGNORECASE)
    if title_match and int(title_match.group(1)) != expected_number:
        errors.append(f"Affidavit title specifies Respondent No.{title_match.group(1)} instead of Respondent No.{expected_number}")

    # 2. Check Deponent Clause
    dep_match = re.search(r'Respondent\s+No\.?\s*(\d+)\s+above\s+named', generated_text, re.IGNORECASE)
    if dep_match and int(dep_match.group(1)) != expected_number:
        errors.append(f"Deponent clause specifies Respondent No.{dep_match.group(1)} instead of Respondent No.{expected_number}")

    # 3. Check Advocate Footer
    adv_match = re.search(r'Advocates?\s+for\s+(?:the\s+)?Respondent\s+No\.?\s*(\d+)', generated_text, re.IGNORECASE)
    if adv_match and int(adv_match.group(1)) != expected_number:
        errors.append(f"Advocate footer specifies Respondent No.{adv_match.group(1)} instead of Respondent No.{expected_number}")

    # 4. Check that all mentions in the body refer to valid respondents in the matter
    after_title = generated_text.split("AFFIDAVIT IN REPLY", 1)
    body_text = after_title[1] if len(after_title) > 1 else generated_text
    mentions = [int(m) for m in re.findall(r'Respondent\s+No\.?\s*(\d+)', body_text)]

    hallucinated = [m for m in mentions if m not in valid_respondents]
    if hallucinated:
        errors.append(f"Document mentions non-existent Respondent No.{', '.join(set(str(m) for m in hallucinated))} (case only has {', '.join(str(r) for r in sorted(valid_respondents))})")

    if errors:
        return ValidationResult(
            check_name="Respondent Number Consistency",
            check_id="respondent_number_consistency",
            passed=False,
            severity="HIGH",
            message="; ".join(errors),
            details="\n".join(errors),
            source_reference="Filing declarations and body paragraphs",
        )

    return ValidationResult(
        check_name="Respondent Number Consistency",
        check_id="respondent_number_consistency",
        passed=True,
        message=f"All {len(mentions)} respondent mentions correspond to valid respondents with Respondent No.{expected_number} as filing party",
    )


# ─── Check 2: Paragraph Range Match ──────────────────────────────────────────

def check_paragraph_range(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that the verification section's paragraph range matches the
    actual number of body paragraphs.
    
    If body has 7 paragraphs, verification must say "paragraphs 1 to 7".
    """
    expected_count = entities.paragraph_count

    # Count numbered paragraphs in the body (between deponent clause and PRAYER)
    body_match = re.search(
        r'state as under:(.*?)PRAYER',
        generated_text,
        re.DOTALL
    )

    actual_paras = []
    if body_match:
        body_section = body_match.group(1)
        actual_paras = re.findall(r'(?:^|\n)\s*(\d+)\.\s', body_section)

    actual_count = len(actual_paras)

    # Extract the stated range from verification
    verification_match = re.search(
        r'paragraphs\s+(\d+)\s+to\s+(\d+)',
        generated_text,
        re.IGNORECASE
    )

    if not verification_match:
        return ValidationResult(
            check_name="Paragraph Range Match",
            check_id="paragraph_range_match",
            passed=False,
            severity="HIGH",
            message="Could not find paragraph range in verification section.",
            source_reference="Verification section",
        )

    stated_start = int(verification_match.group(1))
    stated_end = int(verification_match.group(2))

    errors = []
    if stated_start != 1:
        errors.append(f"Range starts at {stated_start}, expected 1")
    if stated_end != expected_count:
        errors.append(
            f"Range ends at {stated_end}, but entity model has {expected_count} reply points"
        )
    if actual_count > 0 and stated_end != actual_count:
        errors.append(
            f"Range ends at {stated_end}, but body contains {actual_count} numbered paragraphs"
        )

    if errors:
        return ValidationResult(
            check_name="Paragraph Range Match",
            check_id="paragraph_range_match",
            passed=False,
            severity="HIGH",
            message=f"Verification paragraph range mismatch: {'; '.join(errors)}",
            details=f"Stated: paragraphs {stated_start} to {stated_end}, "
                    f"Expected: paragraphs 1 to {expected_count}, "
                    f"Actual body paragraphs found: {actual_count}",
            source_reference="Verification section",
        )

    return ValidationResult(
        check_name="Paragraph Range Match",
        check_id="paragraph_range_match",
        passed=True,
        message=f"Verification correctly states 'paragraphs 1 to {stated_end}'",
    )


# ─── Check 3: Required Sections Present ──────────────────────────────────────

def check_required_sections(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that all required sections of the affidavit are present.
    Uses dynamic patterns based on the actual court and party labels.
    """
    required_sections = get_required_sections(entities)
    missing = []
    found = []

    for section_name, pattern in required_sections:
        if re.search(pattern, generated_text, re.IGNORECASE):
            found.append(section_name)
        else:
            missing.append(section_name)

    if missing:
        return ValidationResult(
            check_name="Required Sections Present",
            check_id="required_sections_present",
            passed=False,
            severity="HIGH",
            message=f"Missing {len(missing)} required section(s): {', '.join(missing)}",
            details=f"Found {len(found)}/{len(required_sections)} sections",
            source_reference="Entire document",
        )

    return ValidationResult(
        check_name="Required Sections Present",
        check_id="required_sections_present",
        passed=True,
        message=f"All {len(required_sections)} required sections are present",
    )


# ─── Check 4: Verb Agreement ─────────────────────────────────────────────────

def check_verb_agreement(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that the deponent clause verb matches the jurat verb.
    
    'solemnly affirm' in clause → 'Solemnly affirmed' in jurat
    'swear and affirm' in clause → 'Sworn' in jurat
    """
    verb = entities.deponent.verification_verb
    expected_jurat = entities.jurat_verb

    # Check deponent clause contains the verb
    clause_pattern = rf'do hereby {re.escape(verb)}'
    has_clause_verb = bool(re.search(clause_pattern, generated_text, re.IGNORECASE))

    # Check jurat contains the matching past tense
    jurat_pattern = rf'{re.escape(expected_jurat)} at'
    has_jurat_verb = bool(re.search(jurat_pattern, generated_text, re.IGNORECASE))

    errors = []
    if not has_clause_verb:
        errors.append(f"Deponent clause does not contain 'do hereby {verb}'")
    if not has_jurat_verb:
        errors.append(f"Jurat does not contain '{expected_jurat} at'")

    # Cross-check: if clause says "affirm" but jurat says "Sworn" (mismatch)
    if "solemnly affirm" in verb.lower():
        if re.search(r'\bSworn\b', generated_text):
            errors.append(
                f"Verb mismatch: clause uses '{verb}' but jurat uses 'Sworn' "
                f"(should be '{expected_jurat}')"
            )
    elif "swear" in verb.lower():
        if re.search(r'Solemnly affirmed', generated_text, re.IGNORECASE):
            errors.append(
                f"Verb mismatch: clause uses '{verb}' but jurat uses 'Solemnly affirmed' "
                f"(should be '{expected_jurat}')"
            )

    if errors:
        return ValidationResult(
            check_name="Verb Agreement",
            check_id="verb_agreement",
            passed=False,
            severity="HIGH",
            message=f"Verb agreement error: {'; '.join(errors)}",
            details=f"Expected: clause='{verb}' → jurat='{expected_jurat}'",
            source_reference="Deponent clause and Jurat section",
        )

    return ValidationResult(
        check_name="Verb Agreement",
        check_id="verb_agreement",
        passed=True,
        message=f"Verb agreement correct: '{verb}' → '{expected_jurat}'",
    )


# ─── Check 5: Deponent-Respondent Type Match ─────────────────────────────────

def check_deponent_type_match(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that the deponent clause correctly reflects whether the
    respondent is a person or an organisation.
    
    - Person: "the Respondent No.X above named"
    - Organisation officer: "the [Designation] of the Respondent No.X above named"
    """
    dep = entities.deponent
    resp = entities.filing_respondent
    is_org = dep.is_organisation_representative

    # Look at the deponent clause (between the name and "state as under")
    clause_match = re.search(
        rf'I,\s*{re.escape(dep.name)}.*?state as under',
        generated_text,
        re.DOTALL | re.IGNORECASE
    )

    if not clause_match:
        return ValidationResult(
            check_name="Deponent-Respondent Type Match",
            check_id="deponent_type_match",
            passed=False,
            severity="MEDIUM",
            message="Could not locate deponent clause to check type match.",
        )

    clause_text = clause_match.group(0)

    if is_org:
        # Should contain "the [Designation] of the Respondent No.X"
        designation = dep.designation or ""
        expected_phrase = f"of the Respondent No.{entities.filing_respondent_number}"

        if expected_phrase.lower() not in clause_text.lower():
            return ValidationResult(
                check_name="Deponent-Respondent Type Match",
                check_id="deponent_type_match",
                passed=False,
                severity="HIGH",
                message=(
                    f"Respondent No.{entities.filing_respondent_number} is an organisation, "
                    f"but the deponent clause doesn't use the 'of the Respondent No.X' pattern. "
                    f"An officer must file 'on behalf of' the organisation."
                ),
                details=f"Expected phrase: '{designation} of the Respondent No.{entities.filing_respondent_number}'",
                source_reference="Deponent clause",
            )

        # Should NOT say "I am the Respondent No.X" in paragraph 1
        bad_pattern = rf'I\s+am\s+the\s+Respondent\s+No\.?\s*{entities.filing_respondent_number}\s+in'
        if re.search(bad_pattern, generated_text, re.IGNORECASE):
            return ValidationResult(
                check_name="Deponent-Respondent Type Match",
                check_id="deponent_type_match",
                passed=False,
                severity="HIGH",
                message=(
                    f"Paragraph 1 says 'I am the Respondent No.{entities.filing_respondent_number}' "
                    f"but the respondent is an organisation. Should say "
                    f"'I am the {designation} of the Respondent No.{entities.filing_respondent_number}'."
                ),
                source_reference="Paragraph 1 (Identity and Perusal)",
            )
    else:
        # Person respondent — should NOT have "of the Respondent" pattern
        # (which implies filing for someone else)
        of_pattern = rf'{dep.designation or "XXX_NO_MATCH"}\s+of\s+the\s+Respondent'
        if dep.designation and re.search(of_pattern, clause_text, re.IGNORECASE):
            return ValidationResult(
                check_name="Deponent-Respondent Type Match",
                check_id="deponent_type_match",
                passed=False,
                severity="MEDIUM",
                message=(
                    f"The deponent IS the respondent (individual), but the clause "
                    f"uses 'of the Respondent' pattern (which implies filing for an org)."
                ),
                source_reference="Deponent clause",
            )

    return ValidationResult(
        check_name="Deponent-Respondent Type Match",
        check_id="deponent_type_match",
        passed=True,
        message=(
            f"Deponent type correctly matches: "
            f"{'Organisation representative' if is_org else 'Individual respondent'}"
        ),
    )


# ─── Check 6: Entity Accuracy ────────────────────────────────────────────────

def check_entity_accuracy(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that key entities from the source case information appear
    correctly in the generated document.
    
    Checks: deponent name, petitioner name, case number, year,
    forum city, attestation place, respondent names.
    """
    errors = []
    checked = 0

    # Check critical entities
    checks = [
        ("Deponent name", entities.deponent.name),
        ("Petitioner name", entities.petitioner.name),
        ("Case number", entities.case_number),
        ("Year", entities.year),
        ("Forum city", entities.forum_city),
        ("Attestation place", entities.attestation_place),
    ]

    # Add respondent names
    for resp in entities.respondents:
        checks.append((f"Respondent {resp.respondent_number} name", resp.name))

    # Add advocate firm if present
    if entities.advocate_firm:
        checks.append(("Advocate firm", entities.advocate_firm))

    for label, expected_value in checks:
        checked += 1
        clean_expected = expected_value.strip().rstrip('.').lower()
        if clean_expected not in generated_text.lower():
            errors.append(f"{label}: '{expected_value}' not found in generated document")

    if errors:
        return ValidationResult(
            check_name="Entity Accuracy",
            check_id="entity_accuracy",
            passed=False,
            severity="HIGH",
            message=f"Entity accuracy errors: {len(errors)} entity/entities missing or incorrect",
            details="\n".join(errors),
            source_reference="Entire document vs source case information",
        )

    return ValidationResult(
        check_name="Entity Accuracy",
        check_id="entity_accuracy",
        passed=True,
        message=f"All {checked} critical entities verified present in the document",
    )


# ─── Check for Required Fixed Phrases ─────────────────────────────────────────

def check_template_fidelity(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that required fixed legal phrases are preserved in the document.
    Uses dynamic phrases based on the actual case type.
    """
    required_phrases = get_required_phrases(entities)
    missing = []
    found = 0

    for item in required_phrases:
        if isinstance(item, tuple):
            matched = any(p.lower() in generated_text.lower() for p in item)
            display_phrase = item[0]
        else:
            matched = item.lower() in generated_text.lower()
            display_phrase = item

        if matched:
            found += 1
        else:
            missing.append(display_phrase)

    total = len(required_phrases)
    fidelity_pct = (found / total * 100) if total > 0 else 0

    if missing:
        return ValidationResult(
            check_name="Template Fidelity (Fixed Phrases)",
            check_id="template_fidelity",
            passed=fidelity_pct >= 80,  # Allow up to 20% missing
            severity="MEDIUM" if fidelity_pct >= 60 else "HIGH",
            message=f"Template fidelity: {found}/{total} fixed phrases present ({fidelity_pct:.0f}%)",
            details=f"Missing phrases:\n" + "\n".join(f"- {p}" for p in missing),
            source_reference="Fixed legal phrases from format guide",
        )

    return ValidationResult(
        check_name="Template Fidelity (Fixed Phrases)",
        check_id="template_fidelity",
        passed=True,
        message=f"All {total} required fixed phrases are present",
    )


# ─── Check 8: Party Label Consistency (Generated Body Text) ──────────────────

def check_party_label_consistency(
    generated_text: str, entities: CaseEntities
) -> ValidationResult:
    """
    Verify that the generated body text (between deponent clause and PRAYER)
    consistently uses the expected party label (e.g. Defendant vs Respondent)
    and does not leak contradictory terminology.
    """
    expected_label = entities.respondent_label
    if expected_label == "Defendant":
        wrong_label = "Respondent"
    elif expected_label == "Respondent":
        wrong_label = "Defendant"
    else:
        return ValidationResult(
            check_name="Party Label Consistency",
            check_id="party_label_consistency",
            passed=True,
            message=f"Non-standard respondent label '{expected_label}'; skipped opposition check",
        )

    # Scan body text between deponent clause ("state as under:") and "PRAYER"
    body_match = re.search(
        r'state as under:(.*?)PRAYER',
        generated_text,
        re.DOTALL | re.IGNORECASE
    )
    body_text = body_match.group(1) if body_match else generated_text

    pattern = rf'\b{re.escape(wrong_label)}\b'
    matches = list(re.finditer(pattern, body_text, re.IGNORECASE))

    if matches:
        return ValidationResult(
            check_name="Party Label Consistency",
            check_id="party_label_consistency",
            passed=False,
            severity="HIGH",
            message=(
                f"Generated body text uses '{wrong_label}' ({len(matches)} occurrence(s)) "
                f"but respondent_label is '{expected_label}' — conflicting terminology in draft"
            ),
            details=f"Found '{wrong_label}' in body text between deponent clause and PRAYER when expected label is '{expected_label}'",
            source_reference="Generated body paragraphs (deponent clause to PRAYER)",
        )

    return ValidationResult(
        check_name="Party Label Consistency",
        check_id="party_label_consistency",
        passed=True,
        message=f"Party label '{expected_label}' used consistently; no conflicting '{wrong_label}' mentions in body",
    )


# ─── Scoring Engine ───────────────────────────────────────────────────────────

def _calculate_dimension_scores(
    results: list[ValidationResult],
    entities: CaseEntities,
) -> list[DimensionScore]:
    """
    Calculate per-dimension scores from validation results.
    
    Each dimension is scored 0-100 based on relevant check results.
    """
    # Map checks to dimensions
    check_dimension_map = {
        "entity_accuracy": ["entity_accuracy"],
        "completeness": ["required_sections_present", "paragraph_range_match"],
        "structure": ["required_sections_present"],
        "consistency": ["respondent_number_consistency", "verb_agreement", "party_label_consistency"],
        "template_fidelity": ["template_fidelity", "deponent_type_match"],
        "hallucination_check": ["entity_accuracy"],
    }

    results_by_id = {r.check_id: r for r in results}
    dimension_scores = []

    dimension_display_names = {
        "entity_accuracy": "Entity Accuracy",
        "completeness": "Completeness",
        "structure": "Structure",
        "consistency": "Consistency",
        "template_fidelity": "Template Fidelity",
        "hallucination_check": "Hallucination Check",
    }

    for dim_key, check_ids in check_dimension_map.items():
        weight = SCORING_WEIGHTS[dim_key]
        relevant_checks = [results_by_id[cid] for cid in check_ids if cid in results_by_id]

        if not relevant_checks:
            score = 100  # No checks applicable → assume pass
        else:
            passed = sum(1 for r in relevant_checks if r.passed)
            total = len(relevant_checks)
            score = int((passed / total) * 100)

            # Severity-based penalty for failed checks
            for r in relevant_checks:
                if not r.passed:
                    if r.severity == "HIGH":
                        score = max(0, score - 20)
                    elif r.severity == "MEDIUM":
                        score = max(0, score - 10)

        weighted = score * weight / 100
        notes_parts = []
        for r in relevant_checks:
            status = "[PASS]" if r.passed else "[FAIL]"
            notes_parts.append(f"{status} {r.check_name}")

        dimension_scores.append(DimensionScore(
            dimension=dimension_display_names[dim_key],
            score=score,
            max_score=100,
            weight=weight,
            weighted_score=round(weighted, 1),
            notes="; ".join(notes_parts) if notes_parts else "No specific checks",
        ))

    return dimension_scores


# ─── Main Evaluation Function ─────────────────────────────────────────────────

def evaluate_document(
    generated_text: str,
    entities: CaseEntities,
) -> EvaluationReport:
    """
    Run all validation checks and produce the evaluation report.

    This is the main entry point for the evaluation stage.

    Args:
        generated_text: The generated affidavit as plain text
        entities: The source CaseEntities used for generation

    Returns:
        EvaluationReport with scores, check results, and issues
    """
    logger.info("Starting document evaluation...")

    # Run all 8 deterministic checks
    results = [
        check_respondent_number_consistency(generated_text, entities),
        check_paragraph_range(generated_text, entities),
        check_required_sections(generated_text, entities),
        check_verb_agreement(generated_text, entities),
        check_deponent_type_match(generated_text, entities),
        check_entity_accuracy(generated_text, entities),
        check_template_fidelity(generated_text, entities),
        check_party_label_consistency(generated_text, entities),
    ]

    # Log results
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        logger.info(f"  [{status}] {r.check_name}: {r.message}")

    # Calculate dimension scores
    dimension_scores = _calculate_dimension_scores(results, entities)

    # Calculate overall score
    overall_score = sum(ds.weighted_score for ds in dimension_scores)

    # Compile issues list
    issues = []
    for r in results:
        if not r.passed:
            issue = f"[{r.severity}] {r.check_name}: {r.message}"
            if r.source_reference:
                issue += f" (Source: {r.source_reference})"
            issues.append(issue)

    # Statistics
    checks_passed = sum(1 for r in results if r.passed)
    checks_failed = sum(1 for r in results if not r.passed)

    # Methodology explanation
    methodology = (
        "Scoring is based on 6 weighted dimensions, each scored 0-100:\n"
        + "\n".join(
            f"- {ds.dimension}: weight {ds.weight}%"
            for ds in dimension_scores
        )
        + "\n\nOverall score = sum of (dimension_score × weight/100) for all dimensions.\n"
        + f"All {len(results)} checks are deterministic (no LLM dependency).\n"
        + "HIGH severity failures incur a 20-point penalty on the dimension score.\n"
        + "MEDIUM severity failures incur a 10-point penalty."
    )

    report = EvaluationReport(
        overall_score=round(overall_score, 1),
        dimension_scores=dimension_scores,
        validation_results=results,
        checks_passed=checks_passed,
        checks_failed=checks_failed,
        total_checks=len(results),
        issues_detected=issues,
        scoring_methodology=methodology,
    )

    logger.info(
        f"Evaluation complete: {overall_score:.1f}/100 "
        f"({checks_passed}/{len(results)} checks passed)"
    )

    return report
