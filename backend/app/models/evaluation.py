"""
Evaluation Models — Validation results, scores, and the evaluation report.

These models define the output of the evaluation stage:
- Individual validation check results
- Per-dimension scores
- The final evaluation report with overall score and error list
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime


class ValidationResult(BaseModel):
    """Result of a single validation check."""
    check_name: str = Field(..., description="Name of the validation check")
    check_id: str = Field(..., description="Machine-readable check ID, e.g. 'respondent_number_consistency'")
    passed: bool = Field(..., description="Whether the check passed")
    severity: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        "MEDIUM",
        description="Severity if the check failed"
    )
    message: str = Field(
        "",
        description="Human-readable description of the result"
    )
    details: Optional[str] = Field(
        None,
        description="Additional details — e.g. what was expected vs what was found"
    )
    source_reference: Optional[str] = Field(
        None,
        description="Where the error was detected — e.g. 'Paragraph 5' or 'Verification section'"
    )


class DimensionScore(BaseModel):
    """Score for a single evaluation dimension."""
    dimension: str = Field(..., description="Name of the dimension, e.g. 'Entity Accuracy'")
    score: int = Field(..., ge=0, le=100, description="Score out of 100")
    max_score: int = Field(100, description="Maximum possible score")
    weight: int = Field(..., description="Weight in overall score calculation")
    weighted_score: float = Field(..., description="Weighted contribution to overall score")
    notes: str = Field("", description="Explanation of how the score was determined")


class EvaluationReport(BaseModel):
    """
    The complete evaluation report produced by the system.

    Contains the overall score, per-dimension breakdown, all validation
    results, and a summary of issues detected.
    """
    # ─── Scores ───────────────────────────────────────────────────────────
    overall_score: float = Field(..., ge=0, le=100, description="Overall weighted score out of 100")
    dimension_scores: list[DimensionScore] = Field(
        ...,
        description="Individual scores for each evaluation dimension"
    )

    # ─── Validation Results ───────────────────────────────────────────────
    validation_results: list[ValidationResult] = Field(
        ...,
        description="Results of all validation checks (deterministic + LLM)"
    )
    checks_passed: int = Field(..., description="Number of checks that passed")
    checks_failed: int = Field(..., description="Number of checks that failed")
    total_checks: int = Field(..., description="Total number of checks run")

    # ─── Issues ───────────────────────────────────────────────────────────
    issues_detected: list[str] = Field(
        default_factory=list,
        description="Human-readable list of all issues found"
    )

    # ─── Metadata ─────────────────────────────────────────────────────────
    scoring_methodology: str = Field(
        "",
        description="Explanation of how the overall score was calculated"
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="Timestamp of when the report was generated"
    )

    def to_markdown(self) -> str:
        """Convert the evaluation report to a human-readable Markdown string."""
        lines = []
        lines.append("# Evaluation Report")
        lines.append("")
        lines.append(f"**Overall Score: {self.overall_score:.1f} / 100**")
        lines.append("")

        # Dimension scores table
        lines.append("## Dimension Scores")
        lines.append("")
        lines.append("| Dimension | Score | Weight | Weighted |")
        lines.append("|-----------|-------|--------|----------|")
        for ds in self.dimension_scores:
            lines.append(
                f"| {ds.dimension} | {ds.score}/100 | {ds.weight}% | {ds.weighted_score:.1f} |"
            )
        lines.append("")

        # Validation checks
        lines.append("## Validation Checks")
        lines.append("")
        lines.append(f"**Passed:** {self.checks_passed}/{self.total_checks}")
        lines.append("")
        for vr in self.validation_results:
            icon = "✅" if vr.passed else "❌"
            lines.append(f"- {icon} **{vr.check_name}**")
            if not vr.passed:
                lines.append(f"  - Severity: {vr.severity}")
                lines.append(f"  - {vr.message}")
                if vr.details:
                    lines.append(f"  - Details: {vr.details}")
                if vr.source_reference:
                    lines.append(f"  - Source: {vr.source_reference}")
        lines.append("")

        # Issues
        if self.issues_detected:
            lines.append("## Issues Detected")
            lines.append("")
            for i, issue in enumerate(self.issues_detected, 1):
                lines.append(f"{i}. {issue}")
            lines.append("")

        # Methodology
        lines.append("## Scoring Methodology")
        lines.append("")
        lines.append(self.scoring_methodology)
        lines.append("")
        lines.append(f"*Report generated at: {self.generated_at}*")

        return "\n".join(lines)
