"""Discovery and Analysis Agent.

Responsibilities (ARCHITECTURE.md §2.1):
- Fetch career-page URLs (via the Playwright service for JS-rendered pages).
- Extract JD text from the rendered page.
- Parse JD into structured requirements and a ranked keyword list (via OpenAI).
- Score fit against the master Profile.
- Persist a Job record; drop low-fit jobs from the pipeline.

Implements: Phase 1 of the ROADMAP.
"""

# TODO: Implement in Phase 1.
#   - Wire up Playwright service fetch.
#   - Call OpenAI with the discovery_analyze prompt.
#   - Persist Job record to Postgres.
