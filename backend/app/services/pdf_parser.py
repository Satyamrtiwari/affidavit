"""
Document Parser Service — Stage 1 of the pipeline.

Extracts text from PDF (via pdfplumber), Word DOCX (via python-docx),
and plain text files, and cleans the output for downstream processing.
Supports automatic format detection by file extension and magic bytes.
"""

import re
import logging
from pathlib import Path
from io import BytesIO
from typing import Union, Optional

import pdfplumber
import docx

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

    except Exception as e:
        logger.error(f"Failed to extract text from PDF: {e}")
        raise ValueError(f"Failed to extract text from PDF: {e}")


def extract_text_from_docx(source: Union[str, Path, bytes, BytesIO]) -> str:
    """
    Extract raw text from a DOCX Word document file.

    Args:
        source: File path (str/Path) or file bytes (bytes/BytesIO)

    Returns:
        Raw extracted text from paragraphs and tables.
    """
    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"DOCX file not found: {path}")
            doc = docx.Document(str(path))
        elif isinstance(source, bytes):
            doc = docx.Document(BytesIO(source))
        elif isinstance(source, BytesIO):
            doc = docx.Document(source)
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")

        text_chunks = []
        for p in doc.paragraphs:
            stripped = p.text.strip()
            if stripped:
                text_chunks.append(stripped)

        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    text_chunks.append(" | ".join(cells))

        if not text_chunks:
            raise ValueError("No text could be extracted from the DOCX file")

        raw_text = "\n".join(text_chunks)
        logger.info(f"Extracted {len(raw_text)} chars from DOCX")
        return raw_text

    except Exception as e:
        logger.error(f"Failed to extract text from DOCX: {e}")
        raise ValueError(f"Failed to extract text from DOCX: {e}")


def extract_text_from_txt(source: Union[str, Path, bytes, BytesIO]) -> str:
    """
    Extract text from a plain text file.
    """
    try:
        if isinstance(source, (str, Path)):
            with open(source, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif isinstance(source, bytes):
            return source.decode("utf-8", errors="replace")
        elif isinstance(source, BytesIO):
            return source.getvalue().decode("utf-8", errors="replace")
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")
    except Exception as e:
        logger.error(f"Failed to extract text from text file: {e}")
        raise ValueError(f"Failed to extract text from text file: {e}")


def parse_document(
    source: Union[str, Path, bytes, BytesIO],
    filename: Optional[str] = None
) -> str:
    """
    Universal document parser supporting PDF, DOCX, and TXT files.
    Automatically detects format from file extension or content magic bytes.

    Args:
        source: File path, bytes, or BytesIO
        filename: Optional original filename for extension detection

    Returns:
        Clean, LLM-ready text
    """
    ext = Path(filename).suffix.lower() if filename else ""
    if not ext and isinstance(source, (str, Path)):
        ext = Path(source).suffix.lower()

    if ext in (".docx", ".doc"):
        raw_text = extract_text_from_docx(source)
    elif ext == ".pdf":
        raw_text = extract_text_from_pdf(source)
    elif ext in (".txt", ".text", ".md"):
        raw_text = extract_text_from_txt(source)
    else:
        # Sniff magic bytes
        first_bytes = b""
        if isinstance(source, bytes):
            first_bytes = source[:8]
        elif isinstance(source, BytesIO):
            pos = source.tell()
            first_bytes = source.read(8)
            source.seek(pos)
        elif isinstance(source, (str, Path)) and Path(source).exists():
            with open(source, "rb") as f:
                first_bytes = f.read(8)

        if first_bytes.startswith(b"%PDF"):
            raw_text = extract_text_from_pdf(source)
        elif first_bytes.startswith(b"PK\x03\x04"):
            # Typical for DOCX zip container
            try:
                raw_text = extract_text_from_docx(source)
            except Exception:
                raw_text = extract_text_from_pdf(source)
        else:
            # Fallback attempts
            try:
                raw_text = extract_text_from_pdf(source)
            except Exception:
                try:
                    raw_text = extract_text_from_docx(source)
                except Exception:
                    raw_text = extract_text_from_txt(source)

    cleaned_text = clean_text(raw_text)
    return cleaned_text
def clean_text(raw_text: str) -> str:
    """
    Clean raw PDF-extracted text for LLM processing.

    Handles common PDF extraction artifacts:
    - Words split across lines (e.g., "JUDICA\nTURE" → "JUDICATURE")
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
    # Avoid merging valid English words/prepositions like OF, AT, IN, TO, A, etc.
    _STOP_WORDS = {"OF", "AT", "IN", "TO", "BY", "ON", "AS", "NO", "OR", "IF", "AN", "IS", "IT", "A", "I", "VS", "MR", "MS", "DR"}
    def _merge_pdf_word(match):
        p1, p2 = match.group(1), match.group(2)
        if p2 in _STOP_WORDS:
            return match.group(0)
        return p1 + p2

    text = re.sub(r'([A-Z]{2,})\s([A-Z]{1,2})(?=\s|[.,;:\n]|$)', _merge_pdf_word, text)

    # Fix "VERIFICA TION" style breaks (space before suffix in common legal words)
    common_fixes = {
        "BOMBA Y": "BOMBAY",
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


def parse_pdf(
    source: Union[str, Path, bytes, BytesIO],
    filename: Optional[str] = None
) -> str:
    """
    Extract and clean text from PDF or DOCX file.
    Maintained for full backward compatibility; delegates to parse_document.
    """
    return parse_document(source, filename=filename)
