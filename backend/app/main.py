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
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.config import validate_config, APP_DEBUG, OUTPUTS_DIR
from app.services.pdf_parser import parse_pdf, parse_document
from app.services.entity_extractor import extract_entities
from app.services.document_generator import generate_text, generate_docx, generate_all
from app.services.evaluator import evaluate_document
from app.services.agents import LinearDocumentWorkflow

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if APP_DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy external library loggers
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ─── Latest Generated Case Tracker ───────────────────────────────────────────

# Track latest files for download endpoints
_latest_generated_files = {
    "stem": None,
    "docx": None,
    "report_json": None,
    "report_md": None,
}


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
    title="Affidavit Generator API",
    description="Automated legal affidavit generation and evaluation pipeline (DOCX & PDF support)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow React frontend (Render backend, Vercel frontend, and localhost)
cors_origins = [
    "https://affidavit-backend-czuu.onrender.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:8000",
    "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
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
    case_info: UploadFile = File(..., description="Case Information PDF or Word DOCX (mandatory)"),
    reference_doc: Union[UploadFile, str, None] = File(None, description="Reference Document PDF or Word DOCX (optional)"),
):
    """
    Full pipeline: Upload case info (PDF/DOCX) → Extract entities → Generate affidavit → Evaluate.

    Returns JSON with:
    - entities: The extracted structured entities
    - evaluation: The evaluation report with scores and issues
    - generated_text: The generated affidavit as plain text
    - docx_download_url: URL to download the .docx file
    - agent_trajectory: Multi-agent execution steps
    """
    try:
        validate_config()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # ── Step 1: Parse Documents (PDF / DOCX) ──────────────────────────────
    logger.info(f"Received case info: {case_info.filename}")

    try:
        case_bytes = await case_info.read()
        case_text = parse_document(case_bytes, filename=case_info.filename)
        logger.info(f"Parsed case info ({case_info.filename}): {len(case_text)} chars")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to parse case information document ({case_info.filename}): {e}"
        )

    reference_text = None
    if isinstance(reference_doc, UploadFile) and reference_doc.filename:
        try:
            ref_bytes = await reference_doc.read()
            if ref_bytes:
                reference_text = parse_document(ref_bytes, filename=reference_doc.filename)
                logger.info(f"Parsed reference doc ({reference_doc.filename}): {len(reference_text)} chars")
        except Exception as e:
            logger.warning(f"Failed to parse reference doc, using default template: {e}")

    # ── Linear Multi-Agent Workflow Execution ──────────────────────────────
    try:
        workflow = LinearDocumentWorkflow()
        result = await run_in_threadpool(
            workflow.run,
            case_text=case_text,
            reference_text=reference_text,
            filename=case_info.filename,
        )

        # Ensure latest generated files tracker is updated for download endpoints
        if "files" in result:
            raw_stem = Path(case_info.filename).stem if case_info.filename else "affidavit"
            clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_stem)
            _latest_generated_files["stem"] = clean_stem
            _latest_generated_files["docx"] = result["files"].get("affidavit")
            _latest_generated_files["report_json"] = result["files"].get("report_json")
            _latest_generated_files["report_md"] = result["files"].get("report_md")

        return result
    except Exception as e:
        logger.error(f"Multi-agent workflow failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Document generation workflow failed: {e}"
        )



# ─── Extract Entities Only ────────────────────────────────────────────────────

@app.post("/api/extract-entities")
async def extract_entities_endpoint(
    case_info: UploadFile = File(..., description="Case Information PDF or Word DOCX"),
):
    """
    Extract structured entities from case info (PDF or DOCX) without generating a document.
    Useful for debugging and inspecting the extraction quality.
    """
    try:
        validate_config()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        case_bytes = await case_info.read()
        case_text = parse_document(case_bytes, filename=case_info.filename)
        entities = await run_in_threadpool(extract_entities, case_text)
        return {
            "success": True,
            "entities": entities.model_dump(),
            "raw_text_length": len(case_text),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")


# ─── Helper Functions for File Downloads ──────────────────────────────────────

def _find_latest_output_file(pattern: str, fallback_name: str) -> Optional[str]:
    """
    Find the most recently modified file in OUTPUTS_DIR matching pattern,
    preferring specifically named files over generic fallback.
    """
    if not OUTPUTS_DIR.exists():
        return None

    matches = [
        f for f in OUTPUTS_DIR.glob(pattern)
        if f.is_file() and f.name != fallback_name
    ]
    if matches:
        matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return matches[0].name

    fallback_path = OUTPUTS_DIR / fallback_name
    if fallback_path.exists():
        return fallback_path.name
    return None


def _resolve_affidavit_filename(filename: Optional[str] = None) -> Optional[str]:
    """
    Resolve target DOCX filename.
    If no filename provided or blank:
      1. Use in-memory latest generated docx if present.
      2. Search filesystem for most recent 'generated_*.docx'.
      3. Fallback to 'generated_affidavit.docx'.
    If filename is provided:
      Handle exact match, 'case_3' -> 'generated_case_3.docx', etc.
    """
    clean_input = filename.strip() if filename else ""
    if not clean_input:
        latest = _latest_generated_files.get("docx")
        if latest and (OUTPUTS_DIR / latest).exists():
            return latest
        return _find_latest_output_file("generated_*.docx", "generated_affidavit.docx")

    target_name = Path(clean_input).name
    stem = Path(clean_input).stem
    candidates = [
        target_name,
        f"{stem}.docx",
        f"generated_{target_name}",
        f"generated_{stem}.docx",
    ]
    for cand in candidates:
        if (OUTPUTS_DIR / cand).exists():
            return cand

    return target_name


def _resolve_report_filename(format_type: str = "json", filename: Optional[str] = None) -> Optional[str]:
    """
    Resolve target evaluation report filename.
    If no filename provided or blank:
      1. Use in-memory latest generated report if present.
      2. Search filesystem for most recent 'evaluation_report_*.<ext>'.
      3. Fallback to 'evaluation_report.<ext>'.
    If filename is provided:
      Handle exact match, 'case_3' -> 'evaluation_report_case_3.json', etc.
    """
    clean_input = filename.strip() if filename else ""
    is_md = (format_type.lower() == "md") or (clean_input.endswith(".md"))
    ext = "md" if is_md else "json"
    fallback_name = f"evaluation_report.{ext}"
    latest_key = "report_md" if is_md else "report_json"

    if not clean_input:
        latest = _latest_generated_files.get(latest_key)
        if latest and (OUTPUTS_DIR / latest).exists():
            return latest
        return _find_latest_output_file(f"evaluation_report_*.{ext}", fallback_name)

    target_name = Path(clean_input).name
    stem = Path(clean_input).stem
    candidates = [
        target_name,
        f"{stem}.{ext}",
        f"evaluation_report_{target_name}",
        f"evaluation_report_{stem}.{ext}",
    ]
    for cand in candidates:
        if (OUTPUTS_DIR / cand).exists():
            return cand

    return target_name


# ─── Download Generated DOCX ─────────────────────────────────────────────────

@app.get("/api/download/affidavit")
async def download_affidavit(
    filename: Optional[str] = Query(
        None,
        description="Optional: filename to download. If omitted, automatically downloads the most recently generated affidavit (e.g. generated_case_3.docx)."
    )
):
    """
    Download the generated affidavit .docx file.
    Automatically resolves to the most recent generated case document without asking for a name.
    """
    resolved_filename = _resolve_affidavit_filename(filename)
    docx_path = (OUTPUTS_DIR / resolved_filename) if resolved_filename else None

    if not docx_path or not docx_path.exists():
        fallback_path = OUTPUTS_DIR / "generated_affidavit.docx"
        if fallback_path.exists():
            docx_path = fallback_path
            resolved_filename = "generated_affidavit.docx"
        else:
            raise HTTPException(
                status_code=404,
                detail="No generated affidavit found. Run /api/generate first."
            )

    return StreamingResponse(
        open(docx_path, "rb"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{resolved_filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        },
    )


# ─── Download Evaluation Report ───────────────────────────────────────────────

@app.get("/api/download/report")
async def download_report(
    format: str = Query("json", description="Report format: 'json' or 'md'"),
    filename: Optional[str] = Query(
        None,
        description="Optional: filename to download. If omitted, automatically downloads the most recently generated evaluation report."
    ),
):
    """
    Download the evaluation report in JSON or Markdown format.
    Automatically resolves to the most recent generated report without asking for a name.
    """
    resolved_filename = _resolve_report_filename(format, filename)
    report_path = (OUTPUTS_DIR / resolved_filename) if resolved_filename else None

    if not report_path or not report_path.exists():
        fallback_name = "evaluation_report.md" if format.lower() == "md" else "evaluation_report.json"
        fallback_path = OUTPUTS_DIR / fallback_name
        if fallback_path.exists():
            report_path = fallback_path
            resolved_filename = fallback_name
        else:
            raise HTTPException(
                status_code=404,
                detail="No evaluation report found. Run /api/generate first."
            )

    media_type = "text/markdown" if resolved_filename.endswith(".md") else "application/json"

    return StreamingResponse(
        open(report_path, "rb"),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{resolved_filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        },
    )


# ─── Run Server ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
