from __future__ import annotations

import re
from email.message import Message
from email.header import decode_header

from .models import GitHubContext

REVIEW_ONLY_PATTERNS = ("fes-ne una review", "fes una review", "fes review", "fer una review", "fes-ne una revisio", "fes-ne una revisió", "fes una revisio", "fes una revisió", "fer una revisio", "fer una revisió", "review de la pr", "revisió de la pr", "revisio de la pr", "revisa aquesta pr", "revisa els canvis", "revisar els canvis", "com veus els canvis", "què et semblen els canvis", "que et semblen els canvis", "what do you think of these changes", "please review", "can you review")
IMPLEMENTATION_PATTERNS = ("fes els canvis", "fes-ho", "implementa", "modifica", "canvia", "arregla", "corregeix", "fix", "push", "commit", "aplica", "resol", "resolve")
ISSUE_CREATION_PATTERNS = (
    "crea la issue",
    "crea una issue",
    "obre la issue",
    "obre una issue",
    "create the issue",
    "create an issue",
    "open the issue",
    "open an issue",
)
BOT_MENTION_PATTERNS = ("you are receiving this because you were mentioned",)
ASSIGNMENT_PATTERNS = ("assigned you", "assigned to you", "you were assigned", "you are assigned")
REVIEW_REQUEST_PATTERNS = ("requested your review", "requested a review from you", "you were requested for review", "review requested")
COPILOT_REVIEW_PATTERNS = ("copilot-pull-request-reviewer", "github-copilot", "github copilot", "copilot reviewed", "copilot commented", "copilot left a comment", "copilot suggested", "copilot requested changes")
WORKFLOW_RUN_FAILED_PATTERNS = ("run failed", "workflow run failed", "workflow failed", "job failed", "failing after")
MERGE_EVENT_RE = re.compile(r"\b[\w.-]+\s+merged\s+(?:commit(?:\s+[0-9a-f]{7,40})?|[0-9a-f]{7,40}|#\d+)\s+(?:into|to)\s+[\w./-]+\b")


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    out = ""
    for part, enc in decode_header(value):
        out += part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
    return out.strip()


def extract_body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
    return (msg.get_payload(decode=True) or b"").decode(msg.get_content_charset() or "utf-8", "replace")


def parse_auth_results(msg: Message) -> dict[str, bool]:
    raw = "\n".join(msg.get_all("Authentication-Results", []))
    return {"spf": "spf=pass" in raw, "dkim": "dkim=pass" in raw, "dmarc": "dmarc=pass" in raw}


def is_github_notification_message(msg: Message, from_addr: str | None = None) -> bool:
    """Return True for direct GitHub notifications and Google Groups rewrites.

    GISCE routes GitHub mail through a Google Group, so incoming notifications can
    arrive as `From: ... via GISCE Bot <giscebot@gisce.net>` while retaining the
    GitHub reply address, message id and X-GitHub headers.
    """
    sender = (from_addr or decode_header_value(msg.get("From", ""))).lower()
    if "notifications@github.com" in sender:
        return True
    reply_to = decode_header_value(msg.get("Reply-To", "")).lower()
    message_id = decode_header_value(msg.get("Message-ID", "")).lower()
    return (
        bool(msg.get("X-GitHub-Recipient"))
        and bool(msg.get("X-GitHub-Reason"))
        and "@reply.github.com" in reply_to
        and "github.com" in message_id
    )


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


def _bot_patterns(bot_logins: set[str] | None) -> tuple[str, ...]:
    names = sorted({login.lower().lstrip("@") for login in (bot_logins or set()) if login.strip()})
    return tuple(pattern for name in names for pattern in (f"@{name}", name))


def github_event_flags(subject: str, body: str, bot_logins: set[str] | None = None) -> dict[str, bool]:
    text = f"{subject}\n{body}".lower()
    bot_patterns = _bot_patterns(bot_logins)
    assignment_patterns = ASSIGNMENT_PATTERNS + tuple(f"assigned {p}" for p in bot_patterns)
    review_patterns = REVIEW_REQUEST_PATTERNS + tuple(f"requested review from {p}" for p in bot_patterns) + tuple(f"requested {p}" for p in bot_patterns)
    return {"bot_mentioned": _contains_any(text, BOT_MENTION_PATTERNS + bot_patterns), "assigned": _contains_any(text, assignment_patterns), "review_requested": _contains_any(text, review_patterns), "copilot_review": _contains_any(text, COPILOT_REVIEW_PATTERNS)}


def _looks_like_pr_thread(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}".lower()
    return bool(re.search(r"\bpr #\d+\b|\bpull request #\d+\b", text) or re.search(r"github\.com/[^/]+/[^/]+/pull/\d+", text))


def classify_work_intent(subject: str, body: str, bot_logins: set[str] | None = None) -> str:
    text = f"{subject}\n{body}".lower()
    flags = github_event_flags(subject, body, bot_logins)
    asks_review = flags["review_requested"] or _contains_any(text, REVIEW_ONLY_PATTERNS)
    asks_implementation = _contains_any(text, IMPLEMENTATION_PATTERNS + ISSUE_CREATION_PATTERNS)
    if asks_review and not asks_implementation:
        return "review_only"
    # PR threads are review/discussion by default. Do not mutate a contributor's
    # branch from PR comments unless the human explicitly asks for implementation
    # or assigns the bot to own the PR/issue work.
    if _looks_like_pr_thread(subject, body) and not asks_implementation:
        return "review_only"
    return "work_allowed"


def _is_merge_notification(text: str, ctx: GitHubContext, message_id: str | None) -> bool:
    if "merged" not in text:
        return False
    normalized_message_id = (message_id or "").lower()
    if "/merged@" in normalized_message_id:
        return True
    if ctx.target_kind != "issue":
        return False
    return any("#event-" in url for url in ctx.urls) and bool(MERGE_EVENT_RE.search(text))


def classify_github_action(
    subject: str,
    body: str,
    bot_logins: set[str] | None = None,
    *,
    message_id: str | None = None,
) -> str:
    text = f"{subject}\n{body}".lower()
    flags = github_event_flags(subject, body, bot_logins)
    ctx = extract_github_context(body)
    if re.search(r"github\.com/[^/]+/[^/]+/actions/runs/\d+", text) and _contains_any(text, WORKFLOW_RUN_FAILED_PATTERNS):
        return "workflow_run_failed"
    if _is_merge_notification(text, ctx, message_id):
        return "sync_after_merge"
    # PR reviews/comments should be handled as replies even when GitHub's footer
    # also says the bot was assigned to the thread.
    if flags["review_requested"]:
        return "submit_review"
    if flags["copilot_review"] or "pullrequestreview" in text:
        return "reply_comment"
    if _contains_any(text, ISSUE_CREATION_PATTERNS):
        return "open_issue"
    if flags["assigned"]:
        return "open_issue"
    if flags["bot_mentioned"]:
        return "reply_comment"
    return "archive_notification"


def _github_urls(body: str) -> list[str]:
    urls: list[str] = []
    for raw_url in re.findall(r"https://github\.com/[^\s>]+", body):
        url = raw_url.rstrip('.,;:!?)"]\'')
        if url not in urls:
            urls.append(url)
    return urls


def _prioritize_url(urls: list[str], primary_url: str | None) -> list[str]:
    if primary_url is None:
        return urls
    return [primary_url, *[url for url in urls if url != primary_url]]


def extract_github_context(body: str) -> GitHubContext:
    urls = _github_urls(body)
    repo = None; issue_number = None; comment_id = None; review_id = None; review_comment_id = None; commit_comment_id = None; commit_sha = None; workflow_run_id = None; target_kind = None
    primary_url = None
    for url in urls:
        commit = re.search(r"github\.com/([^/]+/[^/]+)/commit/([0-9a-fA-F]+)", url)
        if commit:
            repo = commit.group(1).lower(); commit_sha = commit.group(2)
            cc = re.search(r"#r(\d+)", url)
            if cc:
                commit_comment_id = int(cc.group(1)); target_kind = "commit_comment"; primary_url = url; break
            target_kind = "commit"
            primary_url = primary_url or url
            continue
        m = re.search(r"github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", url)
        if not m:
            continue
        repo = m.group(1).lower(); issue_number = int(m.group(3))
        cm = re.search(r"issuecomment-(\d+)", url); rv = re.search(r"pullrequestreview-(\d+)", url); rc = re.search(r"discussion_r(\d+)", url)
        if cm:
            comment_id = int(cm.group(1)); target_kind = "issue_comment"; primary_url = url; break
        if rc:
            review_comment_id = int(rc.group(1)); target_kind = "review_comment"; primary_url = url; break
        if rv:
            review_id = int(rv.group(1)); target_kind = "review"; primary_url = primary_url or url; continue
        if target_kind is None:
            target_kind = "issue"; primary_url = primary_url or url
    if target_kind is None:
        for url in urls:
            workflow_run = re.search(r"github\.com/([^/]+/[^/]+)/actions/runs/(\d+)", url)
            if workflow_run:
                repo = workflow_run.group(1).lower(); workflow_run_id = int(workflow_run.group(2)); target_kind = "workflow_run"; primary_url = url
                break
    return GitHubContext(
        urls=_prioritize_url(urls, primary_url),
        repo=repo,
        issue_number=issue_number,
        comment_id=comment_id,
        review_id=review_id,
        review_comment_id=review_comment_id,
        commit_comment_id=commit_comment_id,
        commit_sha=commit_sha,
        target_kind=target_kind,
        workflow_run_id=workflow_run_id,
    )
