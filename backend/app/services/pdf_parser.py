"""
PDF Parser Service — Stage 1 of the pipeline.

Extracts text from PDF files using pdfplumber and cleans the output
for downstream LLM processing.

pdfplumber is used over PyPDF2 because it preserves layout better
and handles tables/columns more reliably.
"""

import re
import logging
from pathlib import Path
from io import BytesIO
from typing import Union

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text_from_pdf(source: Union[str, Path, bytes, BytesIO]) -> str:
    """
    Extract raw text from a PDF file.

    Args:
        source: File path (str/Path) or file bytes (bytes/BytesIO)

    Returns:
        Raw extracted text from all pages concatenated.

    Raises:
        FileNotFoundError: If file path doesn't exist
        ValueError: If PDF cannot be parsed
    """
    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"PDF file not found: {path}")
            pdf = pdfplumber.open(path)
        elif isinstance(source, bytes):
            pdf = pdfplumber.open(BytesIO(source))
        elif isinstance(source, BytesIO):
            pdf = pdfplumber.open(source)
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")

        pages_text = []
        with pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text)
                    logger.debug(f"Page {i + 1}: extracted {len(text)} chars")
                else:
                    logger.warning(f"Page {i + 1}: no text extracted")

        if not pages_text:
            raise ValueError("No text could be extracted from the PDF")

        raw_text = "\n".join(pages_text)
        logger.info(f"Extracted {len(raw_text)} chars from {len(pages_text)} pages")
        return raw_text

    except pdfplumber.exceptions.PDFSyntaxError as e:
        raise ValueError(f"Invalid PDF file: {e}")


def clean_text(raw_text: str) -> str:
    """
    Clean raw PDF-extracted text for LLM processing.

    Handles common PDF extraction artifacts:
    - Words split across lines (e.g., "JUDICA\\nTURE" → "JUDICATURE")
    - Excessive whitespace
    - Broken words with spaces (e.g., "BOMBA Y" → "BOMBAY")
    - Normalise line endings

    Args:
        raw_text: Raw text from PDF extraction

    Returns:
        Cleaned text suitable for LLM processing
    """
    text = raw_text

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Normalize smart quotes and dashes
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")

    # Fix common PDF word-break artifacts where a space is inserted
    # before the last 1-2 characters of a word (e.g., "BOMBA Y" → "BOMBAY")
    # Pattern: uppercase word fragment + space + 1-2 uppercase chars at word boundary
    text = re.sub(r'([A-Z]{2,})\s([A-Z]{1,2})(?=\s|[.,;:\n]|$)', r'\1\2', text)

    # Fix "VERIFICA TION" style breaks (space before suffix in common legal words)
    common_fixes = {
        "JUDICA TURE": "JUDICATURE",
        "APPELLA TE": "APPELLATE",
        "VERIFICA TION": "VERIFICATION",
        "AFFIDA VIT": "AFFIDAVIT",
        "REPL Y": "REPLY",
        "ORDINAR Y": "ORDINARY",
        "PRA YER": "PRAYER",
        "COUR T": "COURT",
        "INFORMA TION": "INFORMATION",
        "EXHIBIT -": "EXHIBIT-",
        "Addr ess": "Address",
    }
    for broken, fixed in common_fixes.items():
        text = text.replace(broken, fixed)

    # Collapse multiple spaces into single space (but preserve newlines)
    text = re.sub(r'[^\S\n]+', ' ', text)

    # Collapse more than 2 consecutive newlines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip leading/trailing whitespace from each line
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    # Remove empty lines at start and end
    text = text.strip()

    logger.info(f"Cleaned text: {len(text)} chars")
    return text


def parse_pdf(source: Union[str, Path, bytes, BytesIO]) -> str:
    """
    Full pipeline: extract text from PDF and clean it.

    This is the main entry point for the PDF parser service.

    Args:
        source: PDF file path or bytes

    Returns:
        Clean, LLM-ready text
    """
    raw_text = extract_text_from_pdf(source)
    cleaned_text = clean_text(raw_text)
    return cleaned_text
