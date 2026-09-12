"""
Linear Multi-Agent Architecture for Legal Document Generation & Evaluation.

Implements 5 specialized agents executing in a strict, deterministic sequence:
1. ExtractorAgent           — LLM entity extraction into structured CaseEntities
2. PreGenerationGuardAgent  — Deterministic pre-drafting validation (zero LLM calls)
3. DrafterAgent             — Deterministic compilation to text & DOCX via Jinja2
4. EvaluatorAgent           — Deterministic 8-check compliance audit & scoring
5. ExporterAgent            — Dynamic case-stem file naming, persistence & export packaging
"""

import time
import re
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from io import BytesIO
from typing import Optional, Any

from app.config import OUTPUTS_DIR
from app.models.entities import CaseEntities
from app.models.evaluation import EvaluationReport
from app.services.entity_extractor import extract_entities
from app.services.document_generator import generate_all
from app.services.evaluator import evaluate_document

logger = logging.getLogger(__name__)


# ─── 1. AgentStep & WorkflowState ─────────────────────────────────────────────

@dataclass
class AgentStep:
    agent_name: str
    status: str  # "success" | "failure"
    duration_ms: float
    summary: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowState:
    case_text: str
    reference_text: Optional[str] = None
    filename: Optional[str] = None
    entities: Optional[CaseEntities] = None
    pre_guard_failures: list[str] = field(default_factory=list)
    plain_text: Optional[str] = None
    docx_buffer: Optional[BytesIO] = None
    evaluation_report: Optional[EvaluationReport] = None
    exported_files: dict[str, str] = field(default_factory=dict)
    trajectory: list[AgentStep] = field(default_factory=list)


# ─── 2. ExtractorAgent ────────────────────────────────────────────────────────

class ExtractorAgent:
    """
    Agent 1: Legal Information Extraction.
    Thin wrapper calling the existing entity_extractor.extract_entities() function.
    """
    NAME = "ExtractorAgent"

    @classmethod
    def run(cls, state: WorkflowState) -> WorkflowState:
        start_time = time.perf_counter()
        try:
            entities = extract_entities(state.case_text, state.reference_text)
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state.entities = entities

            summary = (
                f"Extracted {len(entities.reply_points)} reply points for "
                f"{entities.respondent_label} No. {entities.filing_respondent_number}"
            )
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="success",
                duration_ms=elapsed_ms,
                summary=summary,
                details={
                    "court_name": entities.court_name,
                    "case_type": entities.case_type,
                    "case_number": entities.case_number,
                    "year": entities.year,
                    "reply_points_count": len(entities.reply_points),
                    "filing_respondent_number": entities.filing_respondent_number,
                }
            ))
            logger.info(f"[{cls.NAME}] {summary} ({elapsed_ms}ms)")
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="failure",
                duration_ms=elapsed_ms,
                summary=f"Extraction failed: {e}",
                details={"error": str(e)}
            ))
            logger.error(f"[{cls.NAME}] Extraction failed: {e}")
            raise
        return state


# ─── 3. PreGenerationGuardAgent ───────────────────────────────────────────────

class PreGenerationGuardAgent:
    """
    Agent 2: Pre-Generation Guard.
    Pure Python, zero LLM calls, operating on the extracted CaseEntities.
    Runs 5 deterministic integrity checks before drafting begins:
      - Check A: Filing Party Existence
      - Check B: Deponent Capacity
      - Check C: Reply Points Non-Empty
      - Check D: Verification Verb Agreement
      - Check E: Party Label Integrity (regex word-boundary scan on reply points)
    """
    NAME = "PreGenerationGuardAgent"

    @classmethod
    def run(cls, state: WorkflowState) -> WorkflowState:
        start_time = time.perf_counter()
        entities = state.entities
        if not entities:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="failure",
                duration_ms=elapsed_ms,
                summary="Pre-generation check skipped: CaseEntities missing",
                details={"error": "entities is None"}
            ))
            return state

        failures: list[str] = []
        checks_run: dict[str, str] = {}

        # ── Check A: Filing Party Existence ───────────────────────────────────
        valid_resp_numbers = {r.respondent_number for r in entities.respondents}
        if entities.filing_respondent_number not in valid_resp_numbers:
            msg = (
                f"Filing party Respondent No. {entities.filing_respondent_number} "
                f"not found in respondent list ({sorted(valid_resp_numbers)})"
            )
            failures.append(msg)
            checks_run["Check_A_Filing_Party_Existence"] = f"FAIL: {msg}"
        else:
            checks_run["Check_A_Filing_Party_Existence"] = (
                f"PASS: Respondent No. {entities.filing_respondent_number} exists in parties"
            )

        # ── Check B: Deponent Capacity ────────────────────────────────────────
        dep = entities.deponent
        dep_address = dep.clean_address
        if dep.is_organisation_representative:
            if not dep.designation or not dep_address:
                msg = "Organisation representative requires non-empty designation and address"
                failures.append(msg)
                checks_run["Check_B_Deponent_Capacity"] = f"FAIL: {msg}"
            else:
                checks_run["Check_B_Deponent_Capacity"] = (
                    f"PASS: Org officer '{dep.designation}' with valid address"
                )
        else:
            if not dep_address:
                msg = "Individual deponent requires non-empty address"
                failures.append(msg)
                checks_run["Check_B_Deponent_Capacity"] = f"FAIL: {msg}"
            else:
                checks_run["Check_B_Deponent_Capacity"] = "PASS: Individual deponent with valid address"

        # ── Check C: Reply Points Non-Empty ───────────────────────────────────
        if len(entities.reply_points) < 1:
            msg = "Reply points list is empty; at least 1 substantive reply point required"
            failures.append(msg)
            checks_run["Check_C_Reply_Points_Non_Empty"] = f"FAIL: {msg}"
        else:
            checks_run["Check_C_Reply_Points_Non_Empty"] = (
                f"PASS: {len(entities.reply_points)} reply point(s) present"
            )

        # ── Check D: Verification Verb Agreement ──────────────────────────────
        valid_verbs = ("solemnly affirm", "swear and affirm")
        if dep.verification_verb not in valid_verbs:
            msg = (
                f"Verification verb '{dep.verification_verb}' is not recognized; "
                f"must be exactly one of {valid_verbs}"
            )
            failures.append(msg)
            checks_run["Check_D_Verification_Verb_Agreement"] = f"FAIL: {msg}"
        else:
            checks_run["Check_D_Verification_Verb_Agreement"] = (
                f"PASS: Verification verb '{dep.verification_verb}' is standard"
            )

        # ── Check E: Party Label Integrity ────────────────────────────────────
        # Determine wrong_label as opposite of respondent_label
        wrong_label = None
        if entities.respondent_label == "Defendant":
            wrong_label = "Respondent"
        elif entities.respondent_label == "Respondent":
            wrong_label = "Defendant"

        check_e_passed = True
        if wrong_label:
            pattern = rf'\b{re.escape(wrong_label)}\b'
            for point in entities.reply_points:
                if re.search(pattern, point.content, re.IGNORECASE):
                    msg = (
                        f"Reply point {point.point_number} uses '{wrong_label}' but "
                        f"respondent_label is '{entities.respondent_label}' — will propagate "
                        f"a terminology bug into the draft."
                    )
                    failures.append(msg)
                    check_e_passed = False

        if check_e_passed:
            checks_run["Check_E_Party_Label_Integrity"] = (
                f"PASS: No conflicting '{wrong_label}' references in reply points"
                if wrong_label else "PASS: Non-standard party label; skipped opposition check"
            )
        else:
            checks_run["Check_E_Party_Label_Integrity"] = f"FAIL: Found conflicting '{wrong_label}' terminology"

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        state.pre_guard_failures = failures

        status = "success" if not failures else "failure"
        summary = (
            f"All 5 pre-generation checks passed ({elapsed_ms}ms)"
            if not failures else
            f"Pre-generation check flagged {len(failures)} issue(s) ({elapsed_ms}ms)"
        )

        state.trajectory.append(AgentStep(
            agent_name=cls.NAME,
            status=status,
            duration_ms=elapsed_ms,
            summary=summary,
            details={
                "failures": failures,
                "checks": checks_run,
                "fail_visible": True,
            }
        ))
        logger.info(f"[{cls.NAME}] {summary} (status={status})")
        return state


# ─── 4. DrafterAgent ──────────────────────────────────────────────────────────

class DrafterAgent:
    """
    Agent 3: Legal Document Drafting.
    Thin wrapper calling existing document_generator.generate_all() unchanged.
    """
    NAME = "DrafterAgent"

    @classmethod
    def run(cls, state: WorkflowState) -> WorkflowState:
        start_time = time.perf_counter()
        entities = state.entities
        if not entities:
            raise ValueError("Cannot draft document without extracted entities")

        try:
            # We pass save_docx=False because ExporterAgent handles dynamic persistence
            plain_text, docx_buffer = generate_all(entities, save_docx=False)
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

            state.plain_text = plain_text
            state.docx_buffer = docx_buffer

            summary = f"Compiled {entities.paragraph_count} document paragraphs and DOCX buffer"
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="success",
                duration_ms=elapsed_ms,
                summary=summary,
                details={
                    "paragraph_count": entities.paragraph_count,
                    "character_count": len(plain_text),
                    "docx_bytes": len(docx_buffer.getvalue()) if docx_buffer else 0,
                }
            ))
            logger.info(f"[{cls.NAME}] {summary} ({elapsed_ms}ms)")
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="failure",
                duration_ms=elapsed_ms,
                summary=f"Drafting failed: {e}",
                details={"error": str(e)}
            ))
            logger.error(f"[{cls.NAME}] Drafting failed: {e}")
            raise
        return state


# ─── 5. EvaluatorAgent ────────────────────────────────────────────────────────

class EvaluatorAgent:
    """
    Agent 4: Legal Document Compliance Evaluator.
    Thin wrapper calling existing evaluator.evaluate_document() unchanged.
    Surfaces pre-generation guard failures in issues_detected for complete auditability.
    """
    NAME = "EvaluatorAgent"

    @classmethod
    def run(cls, state: WorkflowState) -> WorkflowState:
        start_time = time.perf_counter()
        if not state.plain_text or not state.entities:
            raise ValueError("Evaluator requires plain_text and entities")

        try:
            report = evaluate_document(state.plain_text, state.entities)

            # Surface any pre-generation guard failures into issues list if not already present
            if state.pre_guard_failures:
                for fail_msg in state.pre_guard_failures:
                    annotated = f"[PRE-GENERATION GUARD] {fail_msg}"
                    if annotated not in report.issues_detected:
                        report.issues_detected.append(annotated)

            state.evaluation_report = report
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

            summary = (
                f"Audit complete: {report.overall_score}/100 "
                f"({report.checks_passed}/{report.total_checks} checks passed)"
            )
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="success",
                duration_ms=elapsed_ms,
                summary=summary,
                details={
                    "overall_score": report.overall_score,
                    "checks_passed": report.checks_passed,
                    "checks_failed": report.checks_failed,
                    "total_checks": report.total_checks,
                    "issues_count": len(report.issues_detected),
                }
            ))
            logger.info(f"[{cls.NAME}] {summary} ({elapsed_ms}ms)")
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="failure",
                duration_ms=elapsed_ms,
                summary=f"Evaluation failed: {e}",
                details={"error": str(e)}
            ))
            logger.error(f"[{cls.NAME}] Evaluation failed: {e}")
            raise
        return state


# ─── 6. ExporterAgent ─────────────────────────────────────────────────────────

class ExporterAgent:
    """
    Agent 5: Artifact Packaging & Export.
    Derives case-stem filename, writes DOCX and evaluation reports to outputs/,
    updates download tracker, and structures download URLs.
    """
    NAME = "ExporterAgent"

    @classmethod
    def run(cls, state: WorkflowState) -> WorkflowState:
        start_time = time.perf_counter()
        if not state.docx_buffer or not state.evaluation_report:
            raise ValueError("Exporter requires docx_buffer and evaluation_report")

        try:
            # Derive clean stem
            raw_stem = Path(state.filename).stem if state.filename else "affidavit"
            clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_stem)

            docx_filename = f"generated_{clean_stem}.docx"
            report_json_filename = f"evaluation_report_{clean_stem}.json"
            report_md_filename = f"evaluation_report_{clean_stem}.md"

            # Ensure OUTPUTS_DIR exists
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

            # 1. Save dynamic named files
            with open(OUTPUTS_DIR / docx_filename, "wb") as f:
                f.write(state.docx_buffer.getvalue())

            with open(OUTPUTS_DIR / report_json_filename, "w", encoding="utf-8") as f:
                f.write(state.evaluation_report.model_dump_json(indent=2))

            with open(OUTPUTS_DIR / report_md_filename, "w", encoding="utf-8") as f:
                f.write(state.evaluation_report.to_markdown())

            # 2. Maintain backward compatible fallback files
            with open(OUTPUTS_DIR / "generated_affidavit.docx", "wb") as f:
                f.write(state.docx_buffer.getvalue())

            with open(OUTPUTS_DIR / "evaluation_report.json", "w", encoding="utf-8") as f:
                f.write(state.evaluation_report.model_dump_json(indent=2))

            with open(OUTPUTS_DIR / "evaluation_report.md", "w", encoding="utf-8") as f:
                f.write(state.evaluation_report.to_markdown())

            # 3. Update main download tracker if available
            try:
                from app.main import _latest_generated_files
                _latest_generated_files["stem"] = clean_stem
                _latest_generated_files["docx"] = docx_filename
                _latest_generated_files["report_json"] = report_json_filename
                _latest_generated_files["report_md"] = report_md_filename
            except (ImportError, AttributeError):
                pass

            exported_files = {
                "affidavit": docx_filename,
                "report_json": report_json_filename,
                "report_md": report_md_filename,
                "download_affidavit_url": f"/api/download/affidavit?filename={docx_filename}",
                "download_report_url": f"/api/download/report?filename={report_json_filename}",
                "download_affidavit_direct": "/api/download/affidavit",
                "download_report_direct": "/api/download/report",
            }
            state.exported_files = exported_files

            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            summary = f"Persisted {docx_filename} and evaluation reports"
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="success",
                duration_ms=elapsed_ms,
                summary=summary,
                details={
                    "files": exported_files,
                    "stem": clean_stem,
                }
            ))
            logger.info(f"[{cls.NAME}] {summary} ({elapsed_ms}ms)")
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state.trajectory.append(AgentStep(
                agent_name=cls.NAME,
                status="failure",
                duration_ms=elapsed_ms,
                summary=f"Export failed: {e}",
                details={"error": str(e)}
            ))
            logger.error(f"[{cls.NAME}] Export failed: {e}")
            raise
        return state


# ─── 7. LinearDocumentWorkflow ────────────────────────────────────────────────

class LinearDocumentWorkflow:
    """
    Linear Multi-Agent Workflow Coordinator.
    Executes:
      ExtractorAgent -> PreGenerationGuardAgent -> DrafterAgent -> EvaluatorAgent -> ExporterAgent
    Strictly linear: single-pass, no retry loops, no branching on score.
    """

    def __init__(self):
        self.agents = [
            ExtractorAgent,
            PreGenerationGuardAgent,
            DrafterAgent,
            EvaluatorAgent,
            ExporterAgent,
        ]

    def run(
        self,
        case_text: str,
        reference_text: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Execute all 5 agents in strict sequence and assemble final API response.
        """
        state = WorkflowState(
            case_text=case_text,
            reference_text=reference_text,
            filename=filename,
        )

        for agent in self.agents:
            state = agent.run(state)

        # Assemble backward-compatible dictionary payload
        docx_filename = state.exported_files.get("affidavit", "generated_affidavit.docx")
        return {
            "success": True,
            "filename": docx_filename,
            "entities": state.entities.model_dump() if state.entities else {},
            "generated_text": state.plain_text or "",
            "evaluation": state.evaluation_report.model_dump() if state.evaluation_report else {},
            "evaluation_markdown": state.evaluation_report.to_markdown() if state.evaluation_report else "",
            "files": state.exported_files,
            "agent_trajectory": [step.to_dict() for step in state.trajectory],
        }
