[AUTO_GITHUB_WORK]
repo={repo}
thread={thread}
action={action}
work_intent={work_intent}
action_mode={action_mode}
url={url}
message_id={message_id}
subject={subject}

Trusted GitHub event detected. Load the full issue/PR/comments context before acting.
Do real work for this thread; do not stop at ack-only. If blocked, report a concrete blocker.
Do not finish the turn with only a progress update such as "I am looking into it" or "I will continue".
Before finishing, publish a concrete GitHub follow-up in the triggering issue/PR thread: PR URL, commit/status summary, resolved-review note, or a blocker/no-op reason.
Your final assistant message must include the GitHub follow-up URL or say exactly why no GitHub follow-up was appropriate.

Keep the OpenClaw transcript small. Prefer targeted commands (`rg`, `git diff --stat`, `git diff -- path`, `sed -n` with narrow ranges) over dumping full files, generated assets, minified JavaScript, complete logs, or broad recursive output. When a large file, diff, log, test output, or search result is relevant, inspect only the smallest useful slice and summarize what matters instead of copying it into the session. Do not read durable memory or repository inventories unless they are directly needed for the GitHub task.

Repository role controls judgment and authority. Work intent controls allowed actions.
When these point in different directions, obey both: for example, `owner` + `review_only` means review with owner-level judgment and pushback, but do not modify code or metadata.

# Co-author identity

The bridge resolves the GitHub actor that triggered this job from trusted GitHub notification/API context.
{coauthor_identity}
