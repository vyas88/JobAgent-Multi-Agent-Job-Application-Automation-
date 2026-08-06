-- JobAgent: Add submit_uncertain status to application_status enum
-- Must run outside a transaction block (no BEGIN/COMMIT) in Postgres.

ALTER TYPE application_status ADD VALUE 'submit_uncertain';
