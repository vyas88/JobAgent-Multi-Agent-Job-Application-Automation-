# JobAgent — n8n Workflow Orchestration Guide

This directory contains importable n8n workflow JSON files that orchestrate the JobAgent pipeline by invoking the Phase 5a FastAPI service over HTTP.

---

## ⚠️ CRITICAL SECURITY DIRECTIVE (MANDATORY APPROVAL GATE)

> [!CAUTION]
> **WORKFLOW D (`n8n/workflow_d_approve_submit.json`) MUST NEVER BE PUT ON AN AUTOMATIC TRIGGER, WEBHOOK, OR SCHEDULED CRON JOB.**
> 
> **Per `AGENTS.md` Security Rule #4, all job applications require an explicit human approval step before submission. Automatically triggering Workflow D would bypass the human approval gate, resulting in unauthorized auto-submission of real job applications. Workflow D must ALWAYS be executed manually by a human operator for a specific `application_id` after reviewing the generated `review_artifact` (screenshot, pre-filled field map, and unanswered questions).**

---

## 1. Network Architecture & Base URL Setup

When running n8n inside a Docker container (standard deployment), `localhost:8000` inside the container refers to n8n itself, **not** the host machine running the FastAPI service.

### Container-to-Host Communication
- **Host / Local Machine Base URL**: `http://localhost:8000`
- **Dockerized n8n Base URL**: `http://host.docker.internal:8000`

### Environment Variable Configuration
Set the following environment variable in your n8n Docker environment or `.env` file:
```env
JOBAGENT_BASE_URL=http://host.docker.internal:8000
```
If `JOBAGENT_BASE_URL` is not set, workflows fall back to `http://host.docker.internal:8000` by default.

---

## 2. API Key Credential Setup in n8n

No API keys or secrets are hardcoded in the workflow JSON files. You must create a Header Auth credential in n8n:

1. Open your n8n web interface (**Credentials** -> **New Credential**).
2. Search for and select **Header Auth**.
3. Configure the credential:
   - **Credential Name**: `JobAgent API Key`
   - **Header Name**: `X-API-Key`
   - **Header Value**: `<YOUR_API_KEY_FROM_ENV>` (e.g. value of `API_KEY` in `.env`)
4. Save the credential. Workflows look for credential name `JobAgent API Key` (ID: `jobagent-api-key`).

---

## 3. Workflow Overview & Import List

Import each JSON file into n8n via **Workflows** -> **Import from File**:

| File | Workflow Name | Description | Trigger Type |
|---|---|---|---|
| `n8n/workflow_a_ingest.json` | **Workflow A: Ingest & Analyze** | Sends job posting URL to `POST /jobs/analyze`. Extracts requirements and persists `Job` record. | Manual / Webhook |
| `n8n/workflow_b_generate.json` | **Workflow B: Generate Content** | Invokes Content Agent via `POST /content/generate` to generate tailored resume variant and cover letter. | Manual / Trigger |
| `n8n/workflow_c_prefill.json` | **Workflow C: Pre-fill Form** | Launches Playwright pre-fill via `POST /applications/prefill`. Captures screenshot and field map, pushes to `pending_review` queue. | Manual / Trigger |
| `n8n/workflow_d_approve_submit.json` | **Workflow D: Manual Approval & Submit Gate** | **HUMAN GATE**: GETs `/applications/{id}` for review artifact verification $\to$ `POST /approve` $\to$ `POST /submit`. | **MANUAL ONLY** |
| `n8n/workflow_e_tracking.json` | **Workflow E: Status & Interview Scheduling** | Updates application status via `POST /status` or schedules Google Calendar interview via `POST /interview`. | Manual / Webhook |

---

## 4. End-to-End Execution Walkthrough

Follow these steps to process a job posting through the pipeline:

### Step 1: Ingest Job (Workflow A)
1. Open **Workflow A**.
2. Set `source_url` in the Manual Trigger node (e.g. `https://boards.greenhouse.io/acme/jobs/12345`).
3. Click **Execute Workflow**.
4. Output contains `job_id` and fit outcome (`qualified` or `disqualified`).

### Step 2: Generate Tailored Content (Workflow B)
1. Open **Workflow B**.
2. Set `job_id` and `profile_id`.
3. Click **Execute Workflow**.
4. Output returns `resume_variant_id` and `cover_letter_id`. Handles 422 fabrication error if ungrounded claims are detected.

### Step 3: Form Pre-fill (Workflow C)
1. Open **Workflow C**.
2. Set `job_id`, `profile_id`, and `resume_variant_id`.
3. Click **Execute Workflow**.
4. Application status is set to `pending_review`. The node outputs the `review_artifact` (screenshot filepath, prefilled field map, unanswered questions).

### Step 4: Human Review & Submission Gate (Workflow D)
1. **Human Operator Action**: Review the screenshot and field map in `review_artifact`.
2. Open **Workflow D**. Enter `application_id`.
3. Click **Execute Workflow**.
4. Workflow D fetches `/applications/{id}`, verifies status is `pending_review`, calls `POST /approve` (transitions status to `approved`), and then calls `POST /submit` (atomic approval gate).

### Step 5: Tracking & Interview Scheduling (Workflow E)
1. Open **Workflow E**.
2. Set `action` (`status_update` or `interview`) and `application_id`.
3. Click **Execute Workflow**. Updates status audit log or creates Google Calendar event.

---

## 5. Rate Limiting & Volume Throttling (50+/Week Target)

To achieve the volume target of **50+ applications per week** while respecting job board anti-bot measures and OpenAI rate limits:

1. **Insert Wait Nodes**: In batch orchestration workflows, place an n8n **Wait Node** set to **2 to 5 minutes** between consecutive form pre-fill executions.
2. **Avoid Parallel Portal Requests**: Run form pre-fill and submit workflows sequentially (concurrency = 1) to prevent IP rate-limiting or CAPTCHA triggers by career portals like Greenhouse.
3. **OpenAI Retry Backoff**: The FastAPI layer automatically handles exponential backoff for OpenAI rate limits. n8n HTTP Request nodes should be configured with 3 retry attempts on 5xx status codes.
