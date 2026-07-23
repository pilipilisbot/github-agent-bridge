# Failure modes

Known failures and expected containment behavior.

| Failure | Expected behavior | Operator response |
| --- | --- | --- |
| IMAP burst backlog | Reader enqueues oldest-first and never waits for agents. | Monitor pending age and worker capacity. |
| Agent dispatch timeout | Job becomes `blocked`; unrelated jobs continue. | Inspect `last_error`, then retry or fix policy/agent issue. |
| Duplicate notification | Notification coalesces into active `work_key`. | No action unless coalescing count is suspiciously high. |
| GitHub reaction failure | Agent dispatch continues if policy allows it. | Treat reaction as best-effort; inspect worklog if repeated. |
| Stale running job | Monitor alerts; lock can be released manually. | Use `unlock-stale` after confirming no live agent is still acting. |

## IMAP burst backlog

Reader must enqueue all new UIDs oldest-first and never wait for agent completion.

The queue/executor split exists specifically so a burst of GitHub mail does not stall mailbox cursor progress.

## Agent dispatch timeout

OpenClaw agent execution uses explicit per-intent timeouts:

| Intent | Default timeout |
| --- | --- |
| `review_only` | 900 seconds |
| `work_allowed` | 3600 seconds |

The bridge waits for the OpenClaw CLI with a small grace window after the agent timeout. If the CLI still has not returned, the job becomes `blocked`, stderr/stdout summary is stored, and the `work_key` lock is released.

## Duplicate notifications for same PR/issue

If a job is already `pending`, `running`, or `waiting_approval` for the same `work_key`, the new notification is stored in `coalesced_notifications` instead of starting parallel work for the same thread.

## Reaction failure

GitHub reactions are useful social signals, not the source of truth. If a 👀 reaction fails, dispatch continues when policy permits it and records the reaction failure.
