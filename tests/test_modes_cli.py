import threading
import time

from github_agent_bridge.dispatch import GitHubClient, OpenClawDispatcher, RunMode
from github_agent_bridge.models import GitHubContext, Job
from github_agent_bridge.policy import ModelRoute, ModelRoutes, Policy


def make_job(work_intent="work_allowed", job_id=1, attempts=1):
    ctx = GitHubContext(["https://github.com/gisce/erp/pull/1#discussion_r2"], "gisce/erp", 1, review_comment_id=2)
    return Job(job_id, ctx.work_key, ctx.repo, ctx.issue_number, "running", "reply_comment", work_intent, "subject", "<x@github.com>", 1, ctx, attempts=attempts)


def test_shadow_github_reaction_has_no_external_failure():
    assert GitHubClient(gh_bin="definitely-not-present", mode=RunMode.SHADOW).react_eyes(make_job().context) is True


class RecordingGitHubClient(GitHubClient):
    def __init__(self):
        super().__init__(mode=RunMode.LIVE)
        self.calls = []

    def _run(self, args):
        self.calls.append(args)

        class Result:
            returncode = 0
            stdout = '[{"id": 123}, {"id": 456}]' if args[-1].endswith("/comments") else "{}"
            stderr = ""

        return Result()


def test_review_reaction_targets_review_comments():
    client = RecordingGitHubClient()
    ctx = GitHubContext(
        ["https://github.com/gisce/erp/pull/1#pullrequestreview-99"],
        "gisce/erp",
        1,
        review_id=99,
        target_kind="review",
    )

    assert client.react_eyes(ctx) is True

    assert any(call[-1].endswith("/reviews/99/comments") for call in client.calls)
    assert any("pulls/comments/123/reactions" in " ".join(call) for call in client.calls)
    assert any("pulls/comments/456/reactions" in " ".join(call) for call in client.calls)


def test_commit_comment_reaction_targets_commit_comment():
    client = RecordingGitHubClient()
    ctx = GitHubContext(
        ["https://github.com/pilipilisbot/github-agent-bridge/commit/fbd7bc1#r185806568"],
        "pilipilisbot/github-agent-bridge",
        commit_comment_id=185806568,
        commit_sha="fbd7bc1",
        target_kind="commit_comment",
    )

    assert client.react_eyes(ctx) is True

    assert any("repos/pilipilisbot/github-agent-bridge/comments/185806568/reactions" in " ".join(call) for call in client.calls)


def test_shadow_dispatch_returns_command_without_running():
    result = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW).dispatch(make_job(), Policy(trusted_orgs={"gisce"}), reaction_ok=True)
    assert result.ok is True
    assert result.command
    assert result.command[0] == "systemd-run"
    assert "--scope" in result.command
    assert "--unit=github-agent-bridge-job-1-attempt-1.scope" in result.command
    assert "agent" in result.command
    assert "--local" in result.command
    assert "--model" not in result.command
    assert "--thinking" not in result.command
    assert "--session-id" in result.command
    assert result.command[result.command.index("--session-id") + 1] == "github-agent-bridge-job-1-attempt-1"
    assert "--session-key" in result.command
    assert result.command[result.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1"
    )
    assert result.command[result.command.index("--verbose") + 1] == "on"
    assert "--timeout" in result.command
    assert "3600" in result.command


def test_work_allowed_dispatch_uses_fresh_session_id_and_stable_thread_key():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    policy = Policy(trusted_orgs={"gisce"})
    first = dispatcher.dispatch(make_job(), policy, reaction_ok=True)
    second = dispatcher.dispatch(make_job(job_id=2), policy, reaction_ok=True)
    retry = dispatcher.dispatch(make_job(job_id=2, attempts=2), policy, reaction_ok=True)

    assert first.command
    assert second.command
    assert retry.command
    assert first.command[first.command.index("--session-id") + 1] == "github-agent-bridge-job-1-attempt-1"
    assert second.command[second.command.index("--session-id") + 1] == "github-agent-bridge-job-2-attempt-1"
    assert retry.command[retry.command.index("--session-id") + 1] == "github-agent-bridge-job-2-attempt-2"
    assert first.command[first.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1"
    )
    assert second.command[second.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1"
    )
    assert retry.command[retry.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1"
    )


def test_compaction_retry_uses_fresh_session_key_and_id_for_review_only_work():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    job = make_job("review_only", job_id=2, attempts=2)
    job.metadata["openclaw_session_id"] = "github-agent-bridge-job-2"
    job.metadata["fresh_session_on_retry"] = True

    result = dispatcher.dispatch(job, Policy(trusted_orgs={"gisce"}), reaction_ok=True)

    assert result.command
    assert result.command[result.command.index("--session-id") + 1] == "github-agent-bridge-job-2-attempt-2"
    assert result.command[result.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1:fresh:2:attempt:2"
    )


def test_work_allowed_dispatch_ignores_legacy_session_id_metadata():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    policy = Policy(trusted_orgs={"gisce"})
    job = make_job(job_id=2, attempts=2)
    job.metadata["openclaw_session_id"] = "github-agent-bridge-job-2"

    result = dispatcher.dispatch(job, policy, reaction_ok=True)

    assert result.command
    assert result.command[result.command.index("--session-id") + 1] == "github-agent-bridge-job-2-attempt-2"


def test_review_only_dispatch_uses_stable_thread_key():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    policy = Policy(trusted_orgs={"gisce"})
    first = dispatcher.dispatch(make_job("review_only"), policy, reaction_ok=True)
    second = dispatcher.dispatch(make_job("review_only", job_id=2), policy, reaction_ok=True)

    assert first.command
    assert second.command
    assert first.command[first.command.index("--session-id") + 1] == "github-agent-bridge-job-1"
    assert second.command[second.command.index("--session-id") + 1] == "github-agent-bridge-job-2"
    assert first.command[first.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1"
    )
    assert second.command[second.command.index("--session-key") + 1] == (
        "github-agent-bridge:local-v2:gisce-erp-1"
    )


def test_review_only_dispatch_uses_shorter_timeout():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    result = dispatcher.dispatch(make_job("review_only"), Policy(trusted_orgs={"gisce"}), reaction_ok=True)
    assert result.command
    timeout_idx = result.command.index("--timeout")
    assert result.command[timeout_idx + 1] == "900"


def test_dispatch_adds_configured_model_route_to_command():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    policy = Policy(
        trusted_orgs={"gisce"},
        model_routes=ModelRoutes(
            by_action={
                "reply_comment": ModelRoute(
                    model="openai/gpt-5.4-mini",
                    thinking="low",
                )
            }
        ),
    )

    result = dispatcher.dispatch(make_job(), policy, reaction_ok=True)

    assert result.command
    assert result.command[result.command.index("--model") + 1] == "openai/gpt-5.4-mini"
    assert result.command[result.command.index("--thinking") + 1] == "low"


def test_dispatch_uses_classifier_complexity_model_route():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    policy = Policy(
        trusted_orgs={"gisce"},
        model_routes=ModelRoutes(
            by_complexity={
                "mechanical": ModelRoute(model="openai/gpt-5.4-mini", thinking="low")
            }
        ),
    )
    job = make_job()
    job.metadata["intent_classifier"] = {
        "llm": {"applied": True, "complexity": "mechanical"}
    }

    result = dispatcher.dispatch(job, policy, reaction_ok=True)

    assert result.command
    assert result.command[result.command.index("--model") + 1] == "openai/gpt-5.4-mini"
    assert result.command[result.command.index("--thinking") + 1] == "low"


def test_live_dispatch_streams_openclaw_output_to_activity_callback(tmp_path):
    openclaw = tmp_path / "openclaw"
    openclaw.write_text(
        "#!/bin/sh\n"
        "printf 'thinking line\\n'\n"
        "printf 'tool error\\n' >&2\n",
        encoding="utf-8",
    )
    openclaw.chmod(0o755)
    events = []
    processes = []

    result = OpenClawDispatcher(
        openclaw_bin=str(openclaw),
        mode=RunMode.LIVE,
        cli_grace_seconds=1,
        systemd_run_bin=None,
    ).dispatch(
        make_job(),
        Policy(trusted_orgs={"gisce"}),
        reaction_ok=True,
        activity_callback=lambda event_type, summary, detail: events.append((event_type, summary, detail)),
        process_callback=lambda identity: processes.append(identity),
    )

    assert result.ok is True
    assert ("openclaw_stdout", "OpenClaw CLI output", "thinking line") in events
    assert ("openclaw_stderr", "OpenClaw CLI error output", "tool error") in events
    assert len(processes) == 1
    assert processes[0]["pid"] > 0
    assert processes[0]["start_time_ticks"] > 0


def test_live_dispatch_streams_partial_openclaw_output_before_process_exits(tmp_path, monkeypatch):
    done = tmp_path / "done"
    openclaw = tmp_path / "openclaw"
    openclaw.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "sys.stdout.write('partial output')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.5)\n"
        "pathlib.Path(os.environ['DONE_FILE']).write_text('done', encoding='utf-8')\n",
        encoding="utf-8",
    )
    openclaw.chmod(0o755)
    monkeypatch.setenv("DONE_FILE", str(done))
    callback_observed_done = []

    result = OpenClawDispatcher(
        openclaw_bin=str(openclaw),
        mode=RunMode.LIVE,
        cli_grace_seconds=1,
        systemd_run_bin=None,
    ).dispatch(
        make_job(),
        Policy(trusted_orgs={"gisce"}),
        reaction_ok=True,
        activity_callback=lambda event_type, summary, detail: callback_observed_done.append(done.exists()) if detail == "partial output" else None,
    )

    assert result.ok is True
    assert done.exists()
    assert callback_observed_done == [False]


def test_dispatcher_shutdown_terminates_active_process_group(tmp_path, monkeypatch):
    started = tmp_path / "started"
    openclaw = tmp_path / "openclaw"
    openclaw.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import pathlib\n"
        "import time\n"
        "pathlib.Path(os.environ['STARTED_FILE']).write_text('started', encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    openclaw.chmod(0o755)
    monkeypatch.setenv("STARTED_FILE", str(started))
    dispatcher = OpenClawDispatcher(
        openclaw_bin=str(openclaw),
        mode=RunMode.LIVE,
        cli_grace_seconds=1,
        systemd_run_bin=None,
    )
    results = []
    thread = threading.Thread(
        target=lambda: results.append(dispatcher.dispatch(make_job(), Policy(trusted_orgs={"gisce"}))),
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert started.exists()
    dispatcher.shutdown(kill_grace_seconds=0.1)
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert results[0].ok is False
    assert results[0].cancelled is True


def test_dispatcher_does_not_hardcode_org_agent_fallback():
    dispatcher = OpenClawDispatcher(mode=RunMode.SHADOW)
    job = make_job()
    assert dispatcher.route_for(job, Policy()) == (None, "telegram", "")


def test_dispatch_passes_policy_role_into_prompt():
    result = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW).dispatch(
        make_job(), Policy(repo_roles={"gisce/erp": "owner"}), reaction_ok=True
    )
    assert result.command
    message = result.command[result.command.index("--message") + 1]
    assert "# Repository role: owner" in message
