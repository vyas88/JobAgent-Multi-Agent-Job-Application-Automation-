# JobAgent — Project Status

A multi-agent job-application automation pipeline for Greenhouse job boards, with a
mandatory human-in-the-loop approval gate before any submission. Built with FastAPI,
Playwright, OpenAI, Neon Postgres, and n8n orchestration.

> **Design principle:** the system never auto-submits and never fabricates resume/cover-letter
> content. A human reviews and explicitly approves every application before it can be submitted.

---

## Architecture

```
n8n (orchestration)
   -> FastAPI orchestration API  (approval gate, auth, exception mapping)
        -> Discovery Agent   -> Playwright render service -> OpenAI  -> Neon
        -> Content Agent     -> OpenAI (+ no-fabrication guardrail)  -> Neon
        -> Application Agent -> Playwright render service (prefill/submit) -> Neon
        -> Tracking Agent    -> Google Calendar (mocked in tests) -> Neon
```

Two local services run side by side:
- **API** — `uvicorn src.api.main:app --port 8000`
- **Render service** — `uvicorn src.services.render_app:app --port 8090` (headless Chromium,
  single reused browser, SSRF/domain allowlist restricted to `*.greenhouse.io`)

---

## What is verified live (real services, not mocks)

These were each exercised against real OpenAI, a real headless browser rendering a real
Greenhouse page, and live Neon Postgres:

- **Discovery / `POST /jobs/analyze`** — fetches and renders a live Greenhouse posting,
  parses it (supports both `boards.greenhouse.io` and the newer `job-boards.greenhouse.io`
  layouts), scores fit, and persists the job. Verified against a real Remote.com posting.
- **Content / `POST /content/generate`** — generates a tailored resume and cover letter with
  GPT-4o, grounded in the master profile. The **no-fabrication guardrail** runs on real model
  output: it blocks invented metrics and passes legitimately grounded content. Verified stable
  across repeated runs (LLM phrasing variation).
- **Prefill / `POST /applications/prefill`** — drives the real browser to fill the standard
  identity fields (name, email, phone, resume) on the live Greenhouse application form, and
  **detects unfilled custom/screening questions**, surfacing them in `unanswered_questions`
  with `manual_completion_required: true`. Never auto-answers screening questions or essays.
- **Approval gate (`/submit`, `/approve`, `/status`)** — verified live end-to-end:
  - submit before approval -> **409** (refused)
  - direct status backdoor to `approved`/`submitted` -> **409** (forbidden)
  - approve via dedicated endpoint -> **200**
  - submit after approval -> **200**, with a single, correct `status_history` audit row.
- **API key auth** — every mutating endpoint returns **401** without a valid `X-API-Key`.
- **n8n Workflow A (Ingest & Analyze)** — run end-to-end through n8n against the live API,
  returning a real analyzed job. This proves the full orchestration path
  `n8n -> FastAPI -> render -> OpenAI -> Neon`.

---

## What is built and API-proven, but not yet each run through n8n

The underlying API endpoints for these are all verified live (above). The corresponding n8n
workflows (B–E) are authored and importable, and follow the **identical** node pattern proven
for Workflow A (fixed `host.docker.internal:8000` URL, Header Auth credential, JSON body):

- Workflow B — Generate Content
- Workflow C — Prefill
- Workflow D — Approve + Submit (human approval gate; must never be auto-triggered)
- Workflow E — Status / Interview tracking

Wiring each is a repeat of the Workflow A setup, not new engineering.

---

## What is intentionally mocked (documented scope decision)

- **Google Calendar integration (Tracking Agent).** The `schedule_interview` logic — create
  calendar event first, then DB write, with a compensating delete on failure and an explicit
  `OrphanedCalendarEventError` if cleanup fails — is fully implemented and covered by unit and
  integration tests using a **mock calendar client**. Live Google OAuth (downloading
  `credentials.json`, completing the consent flow to mint `token.json`) is a documented setup
  step and was deliberately deferred: it is peripheral to the core automation and adds no new
  logic, only external credentials.

---

## Safety properties (enforced, not aspirational)

- **No auto-submit.** Submission is refused unless the application is explicitly `approved`
  via the dedicated endpoint. The generic status endpoint cannot set `approved`/`submitted`/
  `submit_uncertain`.
- **No fabrication.** Tailored content is verified against the master profile; invented
  metrics hard-fail; ambiguous cases are flagged `needs_review`.
- **Honest review artifact.** Prefill surfaces every unanswered required question so a human
  reviewer sees exactly what still needs completing before approving.
- **Append-only audit trail.** Every status transition is recorded once in `status_history`.
- **Untrusted scraped content.** Page content is treated as data, never as instructions.
- **SSRF guard.** The render service only fetches allowlisted Greenhouse domains.

---

## Testing

- Unit tests run by default; integration tests are marked and opt-in (`pytest -m integration`)
  because they require live Neon, OpenAI, a browser, and network access.
- Integration-test teardown deletes only the rows each test created (no table-wide cleanup),
  so manually-seeded data is preserved.
- CI runs the unit suite only (no live credentials in CI).

## Known follow-ups (honest backlog)

- Wire n8n Workflows B–E through the GUI the same way Workflow A was proven.
- Live Google Calendar OAuth setup for the Tracking Agent.
- Graceful handling when the render service rejects a URL (currently a render 400 can surface
  as a 500 on `/jobs/analyze` instead of a clean `parse_failed`).
- Make the discovery profile lookup explicit rather than silently falling back to a fixture.
- Fit-score threshold tuning (a 72.5 score currently maps to `disqualified`).
