import sqlite3
from importlib import resources

from github_agent_bridge.queue import SCHEMA


def test_sql_schema_is_packaged_resource_and_valid():
    schema_path = resources.files("github_agent_bridge.sql").joinpath("schema.sql")
    schema = schema_path.read_text(encoding="utf-8")
    assert schema == SCHEMA
    assert "CREATE TABLE IF NOT EXISTS jobs" in schema
    con = sqlite3.connect(":memory:")
    con.executescript(schema)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "jobs",
        "coalesced_notifications",
        "state",
        "worklog",
        "job_session_events",
        "job_progress",
        "process_samples",
        "alerts",
        "feedback_events",
        "feedback_rules",
        "feedback_rule_proposals",
    } <= tables


def test_packaged_resource_names_are_documented():
    prompt_names = {p.name for p in resources.files("github_agent_bridge.prompt_rules").iterdir() if p.name.endswith(".md")}
    assert {"base.md", "worktree.md", "pr_metadata.md", "human_reviewer.md", "review_only.md", "sync_after_merge.md", "feedback_classifier.md", "intent_classifier.md"} <= prompt_names
    role_names = {p.name for p in resources.files("github_agent_bridge.prompt_rules").joinpath("roles").iterdir() if p.name.endswith(".md")}
    assert {"owner.md", "maintainer.md", "contributor.md", "reviewer.md"} <= role_names
    sql_names = {p.name for p in resources.files("github_agent_bridge.sql").iterdir() if p.name.endswith(".sql")}
    assert {"schema.sql"} <= sql_names
