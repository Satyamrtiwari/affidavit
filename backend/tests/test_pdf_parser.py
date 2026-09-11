"""
Unit tests for PDF Parser service.
"""

import pytest
from pathlib import Path
from app.services.pdf_parser import clean_text, parse_pdf
from app.config import REFERENCE_DOCS_DIR


def test_clean_text_removes_broken_words():
    raw = "IN THE HIGH COUR T OF JUDICA TURE AT BOMBA Y"
    cleaned = clean_text(raw)
    assert "COURT" in cleaned
    assert "JUDICATURE" in cleaned
    assert "BOMBAY" in cleaned


def test_clean_text_normalizes_whitespace():
    raw = "Line 1    \n\n\n\n\nLine 2     with   spaces"
    cleaned = clean_text(raw)
    assert "Line 1\n\nLine 2 with spaces" == cleaned


def test_parse_pdf_reference_files():
    case_pdf = REFERENCE_DOCS_DIR / "03_Case_Information.pdf"
    if case_pdf.exists():
        text = parse_pdf(case_pdf)
        assert len(text) > 500
        assert "Sunrise Housing Private Limited" in text
        assert "Arvind Rajan" in text
