# JobAgent

Multi-agent AI system that automates the end-to-end job application pipeline: JD analysis, resume tailoring, application pre-filling, status tracking, and interview scheduling.

## Quick Start

### Prerequisites

- Python 3.11+
- A `.env` file (copy from `.env.example` and fill in values)

### Setup

```bash
# Clone the repo
git clone https://github.com/vyas88/JobAgent-Multi-Agent-Job-Application-Automation-.git
cd JobAgent

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies (including dev tools)
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest --cov=src -v
```

Tests use dummy environment variables and mock all external services. No real credentials or live API calls are needed.

### Project Structure

```
src/
├── config.py                 # Environment-based configuration
├── db.py                     # Postgres connection helper (asyncpg)
├── llm/
│   └── openai_client.py      # OpenAI wrapper (structured JSON output)
├── agents/
│   ├── discovery.py           # Discovery & Analysis Agent
│   ├── content.py             # Resume & Content Agent
│   ├── application.py         # Application Agent
│   └── tracking.py            # Tracking & Scheduling Agent
└── services/
    └── playwright_service.py  # Playwright HTTP service client

prompts/                       # Versioned LLM system prompts
migrations/                    # SQL migration files
fixtures/                      # Test fixtures (dummy data, saved pages)
tests/                         # pytest test suite
```

### Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full technical design. See [docs/PRD.md](docs/PRD.md) for product requirements. See [docs/ROADMAP.md](docs/ROADMAP.md) for the build plan.

### Security

- No real credentials in development — use test fixtures and dummy accounts only.
- All secrets come from environment variables (never hardcoded).
- Human approval is required before any application is submitted.
- LLM prompts are hardened against prompt injection from scraped content.

See [AGENTS.md](AGENTS.md) for the full set of security guardrails.
