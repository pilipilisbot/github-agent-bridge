from github_agent_bridge.dispatch import GitHubClient, OpenClawDispatcher, RunMode
from github_agent_bridge.models import GitHubContext, Job
from github_agent_bridge.policy import Policy


def make_job(work_intent="work_allowed"):
    ctx = GitHubContext(["https://github.com/gisce/erp/pull/1#discussion_r2"], "gisce/erp", 1, review_comment_id=2)
    return Job(1, ctx.work_key, ctx.repo, ctx.issue_number, "running", "reply_comment", work_intent, "subject", "<x@github.com>", 1, ctx)


def test_shadow_github_reaction_has_no_external_failure():
    assert GitHubClient(gh_bin="definitely-not-present", mode=RunMode.SHADOW).react_eyes(make_job().context) is True


def test_shadow_dispatch_returns_command_without_running():
    result = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW).dispatch(make_job(), Policy(trusted_orgs={"gisce"}), reaction_ok=True)
    assert result.ok is True
    assert result.command
    assert "agent" in result.command
    assert "--timeout" in result.command
    assert "3600" in result.command


def test_review_only_dispatch_uses_shorter_timeout():
    dispatcher = OpenClawDispatcher(openclaw_bin="definitely-not-present", mode=RunMode.SHADOW)
    result = dispatcher.dispatch(make_job("review_only"), Policy(trusted_orgs={"gisce"}), reaction_ok=True)
    assert result.command
    timeout_idx = result.command.index("--timeout")
    assert result.command[timeout_idx + 1] == "900"
