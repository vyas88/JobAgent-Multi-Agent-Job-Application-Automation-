# JobAgent, Product Requirements Document (PRD)

**Version:** 0.1 (draft)
**Status:** Source of truth. Read this before any planning or code.
**Owner:** (you)
**Last updated:** 2026-08-04

---

## 1. Summary

JobAgent is a multi-agent AI system that automates the end-to-end job application pipeline. It analyzes job descriptions, tailors resumes for ATS keyword matching, auto-fills applications on ATS-backed career pages, generates personalized cover letters, tracks application status live, and schedules confirmed interviews to Google Calendar. Four coordinated agents are orchestrated by n8n, with OpenAI as the reasoning layer.

## 2. Problem

Job seekers spend roughly 6 to 8 hours per week manually tailoring resumes, filling repetitive application forms, tracking where they've applied, and juggling interview schedules across three or more job portals. The work is high-volume, repetitive, and error-prone, which caps how many quality applications a person can realistically send.

## 3. Goals and success metrics

- Cut manual effort by 90%+, from ~7 hrs/week to under 30 minutes/week.
- Enable 50+ targeted applications per week.
- Maintain a live, accurate status for every application (applied, in review, interview, rejected, offer).
- Auto-schedule confirmed interviews to Google Calendar.
- Generate a personalized, job-specific cover letter for every application.
- Optimize the resume for each job's ATS keywords before applying.

A build is "successful" for v1 when a user can point JobAgent at a set of target roles and, with under 30 minutes of their own time, have tailored resumes, cover letters, and pre-filled applications ready for review, with tracking updating automatically.

## 4. Scope

### In scope (v1)
- Ingesting job postings from ATS-backed company career pages: **Greenhouse, Lever, Workday** first.
- JD parsing, requirement and keyword extraction, and fit scoring.
- Per-job resume tailoring for ATS keywords (from one master profile).
- Per-job cover letter generation.
- Browser automation to **pre-fill** application forms on the supported ATS platforms.
- Application status tracking in a persistent store.
- Google Calendar scheduling for confirmed interviews.

### Out of scope (v1)
- **LinkedIn automation.** Against their ToS, high ban/CAPTCHA risk. Deferred, revisit only with a compliant approach.
- Fully autonomous submission with no human review (see safety principle below).
- Aggregators like Indeed. Planned for v2 once the ATS flows are solid.
- Mobile app. Web/hosted only for now.

## 5. Core principles

1. **Human-in-the-loop before submit.** *(Assumption, confirm.)* Agents prepare, tailor, and pre-fill everything, but a human reviews and approves before any application is actually submitted in v1. This protects account safety, avoids bad-data submissions, and sidesteps ToS gray areas.
2. **One master profile.** *(Assumption, confirm.)* The user maintains a single master resume/profile. Agents tailor from it per job rather than inventing content, which keeps output truthful and consistent.
3. **Never fabricate.** Agents may rephrase and reorder to match keywords, but never invent experience, skills, or credentials.
4. **Test data first.** No real portal credentials or personal data during development. Dummy accounts and fixtures until flows are proven.

## 6. Users

Primary user: an active job seeker applying to many roles per week who wants volume without sacrificing per-application quality, and who is comfortable reviewing a queue rather than filling forms by hand.

## 7. The four agents (high level)

Detailed responsibilities and interfaces live in the Architecture doc. At the PRD level:

1. **Discovery and Analysis Agent.** Takes job postings (URL or feed), parses the JD, extracts requirements and ATS keywords, and scores how well the master profile fits. Filters out poor matches.
2. **Resume and Content Agent.** Tailors the resume to the target job's keywords and generates a personalized cover letter. Enforces the "never fabricate" rule.
3. **Application Agent.** Drives browser automation to pre-fill the application form on the target ATS platform, then hands off for human review before submit.
4. **Tracking and Scheduling Agent.** Writes and updates application status in the store, and schedules confirmed interviews to Google Calendar.

## 8. End-to-end flow (v1)

1. User provides target roles (list of career-page URLs or a saved search).
2. Discovery and Analysis Agent parses each JD, extracts keywords, scores fit, and drops low-fit roles.
3. For each qualifying role, the Resume and Content Agent produces a tailored resume + cover letter.
4. Application Agent pre-fills the application form and puts it in a review queue.
5. User reviews the queue and approves submissions (batch).
6. Tracking and Scheduling Agent records each submission and monitors status.
7. On a confirmed interview, it creates a Google Calendar event.

## 9. Functional requirements

- Parse JDs from Greenhouse, Lever, and Workday job pages.
- Extract structured requirements and a ranked keyword list from each JD.
- Score profile-to-JD fit and expose the score to the user.
- Tailor the resume per job while preserving factual accuracy.
- Generate a cover letter per job that references the specific role and company.
- Pre-fill standard application fields on each supported ATS.
- Persist every application with a status and timestamp.
- Update status as it changes (source of truth for status TBD in architecture).
- Create calendar events for confirmed interviews.
- Provide the user a review/approval surface for pending applications.

## 10. Tech stack (decisions locked)

- **LLM / reasoning:** OpenAI (via API).
- **Orchestration:** n8n.
- **Deployment:** Cloud / hosted.
- **Browser automation:** Playwright (proposed, confirm in architecture) running against a hosted headless-browser setup.
- **Data store:** persistent DB for applications and status *(specific choice deferred to architecture, e.g., hosted Postgres)*.
- **Integrations:** Google Calendar API.

## 11. Constraints and risks

- **Portal ToS and anti-bot.** Even friendly ATS platforms can change layouts and add CAPTCHAs. Automation must degrade gracefully (flag for manual completion rather than break the pipeline).
- **Credential and PII handling.** The system touches resumes, personal info, and portal logins. Secrets management and least-privilege access are mandatory (detailed in AGENTS.md).
- **Prompt injection.** JD text and career pages are untrusted input fed to an LLM and, in the agentic IDE, to the coding agent. Treat all scraped content as untrusted; never let it trigger actions or leak secrets.
- **Rate and volume.** 50+ applications/week must respect portal rate limits to avoid blocks.
- **Accuracy of tailoring.** Keyword optimization must not tip into fabrication.

## 12. Acceptance criteria (v1)

- Given 10 target Greenhouse/Lever/Workday roles, JobAgent produces 10 tailored resumes, 10 cover letters, and 10 pre-filled applications in a review queue, with fit scores, in one run.
- The user's hands-on time for that run is under 30 minutes.
- All 10 are recorded in the store with correct status.
- A simulated "interview confirmed" event creates a correct Google Calendar entry.

## 13. Open decisions (confirm before architecture)

- [ ] Human-in-the-loop before submit for v1? (assumed yes)
- [ ] Single master profile as the content source? (assumed yes)
- [ ] Preferred data store (Postgres vs hosted alternative)?
- [ ] Where does the user provide target roles: URL list, saved search, or a small UI?
- [ ] Playwright as the automation library? (proposed)
