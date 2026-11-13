-- Web Automation Engine schema. Applied idempotently by db.connection.ensure_schema().
-- All tables are prefixed `wa_` so they can share a database with other applications.

CREATE TABLE IF NOT EXISTS wa_schema_version (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per execution of an automation.
CREATE TABLE IF NOT EXISTS wa_runs (
    id                         bigserial PRIMARY KEY,
    automation                 text        NOT NULL,
    status                     text        NOT NULL DEFAULT 'running'
                                           CHECK (status IN ('running', 'success', 'failed', 'stopped')),
    started_at                 timestamptz NOT NULL DEFAULT now(),
    completed_at               timestamptz,
    exit_code                  integer,
    stop_reason                text,
    steps_completed            integer     NOT NULL DEFAULT 0,
    steps_failed               integer     NOT NULL DEFAULT 0,
    -- README section 9: the engine must tell these apart; clicking the final button is not success.
    execution_completed        boolean     NOT NULL DEFAULT false,
    export_request_submitted   boolean     NOT NULL DEFAULT false,
    export_request_acknowledged boolean    NOT NULL DEFAULT false,
    export_file_downloaded     boolean     NOT NULL DEFAULT false,
    message                    text,
    -- who started the run: a person pressing the button / the CLI, or the schedule
    trigger                    text        NOT NULL DEFAULT 'manual'
                                           CHECK (trigger IN ('manual', 'scheduled'))
);
ALTER TABLE wa_runs ADD COLUMN IF NOT EXISTS trigger text NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS wa_runs_started_idx ON wa_runs (started_at DESC);

-- One row per step of a run (written when the run finishes).
CREATE TABLE IF NOT EXISTS wa_run_steps (
    id           bigserial PRIMARY KEY,
    run_id       bigint      NOT NULL REFERENCES wa_runs (id) ON DELETE CASCADE,
    position     integer     NOT NULL,
    step_id      text        NOT NULL,
    action       text        NOT NULL,
    status       text        NOT NULL CHECK (status IN ('ok', 'failed', 'stopped', 'skipped')),
    duration_ms  integer     NOT NULL DEFAULT 0,
    message      text,
    attempts     integer     NOT NULL DEFAULT 1,
    UNIQUE (run_id, position)
);
-- Tables created by an earlier version get the column too.
ALTER TABLE wa_run_steps ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 1;

-- Log lines of a run. Passwords, codes, cookies and tokens are never logged in the first place.
CREATE TABLE IF NOT EXISTS wa_logs (
    id        bigserial PRIMARY KEY,
    run_id    bigint      REFERENCES wa_runs (id) ON DELETE CASCADE,
    logged_at timestamptz NOT NULL DEFAULT now(),
    level     text        NOT NULL,
    logger    text        NOT NULL,
    message   text        NOT NULL
);
CREATE INDEX IF NOT EXISTS wa_logs_run_idx ON wa_logs (run_id, id);

-- The CURRENT followers of an account: one row per follower, no history rows.
-- A new follower is inserted with added_at = now(); a follower missing from the latest
-- export is deleted (they unfollowed). Existing followers are left untouched, so added_at
-- stays the time we first saw them.
CREATE TABLE IF NOT EXISTS wa_followers (
    account        text        NOT NULL,
    username       text        NOT NULL,
    profile_url    text,
    followed_at    timestamptz,               -- when they followed, as reported by Instagram
    added_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account, username)
);
CREATE INDEX IF NOT EXISTS wa_followers_added_idx ON wa_followers (account, added_at DESC);

-- Audit trail of what each sync changed, so unfollows are not lost when the row is deleted.
CREATE TABLE IF NOT EXISTS wa_follower_events (
    id         bigserial PRIMARY KEY,
    run_id     bigint      REFERENCES wa_runs (id) ON DELETE SET NULL,
    account    text        NOT NULL,
    username   text        NOT NULL,
    event      text        NOT NULL CHECK (event IN ('added', 'removed')),
    happened_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wa_follower_events_idx ON wa_follower_events (account, happened_at DESC);

INSERT INTO wa_schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
