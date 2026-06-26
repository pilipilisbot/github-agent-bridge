PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_key TEXT NOT NULL,
  repo TEXT,
  thread INTEGER,
  status TEXT NOT NULL CHECK(status IN ('pending','running','done','blocked','denied','waiting_approval')),
  action TEXT NOT NULL,
  decision TEXT NOT NULL,
  work_intent TEXT NOT NULL,
  subject TEXT NOT NULL,
  message_id TEXT NOT NULL UNIQUE,
  uid INTEGER,
  trigger_actor TEXT,
  trigger_actor_avatar_url TEXT,
  context_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0,
  coalesced_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  locked_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_work_status ON jobs(work_key, status);
CREATE TABLE IF NOT EXISTS coalesced_notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  uid INTEGER,
  message_id TEXT NOT NULL UNIQUE,
  subject TEXT NOT NULL,
  trigger_actor TEXT,
  trigger_actor_avatar_url TEXT,
  context_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS worklog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  job_id INTEGER,
  work_key TEXT,
  phase TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS job_session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  work_key TEXT,
  session_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_session_events_job_id ON job_session_events(job_id, id);
CREATE TABLE IF NOT EXISTS job_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  work_key TEXT,
  kind TEXT NOT NULL CHECK(kind IN ('semantic','visible')),
  phase TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_progress_job_kind ON job_progress(job_id, kind, id);
CREATE TABLE IF NOT EXISTS process_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  executor_pid INTEGER,
  root_pid INTEGER,
  running_job_ids_json TEXT NOT NULL DEFAULT '[]',
  process_tree_json TEXT NOT NULL DEFAULT '[]',
  cpu_ticks INTEGER NOT NULL DEFAULT 0,
  io_bytes INTEGER NOT NULL DEFAULT 0,
  active_since_last_sample INTEGER NOT NULL DEFAULT 0,
  idle_seconds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_process_samples_ts ON process_samples(ts);
CREATE TABLE IF NOT EXISTS alerts (
  fingerprint TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  resolved_at TEXT,
  observations INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_alerts_source_resolved ON alerts(source, resolved_at, last_seen);
CREATE TABLE IF NOT EXISTS feedback_events (
  id TEXT PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  source TEXT NOT NULL,
  scope TEXT NOT NULL,
  actor TEXT NOT NULL,
  comment TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  classification TEXT NOT NULL,
  confidence REAL NOT NULL,
  memorable INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_feedback_events_scope_seen ON feedback_events(scope, occurred_at);
CREATE TABLE IF NOT EXISTS feedback_rules (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  type TEXT NOT NULL,
  confidence REAL NOT NULL,
  rule TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  source_events_json TEXT NOT NULL DEFAULT '[]',
  observations INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_feedback_rules_scope_confidence ON feedback_rules(scope, confidence);
CREATE TABLE IF NOT EXISTS feedback_rule_proposals (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES feedback_events(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('approved','rejected','proposed','error')),
  scope TEXT NOT NULL,
  type TEXT NOT NULL,
  confidence REAL NOT NULL,
  rule TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_rule_proposals_status ON feedback_rule_proposals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_rule_proposals_event ON feedback_rule_proposals(event_id);
CREATE TABLE IF NOT EXISTS mcp_tokens (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT,
  expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_tokens_active ON mcp_tokens(revoked_at, expires_at, created_at);
CREATE TABLE IF NOT EXISTS web_push_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_login TEXT NOT NULL,
  endpoint TEXT NOT NULL UNIQUE,
  subscription_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_success_at TEXT,
  last_error TEXT,
  disabled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_web_push_subscriptions_user ON web_push_subscriptions(user_login, disabled_at, updated_at);
