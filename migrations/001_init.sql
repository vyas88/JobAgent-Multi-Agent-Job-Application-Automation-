-- JobAgent: initial database schema
-- Applied to Neon (managed Postgres). This file is the committed source of truth.
-- Run with: psql $DATABASE_URL -f migrations/001_init.sql

BEGIN;

-- ============================================================
-- Enums
-- ============================================================

CREATE TYPE platform AS ENUM (
    'greenhouse',
    'lever',
    'workday'
);

CREATE TYPE job_status AS ENUM (
    'new',
    'analyzed',
    'qualified',
    'disqualified',
    'content_ready',
    'archived'
);

CREATE TYPE application_status AS ENUM (
    'draft',
    'pre_filled',
    'pending_review',
    'approved',
    'submitted',
    'in_review',
    'interview',
    'offer',
    'rejected',
    'withdrawn'
);

-- ============================================================
-- Tables
-- ============================================================

-- Master profile: one per user.
CREATE TABLE profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       TEXT        NOT NULL,
    email           TEXT        NOT NULL,
    phone           TEXT,
    location        TEXT,
    summary         TEXT,
    experience      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    education       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    skills          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    certifications  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    links           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Jobs discovered from career pages.
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url      TEXT            NOT NULL UNIQUE,
    platform        platform        NOT NULL,
    title           TEXT            NOT NULL,
    company         TEXT            NOT NULL,
    location        TEXT,
    raw_jd          TEXT,
    requirements    JSONB           NOT NULL DEFAULT '[]'::jsonb,
    keywords        JSONB           NOT NULL DEFAULT '[]'::jsonb,
    fit_score       NUMERIC(5,2),
    status          job_status      NOT NULL DEFAULT 'new',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- Tailored resume variants, one per job.
CREATE TABLE resume_variants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    profile_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    content         JSONB       NOT NULL,
    file_path       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cover letters, one per job.
CREATE TABLE cover_letters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    profile_id      UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    content         TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Application lifecycle.
CREATE TABLE applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID                NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE, -- UNIQUE for v1 (one application per job); can be relaxed later
    resume_variant_id UUID              REFERENCES resume_variants(id) ON DELETE SET NULL,
    cover_letter_id UUID                REFERENCES cover_letters(id) ON DELETE SET NULL,
    status          application_status  NOT NULL DEFAULT 'draft',
    review_artifact JSONB,
    approved_at     TIMESTAMPTZ,
    submitted_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ         NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ         NOT NULL DEFAULT now()
);

-- Interviews linked to applications.
CREATE TABLE interviews (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID        NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    scheduled_at        TIMESTAMPTZ NOT NULL,
    calendar_event_id   TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only status audit trail.
CREATE TABLE status_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  UUID                NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    old_status      application_status,
    new_status      application_status  NOT NULL,
    changed_at      TIMESTAMPTZ         NOT NULL DEFAULT now(),
    reason          TEXT
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX idx_jobs_status        ON jobs(status);
CREATE INDEX idx_jobs_platform      ON jobs(platform);
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_job   ON applications(job_id);
CREATE INDEX idx_status_history_app ON status_history(application_id);
CREATE INDEX idx_interviews_app     ON interviews(application_id);

-- ============================================================
-- Functions & Triggers
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER set_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER set_applications_updated_at
    BEFORE UPDATE ON applications
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

COMMIT;
