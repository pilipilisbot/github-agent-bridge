import json
import subprocess

from github_agent_bridge.dispatch import GitHubClient
from github_agent_bridge.models import GitHubContext


class RecordingGitHubClient(GitHubClient):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def _run(self, args):
        self.calls.append(args)
        key = tuple(args)
        stdout = self.responses.get(key, "")
        return subprocess.CompletedProcess([self.gh_bin, *args], 0, stdout, "")


def test_visible_followup_finds_review_comment_after_review_trigger():
    ctx = GitHubContext(
        urls=["https://github.com/gisce/erp/pull/27805#pullrequestreview-4325056741"],
        repo="gisce/erp",
        issue_number=27805,
        review_id=4325056741,
    )
    followup = {
        "user": {"login": "pilipilisbot"},
        "created_at": "2026-05-20T04:01:00Z",
        "html_url": "https://github.com/gisce/erp/pull/27805#discussion_r3271134328",
    }
    github = RecordingGitHubClient(
        {
            ("api", "user", "--jq", ".login"): "pilipilisbot\n",
            ("api", "repos/gisce/erp/pulls/27805/reviews/4325056741"): json.dumps({"submitted_at": "2026-05-20T03:59:00Z"}),
            ("api", "--paginate", "repos/gisce/erp/pulls/27805/comments", "--jq", ".[] | @json"): json.dumps(followup) + "\n",
        }
    )

    assert github.visible_followup_after_trigger(ctx) == followup["html_url"]


def test_post_completion_notification_mentions_human_actors_once():
    ctx = GitHubContext(
        urls=["https://github.com/gisce/erp/pull/27805"],
        repo="gisce/erp",
        issue_number=27805,
    )
    github = RecordingGitHubClient(
        {
            ("api", "user", "--jq", ".login"): "pilipilisbot\n",
            (
                "api",
                "-X",
                "POST",
                "repos/gisce/erp/issues/27805/comments",
                "-f",
                "body=@ecarreras @marc bridge job #42 for `gisce/erp#27805` finished with status `done`.\n\nagent dispatch queued\n\nFollow-up: https://github.com/gisce/erp/pull/27805#issuecomment-99",
                "--jq",
                ".html_url",
            ): "https://github.com/gisce/erp/pull/27805#issuecomment-100\n",
        }
    )

    url = github.post_completion_notification(
        ctx,
        actors=["ecarreras", "marc", "ecarreras", "copilot[bot]", "pilipilisbot"],
        job_id=42,
        work_key="gisce/erp#27805",
        status="done",
        summary="agent dispatch queued",
        followup_url="https://github.com/gisce/erp/pull/27805#issuecomment-99",
    )

    assert url == "https://github.com/gisce/erp/pull/27805#issuecomment-100"


def test_post_completion_notification_skips_without_recipients():
    ctx = GitHubContext(
        urls=["https://github.com/gisce/erp/pull/27805"],
        repo="gisce/erp",
        issue_number=27805,
    )
    github = RecordingGitHubClient({("api", "user", "--jq", ".login"): "pilipilisbot\n"})

    assert (
        github.post_completion_notification(
            ctx,
            actors=["pilipilisbot", "github", "copilot[bot]"],
            job_id=42,
            work_key="gisce/erp#27805",
            status="done",
            summary="agent dispatch queued",
        )
        is None
    )
    assert github.calls == [["api", "user", "--jq", ".login"]]


def test_visible_followup_finds_review_after_issue_comment_trigger():
    ctx = GitHubContext(
        urls=["https://github.com/pilipilisbot/github-agent-bridge/pull/53#issuecomment-4663425063"],
        repo="pilipilisbot/github-agent-bridge",
        issue_number=53,
        comment_id=4663425063,
    )
    followup = {
        "id": 4325056741,
        "user": {"login": "pilipilisbot"},
        "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-06-09T19:50:00Z",
        "html_url": "https://github.com/pilipilisbot/github-agent-bridge/pull/53#pullrequestreview-4325056741",
    }
    github = RecordingGitHubClient(
        {
            ("api", "user", "--jq", ".login"): "pilipilisbot\n",
            ("api", "repos/pilipilisbot/github-agent-bridge/issues/comments/4663425063"): json.dumps({"created_at": "2026-06-09T19:45:39Z"}),
            ("api", "--paginate", "repos/pilipilisbot/github-agent-bridge/issues/53/comments", "--jq", ".[] | @json"): "",
            ("api", "--paginate", "repos/pilipilisbot/github-agent-bridge/pulls/53/comments", "--jq", ".[] | @json"): "",
            ("api", "--paginate", "repos/pilipilisbot/github-agent-bridge/pulls/53/reviews", "--jq", ".[] | @json"): json.dumps(followup) + "\n",
        }
    )

    assert github.visible_followup_after_trigger(ctx) == followup["html_url"]


def test_visible_followup_ignores_review_comment_before_trigger():
    ctx = GitHubContext(
        urls=["https://github.com/gisce/erp/pull/27805#pullrequestreview-4325056741"],
        repo="gisce/erp",
        issue_number=27805,
        review_id=4325056741,
    )
    old_comment = {
        "user": {"login": "pilipilisbot"},
        "created_at": "2026-05-20T03:58:00Z",
        "html_url": "https://github.com/gisce/erp/pull/27805#discussion_old",
    }
    github = RecordingGitHubClient(
        {
            ("api", "user", "--jq", ".login"): "pilipilisbot\n",
            ("api", "repos/gisce/erp/pulls/27805/reviews/4325056741"): json.dumps({"submitted_at": "2026-05-20T03:59:00Z"}),
            ("api", "--paginate", "repos/gisce/erp/pulls/27805/comments", "--jq", ".[] | @json"): json.dumps(old_comment) + "\n",
            ("api", "--paginate", "repos/gisce/erp/issues/27805/comments", "--jq", ".[] | @json"): "",
        }
    )

    assert github.visible_followup_after_trigger(ctx) is None


def test_approved_review_is_non_actionable():
    ctx = GitHubContext(
        urls=["https://github.com/gisce/erp/pull/27805#pullrequestreview-4325056741"],
        repo="gisce/erp",
        issue_number=27805,
        review_id=4325056741,
    )
    github = RecordingGitHubClient(
        {
            ("api", "repos/gisce/erp/pulls/27805/reviews/4325056741"): json.dumps(
                {
                    "state": "APPROVED",
                    "body": "Looks good after the follow-up commit.",
                    "submitted_at": "2026-05-20T03:59:00Z",
                }
            ),
        }
    )

    assert github.is_non_actionable_review(ctx) is True


def test_visible_followup_for_issue_comment_returns_newest_bot_comment_after_trigger():
    ctx = GitHubContext(
        urls=["https://github.com/pilipilisbot/github-agent-bridge/pull/13#issuecomment-4524715895"],
        repo="pilipilisbot/github-agent-bridge",
        issue_number=13,
        comment_id=4524715895,
    )
    old_followup = {
        "user": {"login": "pilipilisbot"},
        "created_at": "2026-05-23T08:03:45Z",
        "html_url": "https://github.com/pilipilisbot/github-agent-bridge/pull/13#issuecomment-old",
    }
    new_followup = {
        "user": {"login": "pilipilisbot"},
        "created_at": "2026-05-23T09:10:40Z",
        "html_url": "https://github.com/pilipilisbot/github-agent-bridge/pull/13#issuecomment-new",
    }
    github = RecordingGitHubClient(
        {
            ("api", "user", "--jq", ".login"): "pilipilisbot\n",
            ("api", "repos/pilipilisbot/github-agent-bridge/issues/comments/4524715895"): json.dumps({"created_at": "2026-05-23T08:03:06Z"}),
            ("api", "--paginate", "repos/pilipilisbot/github-agent-bridge/issues/13/comments", "--jq", ".[] | @json"): "\n".join(
                [json.dumps(old_followup), json.dumps(new_followup)]
            )
            + "\n",
        }
    )

    assert github.visible_followup_after_trigger(ctx) == new_followup["html_url"]
