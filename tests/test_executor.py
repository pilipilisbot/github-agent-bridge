from github_agent_bridge.dashboard_data import job_session_events
from github_agent_bridge.dispatch import DispatchResult
from github_agent_bridge.executor import ExecutorConfig, ExecutorPool
from github_agent_bridge.models import Notification
from github_agent_bridge.policy import ModelRoute, ModelRoutes, Policy
from github_agent_bridge.queue import JobQueue


class FakeGitHub:
    def __init__(self, assigned: bool, mentioned: bool = True, non_actionable_review: bool = False, authored: bool = False, answered_url: str | None = None):
        self.assigned = assigned
        self.mentioned = mentioned
        self.non_actionable_review = non_actionable_review
        self.authored = authored
        self.answered_url = answered_url
        self.followup_url = answered_url or "https://github.com/gisce/erp/issues/27315#issuecomment-2"
        self.eyes = 0
        self.acks = 0
        self.eye_comment_ids = []

    def is_assigned_to_current_user(self, ctx):
        return self.assigned

    def is_pull_request_authored_by_current_user(self, ctx):
        return self.authored

    def issue_comment_addresses_current_user(self, ctx):
        return self.mentioned

    def is_non_actionable_review(self, ctx):
        return self.non_actionable_review

    def current_user_commented_after(self, ctx):
        return self.answered_url

    def visible_followup_after_trigger(self, ctx):
        return self.followup_url

    def react_eyes(self, ctx):
        self.eyes += 1
        self.eye_comment_ids.append(ctx.comment_id)
        return True

    def react_ack_no_comment(self, ctx):
        self.acks += 1
        return True


class RecordingDispatcher:
    def __init__(self, stdout: str = "ok", stderr: str = "", ok: bool = True, returncode: int = 0, timed_out: bool = False):
        self.jobs = []
        self.stdout = stdout
        self.stderr = stderr
        self.ok = ok
        self.returncode = returncode
        self.timed_out = timed_out

    def dispatch(self, job, policy, reaction_ok=None, activity_callback=None):
        self.jobs.append(job)
        if activity_callback:
            activity_callback("openclaw_stdout", "OpenClaw CLI output", "thinking about the change")
            activity_callback("openclaw_stderr", "OpenClaw CLI error output", "token=secret ghp_abcdefghijklmnopqrstuvwxyz")
        return DispatchResult(self.ok, self.returncode, self.stdout, self.stderr, self.timed_out, reaction_ok, ["openclaw"])


def enqueue_pr_review(queue: JobQueue):
    notification = Notification(
        uid=2,
        message_id="<gisce/erp/pull/27737/review/4282224025@github.com>",
        subject="Re: [gisce/erp] Endurecer ir.values sin nuevos pickles (PR #27737)",
        from_addr="notifications@github.com",
        body="Copilot wasn't able to review any files in this pull request. https://github.com/gisce/erp/pull/27737#pullrequestreview-4282224025",
    )
    job, state = queue.enqueue(notification, Policy(trusted_orgs={"gisce"}))
    assert state == "enqueued"
    assert job is not None
    assert job.action == "reply_comment"
    assert job.context.review_id == 4282224025
    return job


def enqueue_pr_comment(queue: JobQueue):
    notification = Notification(
        uid=1,
        message_id="<gisce/erp/pull/27315/c1@github.com>",
        subject="Re: [gisce/erp] Permitir caller en los dominios (PR #27315)",
        from_addr="notifications@github.com",
        body="@pilipilisbot però la transacció en què s'executa que entra per eval_domain és readonly https://github.com/gisce/erp/pull/27315#issuecomment-1",
    )
    job, state = queue.enqueue(notification, Policy(trusted_orgs={"gisce"}))
    assert state == "enqueued"
    assert job is not None
    assert job.action == "reply_comment"
    assert job.work_intent == "review_only"
    return job


def enqueue_pr_comment_from(queue: JobQueue, actor: str, uid: int, message_id: str, comment_id: int):
    notification = Notification(
        uid=uid,
        message_id=message_id,
        subject="Re: [gisce/erp] Permitir caller en los dominios (PR #27315)",
        from_addr=f"{actor} <notifications@github.com>",
        body=f"@pilipilisbot fes-ho https://github.com/gisce/erp/pull/27315#issuecomment-{comment_id}",
    )
    job, state = queue.enqueue(notification, Policy(trusted_orgs={"gisce"}))
    assert job is not None
    return job, state


def enqueue_workflow_run_failed(queue: JobQueue):
    notification = Notification(
        uid=3,
        message_id="<gisce/erp/actions/runs/26325244472@github.com>",
        subject="[gisce/erp] Run failed: tests - main",
        from_addr="notifications@github.com",
        body="Run failed: https://github.com/gisce/erp/actions/runs/26325244472",
    )
    job, state = queue.enqueue(notification, Policy(trusted_orgs={"gisce"}))
    assert state == "enqueued"
    assert job is not None
    assert job.action == "workflow_run_failed"
    return job


def enqueue_sync_after_merge(queue: JobQueue):
    notification = Notification(
        uid=4,
        message_id="<pilipilisbot/github-agent-bridge/pull/96/merged@github.com>",
        subject="Re: [pilipilisbot/github-agent-bridge] feat: isolate OpenClaw sessions per work key (PR #96)",
        from_addr="notifications@github.com",
        body="Merged #96 into main. https://github.com/pilipilisbot/github-agent-bridge/pull/96",
    )
    job, state = queue.enqueue(notification, Policy(trusted_orgs={"pilipilisbot"}))
    assert state == "enqueued"
    assert job is not None
    assert job.action == "sync_after_merge"
    return job


def test_assigned_pr_comment_keeps_review_only_without_explicit_write_request(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=FakeGitHub(assigned=True), config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs[0].work_intent == "review_only"
    stored = queue.get(dispatcher.jobs[0].id)
    assert stored is not None
    assert stored.work_intent == "review_only"
    event_types = [event["event_type"] for event in job_session_events(queue.path, stored.id)]
    assert "action_mode_retained" in event_types


def test_executor_records_session_activity_events(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    queue = JobQueue(db)
    enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=FakeGitHub(assigned=False, mentioned=True), config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    event_types = [event["event_type"] for event in job_session_events(db, dispatcher.jobs[0].id)]
    assert event_types == ["claimed", "dispatch_started", "model_route_selected", "openclaw_stdout", "openclaw_stderr", "dispatch_finished", "done"]
    route_event = job_session_events(db, dispatcher.jobs[0].id)[2]
    assert route_event["detail"] == "OpenClaw default model route"
    stderr_event = job_session_events(db, dispatcher.jobs[0].id)[4]
    assert stderr_event["detail"] == "token=[redacted] [redacted]"


def test_dispatched_job_completion_pushes_trigger_actor(tmp_path, monkeypatch):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job, state = enqueue_pr_comment_from(queue, "ecarreras", 1, "<gisce/erp/pull/27315/ecarreras@github.com>", 1)
    assert state == "enqueued"
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True)
    notifications = []
    monkeypatch.setattr("github_agent_bridge.executor.notify_job_completion", lambda *args, **kwargs: notifications.append((args, kwargs)) or {"sent": 1})

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert notifications[0][0] == (queue.path,)
    assert notifications[0][1] == {
        "actors": ["ecarreras"],
        "job_id": job.id,
        "work_key": "gisce/erp#27315",
        "status": "done",
        "summary": "👀 reaction ok + agent dispatch queued",
        "detail": "followup_url=https://github.com/gisce/erp/issues/27315#issuecomment-2; ok",
        "followup_url": "https://github.com/gisce/erp/issues/27315#issuecomment-2",
    }


def test_dispatched_job_completion_pushes_coalesced_trigger_actors(tmp_path, monkeypatch):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job, state = enqueue_pr_comment_from(queue, "ecarreras", 1, "<gisce/erp/pull/27315/ecarreras@github.com>", 1)
    assert state == "enqueued"
    _, state = enqueue_pr_comment_from(queue, "marc", 2, "<gisce/erp/pull/27315/marc@github.com>", 2)
    assert state == "coalesced"
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True)
    notifications = []
    monkeypatch.setattr("github_agent_bridge.executor.notify_job_completion", lambda *args, **kwargs: notifications.append(kwargs) or {"sent": 1})

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert notifications[0]["actors"] == ["ecarreras", "marc"]
    assert notifications[0]["job_id"] == job.id


def test_skipped_job_does_not_emit_completion_push(tmp_path, monkeypatch):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    enqueue_pr_comment_from(queue, "ecarreras", 1, "<gisce/erp/pull/27315/ecarreras@github.com>", 1)
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=False, mentioned=False)
    notifications = []
    monkeypatch.setattr("github_agent_bridge.executor.notify_job_completion", lambda *args, **kwargs: notifications.append(kwargs) or {"sent": 1})

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs == []
    assert notifications == []


def test_executor_records_selected_model_route_session_event(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    queue = JobQueue(db)
    enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()
    policy = Policy(
        trusted_orgs={"gisce"},
        model_routes=ModelRoutes(
            by_intent={
                "review_only": ModelRoute(
                    model="openai/gpt-5.4-mini",
                    thinking="medium",
                )
            }
        ),
    )

    pool = ExecutorPool(queue, policy, dispatcher, github=FakeGitHub(assigned=False, mentioned=True), config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    events = job_session_events(db, dispatcher.jobs[0].id)
    route_event = next(event for event in events if event["event_type"] == "model_route_selected")
    assert route_event["summary"] == "OpenClaw model route selected"
    assert route_event["detail"] == "model=openai/gpt-5.4-mini thinking=medium"


def test_unassigned_mentioned_pr_comment_stays_review_only(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=FakeGitHub(assigned=False, mentioned=True), config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs[0].work_intent == "review_only"
    stored = queue.get(dispatcher.jobs[0].id)
    assert stored is not None
    assert stored.work_intent == "review_only"


def test_coalesced_notifications_are_reacted_to_before_dispatch(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    enqueue_pr_comment(queue)
    notification = Notification(
        uid=2,
        message_id="<gisce/erp/pull/27315/c2@github.com>",
        subject="Re: [gisce/erp] Permitir caller en los dominios (PR #27315)",
        from_addr="notifications@github.com",
        body="@pilipilisbot segon comentari https://github.com/gisce/erp/pull/27315#issuecomment-2",
    )
    job, state = queue.enqueue(notification, Policy(trusted_orgs={"gisce"}))
    assert state == "coalesced"
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=False, mentioned=True)

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert len(dispatcher.jobs) == 1
    assert dispatcher.jobs[0].id == job.id
    assert 2 in github.eye_comment_ids


def test_bot_authored_pr_review_comment_keeps_review_only_without_explicit_write_request(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=FakeGitHub(assigned=False, mentioned=True, authored=True), config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs[0].work_intent == "review_only"
    stored = queue.get(dispatcher.jobs[0].id)
    assert stored is not None
    assert stored.work_intent == "review_only"
    event_types = [event["event_type"] for event in job_session_events(queue.path, stored.id)]
    assert "action_mode_retained" in event_types


def test_unassigned_unmentioned_pr_comment_reacts_without_dispatch(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=False, mentioned=False)

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs == []
    assert github.eyes == 1
    assert github.acks == 1
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"


def test_first_attempt_dispatches_even_when_bot_already_commented_after_trigger(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True, answered_url="https://github.com/gisce/erp/pull/27315#issuecomment-2")

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert len(dispatcher.jobs) == 1
    assert dispatcher.jobs[0].id == job.id
    assert github.eyes == 1
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"


def test_retry_dispatches_even_when_prior_bot_comment_exists(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True, answered_url="https://github.com/gisce/erp/pull/27315#issuecomment-2")

    queue.requeue_running(job.id, "simulate retry")
    with queue.connect() as con:
        con.execute("UPDATE jobs SET attempts=1, status='pending' WHERE id=?", (job.id,))

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert len(dispatcher.jobs) == 1
    assert dispatcher.jobs[0].id == job.id
    assert github.eyes == 1
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"


def test_work_allowed_dispatch_auto_retries_once_without_visible_github_followup(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    queue.update_work_intent(job.id, "work_allowed", "explicit implementation request")
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.last_error is None
    assert stored.attempts == 1


def test_work_allowed_dispatch_blocks_after_auto_retry_without_visible_github_followup(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    queue.update_work_intent(job.id, "work_allowed", "explicit implementation request")
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True
    assert pool.work_one("worker-test") is True

    assert len(dispatcher.jobs) == 2
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "blocked"
    assert stored.last_error == "ok"
    assert stored.attempts == 2


def test_reply_comment_duplicate_noop_without_followup_is_done(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher(
        stdout=(
            "No GitHub follow-up comment was appropriate because the thread already contains "
            "the same answer. Adding another comment would be duplicate noise."
        )
    )
    github = FakeGitHub(assigned=True)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs[0].id == job.id
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.last_error is None


def test_workflow_run_failed_dispatch_does_not_require_thread_followup(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_workflow_run_failed(queue)
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=False, mentioned=False)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs
    assert github.eyes == 1
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"


def test_sync_after_merge_noop_duplicate_followup_is_done(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_sync_after_merge(queue)
    dispatcher = RecordingDispatcher(
        stdout=(
            "Post-merge cleanup rechecked for PR #96.\n"
            "No GitHub follow-up comment was appropriate because the thread already contains "
            "the same concrete cleanup/status. Adding another comment would be duplicate noise."
        )
    )
    github = FakeGitHub(assigned=False, mentioned=False)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"pilipilisbot"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs[0].id == job.id
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.last_error is None


def test_sync_after_merge_repeat_without_followup_is_done(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_sync_after_merge(queue)
    dispatcher = RecordingDispatcher(
        stdout=(
            "No GitHub follow-up was appropriate because this was a repeat sync-after-merge "
            "event with no new repository state; the prior cleanup note already covered it."
        )
    )
    github = FakeGitHub(assigned=False, mentioned=False)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"pilipilisbot"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.last_error is None


def test_transient_dispatch_failure_is_requeued(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher(
        stderr="GatewayClientRequestError: Error: CLI transcript compaction failed for openai/gpt-5.5: Summarization failed: Connection error.",
        ok=False,
        returncode=1,
    )

    github = FakeGitHub(assigned=True)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.last_error is None
    assert stored.attempts == 1
    assert stored.metadata["fresh_session_on_retry"] is True

    assert pool.work_one("worker-test") is True
    retry = dispatcher.jobs[-1]
    assert retry.attempts == 2
    events = job_session_events(queue.path, job.id, limit=50)
    assert any(event["event_type"] == "session_rescue_selected" for event in events)


def test_transient_dispatch_failure_blocks_after_retry_budget(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher(
        stderr="GatewayClientRequestError: Error: codex app-server client closed before turn completed",
        ok=False,
        returncode=1,
    )
    config = ExecutorConfig(run_once=True, transient_dispatch_retries=1)
    github = FakeGitHub(assigned=True)
    github.followup_url = None

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=config)
    assert pool.work_one("worker-test") is True
    assert pool.work_one("worker-test") is True

    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "blocked"
    assert "codex app-server client closed" in stored.last_error


def test_dispatch_failure_after_visible_followup_is_blocked_without_retry(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_comment(queue)
    dispatcher = RecordingDispatcher(
        stderr="GatewayClientRequestError: Error: CLI transcript compaction failed for openai/gpt-5.5: Summarization failed: Connection error.",
        ok=False,
        returncode=1,
    )
    github = FakeGitHub(assigned=True, answered_url="https://github.com/gisce/erp/issues/27315#issuecomment-2")

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "blocked"
    assert stored.attempts == 1
    assert "followup_url=https://github.com/gisce/erp/issues/27315#issuecomment-2" in stored.last_error
    assert "dispatch failed rc=1" in stored.last_error
    assert "CLI transcript compaction failed" in stored.last_error


def test_non_actionable_review_reacts_without_dispatch_even_when_assigned(tmp_path):
    queue = JobQueue(tmp_path / "bridge.sqlite3")
    job = enqueue_pr_review(queue)
    dispatcher = RecordingDispatcher()
    github = FakeGitHub(assigned=True, non_actionable_review=True)

    pool = ExecutorPool(queue, Policy(trusted_orgs={"gisce"}), dispatcher, github=github, config=ExecutorConfig(run_once=True))
    assert pool.work_one("worker-test") is True

    assert dispatcher.jobs == []
    assert github.eyes == 1
    assert github.acks == 1
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "done"
