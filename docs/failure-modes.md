# Failure modes

## IMAP burst backlog

Reader must enqueue all new UIDs oldest-first and never wait for agent completion.

## Agent dispatch timeout

OpenClaw agent execution uses explicit per-intent timeouts:

- `review_only`: default 900 seconds.
- `work_allowed`: default 3600 seconds.

The bridge waits for the OpenClaw CLI with a small grace window after that agent timeout. If the CLI still has not returned, mark the job as `blocked`, store stderr/stdout summary, and release the `work_key` lock. This does not block IMAP reading or unrelated PRs/issues because dispatch happens in the executor pool, not the reader.

## Duplicate notifications for same PR/issue

If a job is already `pending`/`running` for the same `work_key`, coalesce the notification
instead of creating parallel agent work for the same thread.

## Reaction failure

Continue dispatching the agent if policy allows it, but record the reaction failure.
