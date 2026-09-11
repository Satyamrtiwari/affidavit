"""
Legal Document Generation & Evaluation Agent — FastAPI Application

Main entry point. Exposes API endpoints for:
- /api/generate  → Full pipeline: upload case info PDF → get affidavit + evaluation
- /api/extract-entities → Extract entities only (for inspection/debugging)
- /api/evaluate → Evaluate a previously generated document
- /api/health → Health check
"""

import json
import logging
import re
from pathlib import Path
from io import BytesIO
from contextlib import asynccontextmanager

from typing import Union, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import validate_config, APP_DEBUG, OUTPUTS_DIR
from app.services.pdf_parser import parse_pdf
from app.services.entity_extractor import extract_entities
from app.services.document_generator import generate_text, generate_docx, generate_all
from app.services.evaluator import evaluate_document

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if APP_DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate configuration on startup."""
    try:
        validate_config()
        logger.info("Configuration validated successfully")
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
    yield


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Legal Document Generation & Evaluation Agent",
    description=(
        "AI-powered system that generates Affidavit in Reply documents "
        "from case information and evaluates the output for accuracy, "
        "completeness, and consistency."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow React frontend (and any origin in dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    try:
        validate_config()
        return {"status": "healthy", "message": "All systems operational"}
    except ValueError as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "message": str(e)}
        )


# ─── Full Pipeline ────────────────────────────────────────────────────────────

@app.post("/api/generate")
async def generate_affidavit(
    case_info: UploadFile = File(..., description="Case Information PDF (mandatory)"),
    reference_doc: Union[UploadFile, str, None] = File(None, description="Reference Document PDF (optional)"),
):
    """
    Full pipeline: Upload case info PDF → Extract entities → Generate affidavit → Evaluate.

    Returns JSON with:
    - entities: The extracted structured entities
    - evaluation: The evaluation report with scores and issues
    - generated_text: The generated affidavit as plain text
    - docx_download_url: URL to download the .docx file
    """
    try:
        validate_config()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # ── Step 1: Parse PDFs ────────────────────────────────────────────────
    logger.info(f"Received case info: {case_info.filename}")

    try:
        case_bytes = await case_info.read()
        case_text = parse_pdf(case_bytes)
        logger.info(f"Parsed case info: {len(case_text)} chars")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to parse case information PDF: {e}"
        )

    reference_text = None
    if isinstance(reference_doc, UploadFile) and reference_doc.filename:
        try:
            ref_bytes = await reference_doc.read()
            if ref_bytes:
                reference_text = parse_pdf(ref_bytes)
                logger.info(f"Parsed reference doc: {len(reference_text)} chars")
        except Exception as e:
            logger.warning(f"Failed to parse reference doc, using default template: {e}")

    # ── Step 2: Extract Entities ──────────────────────────────────────────
    try:
        entities = extract_entities(case_text, reference_text)
        logger.info(f"Extracted {len(entities.reply_points)} reply points")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Entity extraction failed: {e}"
        )

    # ── Step 3: Generate Document ─────────────────────────────────────────
    try:
        raw_stem = Path(case_info.filename).stem if case_info.filename else "affidavit"
        clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_stem)
        docx_filename = f"generated_{clean_stem}.docx"
        report_json_filename = f"evaluation_report_{clean_stem}.json"
        report_md_filename = f"evaluation_report_{clean_stem}.md"

        plain_text, docx_buffer = generate_all(
            entities,
            save_docx=True,
            output_filename=docx_filename
        )

        # Also maintain default generated_affidavit.docx for backward compatibility
        with open(OUTPUTS_DIR / "generated_affidavit.docx", "wb") as f:
            f.write(docx_buffer.getvalue())

        logger.info(f"Document generated successfully: {docx_filename}")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Document generation failed: {e}"
        )

    # ── Step 4: Evaluate ──────────────────────────────────────────────────
    try:
        evaluation = evaluate_document(plain_text, entities)
        logger.info(f"Evaluation complete: {evaluation.overall_score}/100")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Evaluation failed: {e}"
        )

    # ── Step 5: Save Evaluation Report ────────────────────────────────────
    try:
        # Save custom named reports
        with open(OUTPUTS_DIR / report_json_filename, "w", encoding="utf-8") as f:
            f.write(evaluation.model_dump_json(indent=2))
        with open(OUTPUTS_DIR / report_md_filename, "w", encoding="utf-8") as f:
            f.write(evaluation.to_markdown())

        # Also maintain default evaluation_report files
        with open(OUTPUTS_DIR / "evaluation_report.json", "w", encoding="utf-8") as f:
            f.write(evaluation.model_dump_json(indent=2))
        with open(OUTPUTS_DIR / "evaluation_report.md", "w", encoding="utf-8") as f:
            f.write(evaluation.to_markdown())

        logger.info(f"Saved evaluation reports: {report_json_filename}, {report_md_filename}")
    except Exception as e:
        logger.warning(f"Failed to save evaluation reports: {e}")

    # ── Return Response ───────────────────────────────────────────────────
    return {
        "success": True,
        "filename": docx_filename,
        "entities": entities.model_dump(),
        "generated_text": plain_text,
        "evaluation": evaluation.model_dump(),
        "evaluation_markdown": evaluation.to_markdown(),
        "files": {
            "affidavit": docx_filename,
            "report_json": report_json_filename,
            "report_md": report_md_filename,
            "download_affidavit_url": f"/api/download/affidavit?filename={docx_filename}",
            "download_report_url": f"/api/download/report?filename={report_json_filename}",
        },
    }


# ─── Extract Entities Only ────────────────────────────────────────────────────

@app.post("/api/extract-entities")
async def extract_entities_endpoint(
    case_info: UploadFile = File(..., description="Case Information PDF"),
):
    """
    Extract structured entities from case info PDF without generating a document.
    Useful for debugging and inspecting the extraction quality.
    """
    try:
        validate_config()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        case_bytes = await case_info.read()
        case_text = parse_pdf(case_bytes)
        entities = extract_entities(case_text)
        return {
            "success": True,
            "entities": entities.model_dump(),
            "raw_text_length": len(case_text),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")


# ─── Download Generated DOCX ─────────────────────────────────────────────────

@app.get("/api/download/affidavit")
async def download_affidavit(filename: str = None):
    """Download the generated affidavit .docx file."""
    target_filename = Path(filename).name if filename else "generated_affidavit.docx"
    docx_path = OUTPUTS_DIR / target_filename

    if not docx_path.exists():
        docx_path = OUTPUTS_DIR / "generated_affidavit.docx"

    if not docx_path.exists():
        raise HTTPException(
            status_code=404,
            detail="No generated affidavit found. Run /api/generate first."
        )

    return StreamingResponse(
        open(docx_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename={target_filename}",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        },
    )


# ─── Download Evaluation Report ───────────────────────────────────────────────

@app.get("/api/download/report")
async def download_report(format: str = "json", filename: str = None):
    """Download the evaluation report in JSON or Markdown format."""
    if filename:
        target_filename = Path(filename).name
        report_path = OUTPUTS_DIR / target_filename
        media_type = "text/markdown" if target_filename.endswith(".md") else "application/json"
    elif format == "md":
        target_filename = "evaluation_report.md"
        report_path = OUTPUTS_DIR / target_filename
        media_type = "text/markdown"
    else:
        target_filename = "evaluation_report.json"
        report_path = OUTPUTS_DIR / target_filename
        media_type = "application/json"

    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail="No evaluation report found. Run /api/generate first."
        )

    return StreamingResponse(
        open(report_path, "rb"),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename={target_filename}",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        },
    )


# ─── Run Server ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
