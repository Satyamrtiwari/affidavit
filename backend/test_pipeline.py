"""
Test Script for Full Pipeline.
Runs PDF Parsing -> Groq Entity Extraction -> Document Generation -> Evaluation.
"""

import sys
import json
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import REFERENCE_DOCS_DIR, OUTPUTS_DIR, validate_config
from app.services.pdf_parser import parse_pdf
from app.services.entity_extractor import extract_entities
from app.services.document_generator import generate_all
from app.services.evaluator import evaluate_document

def run_test():
    print("=" * 60)
    print("Running Full Pipeline Test")
    print("=" * 60)

    validate_config()
    print("[OK] Config validated")

    case_pdf = REFERENCE_DOCS_DIR / "03_Case_Information.pdf"
    if not case_pdf.exists():
        raise FileNotFoundError(f"Case info PDF not found at {case_pdf}")

    print(f"\n[Stage 1] Parsing PDF: {case_pdf.name}...")
    cleaned_text = parse_pdf(case_pdf)
    print(f"[OK] Extracted {len(cleaned_text)} characters")

    print(f"\n[Stage 2] Extracting Entities using Groq...")
    entities = extract_entities(cleaned_text)
    print(f"[OK] Entities extracted:")
    print(f"  - Case: {entities.case_type} No. {entities.case_number} of {entities.year}")
    print(f"  - Court: {entities.forum_city}")
    print(f"  - Petitioner: {entities.petitioner.name}")
    print(f"  - Filing for: Respondent No. {entities.filing_respondent_number}")
    print(f"  - Deponent: {entities.deponent.name} ({entities.deponent.designation})")
    print(f"  - Org Representative: {entities.deponent.is_organisation_representative}")
    print(f"  - Reply Points: {len(entities.reply_points)}")

    print(f"\n[Stage 3] Generating Document (.docx & text)...")
    plain_text, docx_buf = generate_all(entities, save_docx=True)
    print(f"[OK] Generated text length: {len(plain_text)} chars")
    print(f"[OK] Saved .docx to: {OUTPUTS_DIR / 'generated_affidavit.docx'}")

    print(f"\n[Stage 4] Running Evaluator (Deterministic Checks)...")
    evaluation = evaluate_document(plain_text, entities)
    print(f"\n[OK] Overall Evaluation Score: {evaluation.overall_score} / 100")
    print(f"[OK] Checks Passed: {evaluation.checks_passed} / {evaluation.total_checks}")

    print("\n--- Dimension Scores ---")
    for ds in evaluation.dimension_scores:
        print(f"  - {ds.dimension:25s}: {ds.score:3d}/100 (Weight: {ds.weight}%, Weighted: {ds.weighted_score:.1f})")

    print("\n--- Validation Checks ---")
    for r in evaluation.validation_results:
        mark = "[PASS]" if r.passed else "[FAIL]"
        print(f"  {mark} {r.check_name}: {r.message}")
        if not r.passed and r.details:
            print(f"      Details: {r.details}")

    if evaluation.issues_detected:
        print("\n--- Issues Detected ---")
        for issue in evaluation.issues_detected:
            print(f"  ! {issue}")

    # Save outputs
    with open(OUTPUTS_DIR / "test_entities.json", "w", encoding="utf-8") as f:
        f.write(entities.model_dump_json(indent=2))
    with open(OUTPUTS_DIR / "test_generated_affidavit.txt", "w", encoding="utf-8") as f:
        f.write(plain_text)
    with open(OUTPUTS_DIR / "test_evaluation_report.json", "w", encoding="utf-8") as f:
        f.write(evaluation.model_dump_json(indent=2))
    with open(OUTPUTS_DIR / "test_evaluation_report.md", "w", encoding="utf-8") as f:
        f.write(evaluation.to_markdown())

    print(f"\n[OK] All outputs written to {OUTPUTS_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    run_test()
