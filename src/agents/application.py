"""Application Agent.

Responsibilities (ARCHITECTURE.md §2.3):
- Navigate career pages via the Playwright service.
- Map and fill standard application fields, upload the resume.
- STOP before submit — capture a screenshot + field map as a review artifact.
- Set application status to pending_review.
- On explicit user approval (Workflow D), complete submission.

SECURITY: Never auto-submit. The human approval gate is mandatory in v1.

Implements: Phase 3 of the ROADMAP.
"""

# TODO: Implement in Phase 3.
#   - Wire up Playwright service for form filling.
#   - Implement per-platform adapters (Greenhouse first).
#   - Build review artifact capture.
#   - Implement approval-gated submit path.
