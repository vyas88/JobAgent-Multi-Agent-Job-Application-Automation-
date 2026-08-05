# JobAgent, Build Roadmap

**Version:** 0.1 (draft)
**Status:** Read after PRD.md and ARCHITECTURE.md. Build in this order.
**Last updated:** 2026-08-04

---

## How to use this with Antigravity

Feed **one phase at a time**. For each phase: let the agent read this file plus `PRD.md`, `ARCHITECTURE.md`, and `AGENTS.md`, produce a plan, review the plan, then let it execute. Do not move to the next phase until the current phase's **Done when** criteria pass. The strategy is a thin vertical slice first: get one platform working end to end before widening.

---

## Phase 0, Foundations

**Goal:** a scaffolded repo the later phases slot into.

**Deliverables:**
- Repo structure organized around the four agents and the Playwright service.
- Postgres schema for the entities in ARCHITECTURE.md Section 4, with migrations.
- A Claude API client wrapper (structured JSON in/out, prompts loaded from `/prompts`).
- Environment/secrets scaffolding (env vars only, no real secrets).
- n8n instance reachable (cloud or self-hosted container).
- Test harness plus a `fixtures/` folder, and a CI skeleton that runs unit tests with no live-portal or real-credential calls.
- A sample master `Profile` fixture and one saved Greenhouse job-page fixture.

**Done when:** the repo builds, migrations create the schema, the Claude wrapper returns a parsed JSON response in a test, and CI runs green on an empty test suite.

---

## Phase 1, Discovery and Analysis Agent (Greenhouse only)

**Goal:** URL in, structured `Job` out.

**Deliverables:**
- Playwright service skeleton with a Greenhouse fetch/render adapter.
- JD text extraction from the rendered page.
- Claude prompt: JD text to structured requirements + ranked keyword list.
- Fit scoring against the master Profile fixture.
- Persist a `Job` record with requirements, keywords, and fit_score.
- Tests against the saved Greenhouse fixture (no live calls).

**Done when:** feeding the fixture URL produces a `Job` row with populated keywords and a fit score, verified by a test.

---

## Phase 2, Resume and Content Agent

**Goal:** tailored resume + cover letter per job, without fabrication.

**Deliverables:**
- Claude prompt: tailor the master Profile to the job's keywords (rephrase/reorder only) into a `ResumeVariant`.
- Claude prompt: generate a role-specific `CoverLetter` referencing company and title.
- Persist both, linked to the `Job`.
- Tests asserting target keywords appear and that no content exists outside the master Profile (no-fabrication check).

**Done when:** a `Job` + Profile fixture yields a tailored resume and cover letter that pass the keyword and no-fabrication tests.

---

## Phase 3, Application Agent (pre-fill + approval-gated submit, Greenhouse)

**Goal:** pre-fill a real Greenhouse form, stop before submit, submit only on approval.

**Deliverables:**
- Greenhouse form adapter: navigate, map and fill standard fields, upload resume, **stop before submit**.
- Review artifact: screenshot + field map, application set to `pending_review`, pushed to a review queue (a simple list/endpoint for v1).
- Approval path: on explicit approval, complete submission and set `submitted`.
- Graceful degradation: broken selector or CAPTCHA flags the application for manual completion.
- Tests against a fixture form, including a test that submission is impossible without approval.

**Done when:** a fixture application reaches `pending_review` with a review artifact, cannot be submitted without approval, and submits correctly once approved.

---

## Phase 4, Tracking and Scheduling Agent

**Goal:** status is always accurate, interviews land on the calendar.

**Deliverables:**
- Status updates plus an append-only `StatusHistory` audit.
- Google Calendar OAuth (least scope) and event creation on a confirmed interview.
- Tests with a mocked calendar API.

**Done when:** status transitions are recorded, and a simulated interview confirmation creates a correct calendar event in test.

*At the end of Phase 4, one platform works end to end.*

---

## Phase 5, Orchestration wiring (n8n)

**Goal:** the five workflows run the pipeline automatically instead of by hand.

**Deliverables:**
- Workflows A through E from ARCHITECTURE.md Section 3, wired to the agents/services.
- Retries and backoff configured.

**Done when:** dropping a Greenhouse URL into Workflow A flows automatically through analyze, content, pre-fill, review, and (post-approval) submit and tracking.

---

## Phase 6, Multi-platform + volume

**Goal:** hit the 50+/week target across platforms.

**Deliverables:**
- Lever and Workday adapters (discovery + form), each with fixtures and tests.
- Rate limiting in n8n to respect portal limits at volume.

**Done when:** all three platforms pass their adapter tests and a batch run respects configured rate limits.

---

## Phase 7, Hardening and acceptance

**Goal:** safe, verifiable, demonstrable.

**Deliverables:**
- Prompt-injection tests (malicious text in a JD fixture must not alter behavior).
- Security pass against AGENTS.md guardrails.
- The PRD Section 12 acceptance test: 10 roles to 10 tailored resumes, cover letters, and pre-filled applications with fit scores, in one run.
- Logging/observability with no secrets in logs.

**Done when:** the acceptance test passes and the security pass finds no guardrail violations.

---

## Milestone summary

| Phase | Outcome |
|------|---------|
| 0 | Repo scaffolded, schema + clients + CI ready |
| 1 | URL to structured Job (Greenhouse) |
| 2 | Tailored resume + cover letter |
| 3 | Pre-fill + approval-gated submit |
| 4 | One platform end to end (tracking + calendar) |
| 5 | Automated via n8n |
| 6 | Three platforms, volume-ready |
| 7 | Hardened, acceptance test passing |
