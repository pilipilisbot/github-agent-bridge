from __future__ import annotations

import argparse
import email
import json
import mailbox
import os
from pathlib import Path

from .dispatch import GitHubClient, OpenClawDispatcher, RunMode
from .executor import ExecutorConfig, ExecutorPool
from .models import Notification
from .parser import decode_header_value, extract_body_text, parse_auth_results
from .policy import Policy
from .queue import JobQueue
from .reader import ImapConfig, ImapReader

DEFAULT_DB = os.path.expanduser("~/.local/state/github-agent-bridge/bridge.sqlite3")
DEFAULT_POLICY = os.path.expanduser("~/.config/github-agent-bridge/policy.json")


def load_policy(path: str | None) -> Policy:
    p = path or DEFAULT_POLICY
    return Policy.from_file(p) if Path(p).exists() else Policy()


def msg_to_notification(msg, uid: int | None = None) -> Notification | None:
    from_addr = decode_header_value(msg.get("From", ""))
    if "notifications@github.com" not in from_addr.lower():
        return None
    return Notification(
        uid=uid,
        message_id=decode_header_value(msg.get("Message-ID", "")),
        subject=decode_header_value(msg.get("Subject", "")),
        from_addr=from_addr,
        body=extract_body_text(msg),
        auth=parse_auth_results(msg),
    )


def cmd_init_db(args: argparse.Namespace) -> int:
    JobQueue(args.db)
    print(f"initialized {args.db}")
    return 0


def cmd_enqueue_json(args: argparse.Namespace) -> int:
    q = JobQueue(args.db); policy = load_policy(args.policy)
    data = json.loads(Path(args.file).read_text(encoding="utf-8")) if args.file != "-" else json.load(__import__("sys").stdin)
    n = Notification(**data)
    job, state = q.enqueue(n, policy)
    print(json.dumps({"state": state, "job_id": job.id if job else None, "work_key": job.work_key if job else None}, ensure_ascii=False))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    q = JobQueue(args.db); policy = load_policy(args.policy)
    path = Path(args.path)
    count = 0; skipped = 0
    messages = []
    if path.is_dir():
        messages = [email.message_from_bytes(p.read_bytes()) for p in sorted(path.iterdir()) if p.is_file()]
    elif args.format == "mbox":
        messages = list(mailbox.mbox(path))
    else:
        messages = [email.message_from_bytes(path.read_bytes())]
    for idx, msg in enumerate(messages, 1):
        n = msg_to_notification(msg, uid=args.uid_base + idx if args.uid_base is not None else None)
        if not n:
            skipped += 1; continue
        job, state = q.enqueue(n, policy)
        count += 1
        if args.verbose:
            print(json.dumps({"state": state, "job_id": job.id if job else None, "work_key": job.work_key if job else None, "subject": n.subject}, ensure_ascii=False))
    print(json.dumps({"github_messages": count, "skipped": skipped, "mode": "replay-no-side-effects"}, ensure_ascii=False))
    return 0


def cmd_read_imap_once(args: argparse.Namespace) -> int:
    q = JobQueue(args.db); policy = load_policy(args.policy)
    cfg = ImapConfig(args.imap_host, args.imap_port, args.email, args.password, args.mailbox)
    count = ImapReader(cfg, q, policy, mark_seen=args.mark_seen).fetch_once()
    print(json.dumps({"enqueued_or_seen": count, "mark_seen": args.mark_seen}, ensure_ascii=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    q = JobQueue(args.db); policy = load_policy(args.policy)
    mode = RunMode(args.mode)
    dispatcher = OpenClawDispatcher(
        args.openclaw_bin,
        args.node_bin,
        args.channel,
        args.to,
        args.timeout,
        mode=mode,
        review_timeout_seconds=args.review_timeout,
        work_timeout_seconds=args.work_timeout,
        cli_grace_seconds=args.cli_grace,
    )
    pool = ExecutorPool(q, policy, dispatcher, GitHubClient(args.gh_bin, mode=mode), ExecutorConfig(args.workers, args.idle_sleep, args.once))
    pool.run()
    return 0


def job_dict(job):
    return {"id": job.id, "work_key": job.work_key, "status": job.status, "action": job.action, "intent": job.work_intent, "attempts": job.attempts, "coalesced": job.coalesced_count, "updated_at": job.updated_at, "error": job.last_error}


def cmd_status(args: argparse.Namespace) -> int:
    q = JobQueue(args.db)
    print(json.dumps({"stats": q.stats(), "oldest_pending_age_seconds": q.pending_age_seconds()}, ensure_ascii=False, indent=2))
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    q = JobQueue(args.db)
    print(json.dumps([job_dict(j) for j in q.list_jobs(args.status, args.limit)], ensure_ascii=False, indent=2))
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    ok = JobQueue(args.db).retry(args.job_id)
    print(json.dumps({"job_id": args.job_id, "requeued": ok}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_unlock_stale(args: argparse.Namespace) -> int:
    n = JobQueue(args.db).unlock_stale(args.older_than)
    print(json.dumps({"unlocked": n}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="github-agent-bridge")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--policy", default=None)
    sub = p.add_subparsers(required=True)
    s = sub.add_parser("init-db"); s.set_defaults(func=cmd_init_db)
    s = sub.add_parser("enqueue-json"); s.add_argument("file"); s.set_defaults(func=cmd_enqueue_json)
    s = sub.add_parser("replay"); s.add_argument("path"); s.add_argument("--format", choices=["eml", "mbox"], default="eml"); s.add_argument("--uid-base", type=int); s.add_argument("--verbose", action="store_true"); s.set_defaults(func=cmd_replay)
    s = sub.add_parser("read-imap-once")
    s.add_argument("--imap-host", default=os.getenv("GITHUB_AGENT_BRIDGE_IMAP_HOST", "imap.gmail.com"))
    s.add_argument("--imap-port", type=int, default=int(os.getenv("GITHUB_AGENT_BRIDGE_IMAP_PORT", "993")))
    s.add_argument("--email", default=os.getenv("GITHUB_AGENT_BRIDGE_EMAIL", ""))
    s.add_argument("--password", default=os.getenv("GITHUB_AGENT_BRIDGE_PASSWORD", ""))
    s.add_argument("--mailbox", default="INBOX"); s.add_argument("--mark-seen", action="store_true", help="mark GitHub notifications as seen; leave off for shadow mode")
    s.set_defaults(func=cmd_read_imap_once)
    s = sub.add_parser("run")
    s.add_argument("--mode", choices=[m.value for m in RunMode], default=RunMode.SHADOW.value)
    s.add_argument("--workers", type=int, default=4); s.add_argument("--once", action="store_true")
    s.add_argument("--idle-sleep", type=float, default=1.0)
    s.add_argument("--timeout", type=int, default=3600, help="fallback OpenClaw agent timeout in seconds")
    s.add_argument("--review-timeout", type=int, default=900, help="OpenClaw agent timeout for review_only jobs")
    s.add_argument("--work-timeout", type=int, default=3600, help="OpenClaw agent timeout for work_allowed jobs")
    s.add_argument("--cli-grace", type=int, default=60, help="extra seconds the bridge waits for openclaw CLI cleanup after agent timeout")
    s.add_argument("--openclaw-bin", default=os.getenv("OPENCLAW_BIN", "openclaw")); s.add_argument("--node-bin", default=os.getenv("NODE_BIN"))
    s.add_argument("--gh-bin", default="gh"); s.add_argument("--channel", default="telegram"); s.add_argument("--to", default="43532269")
    s.set_defaults(func=cmd_run)
    s = sub.add_parser("status"); s.set_defaults(func=cmd_status)
    s = sub.add_parser("jobs"); s.add_argument("--status"); s.add_argument("--limit", type=int, default=20); s.set_defaults(func=cmd_jobs)
    s = sub.add_parser("retry"); s.add_argument("job_id", type=int); s.set_defaults(func=cmd_retry)
    s = sub.add_parser("unlock-stale"); s.add_argument("--older-than", type=int, default=1800); s.set_defaults(func=cmd_unlock_stale)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
