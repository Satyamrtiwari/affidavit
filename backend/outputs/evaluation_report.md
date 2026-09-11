# Evaluation Report

**Overall Score: 100.0 / 100**

## Dimension Scores

| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Entity Accuracy | 100/100 | 20% | 20.0 |
| Completeness | 100/100 | 20% | 20.0 |
| Structure | 100/100 | 20% | 20.0 |
| Consistency | 100/100 | 15% | 15.0 |
| Template Fidelity | 100/100 | 15% | 15.0 |
| Hallucination Check | 100/100 | 10% | 10.0 |

## Validation Checks

**Passed:** 7/7

- ✅ **Respondent Number Consistency**
- ✅ **Paragraph Range Match**
- ✅ **Required Sections Present**
- ✅ **Verb Agreement**
- ✅ **Deponent-Respondent Type Match**
- ✅ **Entity Accuracy**
- ✅ **Template Fidelity (Fixed Phrases)**

## Scoring Methodology

Scoring is based on 6 weighted dimensions, each scored 0-100:
- Entity Accuracy: weight 20%
- Completeness: weight 20%
- Structure: weight 20%
- Consistency: weight 15%
- Template Fidelity: weight 15%
- Hallucination Check: weight 10%

Overall score = sum of (dimension_score × weight/100) for all dimensions.
All 7 checks are deterministic (no LLM dependency).
HIGH severity failures incur a 20-point penalty on the dimension score.
MEDIUM severity failures incur a 10-point penalty.

*Report generated at: 2026-09-11T13:11:00.105590*