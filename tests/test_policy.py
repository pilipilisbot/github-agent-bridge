from github_agent_bridge.models import Notification
from github_agent_bridge.parser import extract_github_context
from github_agent_bridge.policy import Policy


def test_trusted_org_auto_trusted():
    body = "@pilipilisbot https://github.com/gisce/erp/issues/1#issuecomment-1"
    n = Notification(1, "<x@github.com>", "subj", "notifications@github.com", body, auth={"spf": True, "dkim": True, "dmarc": True})
    ctx = extract_github_context(body)
    assert Policy(trusted_orgs={"gisce"}).decision(n, ctx, "reply_comment") == "auto_trusted"


def test_trusted_source_accepts_configured_forwarder():
    body = "@giscebot https://github.com/gisce/erp/pull/27853#issuecomment-4547966148"
    n = Notification(
        1,
        "<gisce/erp/pull/27853/c4547966148@github.com>",
        "subj",
        "'Eduard Carreras' via GISCE Bot <giscebot@gisce.net>",
        body,
        auth={"spf": True, "dkim": True, "dmarc": True},
    )
    ctx = extract_github_context(body)
    policy = Policy(source_from=("notifications@github.com", "giscebot@gisce.net"), trusted_orgs={"gisce"})

    assert policy.decision(n, ctx, "reply_comment") == "auto_trusted"


def test_enabled_repos_restricts_canary_scope():
    n = Notification(1, "<x@github.com>", "subj", "notifications@github.com", "", auth={"spf": True, "dkim": True, "dmarc": True})
    erp = extract_github_context("@pilipilisbot https://github.com/gisce/erp/issues/1#issuecomment-1")
    other = extract_github_context("@pilipilisbot https://github.com/gisce/other/issues/1#issuecomment-1")
    policy = Policy(trusted_orgs={"gisce"}, enabled_repos={"gisce/erp"})

    assert policy.decision(n, erp, "reply_comment") == "auto_trusted"
    assert policy.decision(n, other, "reply_comment") == "deny"
    assert policy.decision(n, other, "sync_after_merge") == "deny"


def test_repo_roles_precedence_and_default():
    policy = Policy(repo_roles={"gisce/erp": "owner"}, org_roles={"gisce": "maintainer"})

    assert policy.role_for("gisce/erp") == "owner"
    assert policy.role_for("gisce/other") == "maintainer"
    assert policy.role_for("other/repo") == "contributor"


def test_policy_from_file_loads_roles_and_rejects_unknown(tmp_path):
    valid = tmp_path / "policy.json"
    valid.write_text('{"repoRoles": {"GISCE/ERP": "Owner"}, "orgRoles": {"pilipilisbot": "maintainer"}}')
    policy = Policy.from_file(valid)
    assert policy.role_for("gisce/erp") == "owner"
    assert policy.role_for("pilipilisbot/github-agent-bridge") == "maintainer"

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"repoRoles": {"gisce/erp": "boss"}}')
    try:
        Policy.from_file(invalid)
    except ValueError as exc:
        assert "unknown repo role" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown repo role")


def test_policy_from_file_loads_bot_logins(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"botLogins": ["@GISCEBot", "pilipilisbot"]}')

    policy = Policy.from_file(policy_file)

    assert policy.bot_logins == {"giscebot", "pilipilisbot"}


def test_policy_keeps_default_bot_login_when_not_configured(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text("{}")

    assert Policy().bot_logins == {"pilipilisbot"}
    assert Policy.from_file(policy_file).bot_logins == {"pilipilisbot"}


def test_policy_allows_explicit_empty_bot_logins(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"botLogins": []}')

    assert Policy.from_file(policy_file).bot_logins == set()


def test_policy_from_file_loads_feedback_learning(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"feedbackLearning": {"enabled": false, "minConfidence": 0.7, "autoApproveConfidence": 0.9, "maxEventsPerRun": 3, "model": "test-model", "thinking": "medium", "sessionId": "feedback-test", "fallbackToDefaultModel": false}}')

    policy = Policy.from_file(policy_file)

    assert policy.feedback_learning.enabled is False
    assert policy.feedback_learning.min_confidence == 0.7
    assert policy.feedback_learning.auto_approve_confidence == 0.9
    assert policy.feedback_learning.max_events_per_run == 3
    assert policy.feedback_learning.model == "test-model"
    assert policy.feedback_learning.thinking == "medium"
    assert policy.feedback_learning.session_id == "feedback-test"
    assert policy.feedback_learning.fallback_to_default_model is False


def test_policy_from_file_loads_intent_classifier(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        """{
          "intentClassifier": {
            "enabled": true,
            "model": "gpt-5.4-mini",
            "thinking": "low",
            "minConfidence": 0.8,
            "onlyWhenParserDefaulted": false,
            "openclawBin": "/tmp/openclaw",
            "sessionId": "intent-test",
            "timeout": 12
          }
        }"""
    )

    policy = Policy.from_file(policy_file)

    assert policy.intent_classifier.enabled is True
    assert policy.intent_classifier.model == "gpt-5.4-mini"
    assert policy.intent_classifier.thinking == "low"
    assert policy.intent_classifier.min_confidence == 0.8
    assert policy.intent_classifier.only_when_parser_defaulted is False
    assert policy.intent_classifier.openclaw_bin == "/tmp/openclaw"
    assert policy.intent_classifier.session_id == "intent-test"
    assert policy.intent_classifier.timeout == 12


def test_policy_from_file_rejects_invalid_intent_classifier_confidence(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"intentClassifier": {"minConfidence": 1.5}}')

    try:
        Policy.from_file(policy_file)
    except ValueError as exc:
        assert "intentClassifier.minConfidence" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid intent classifier confidence")


def test_policy_from_file_loads_model_routes_and_resolution_order(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        """{
          "modelRoutes": {
            "default": {"model": "openai/gpt-5.5", "thinking": "medium"},
            "byIntent": {
              "review_only": {"model": "openai/gpt-5.4-mini", "thinking": "medium"}
            },
            "byAction": {
              "sync_after_merge": {"model": "openai/gpt-5.4-mini", "thinking": "low"}
            },
            "byComplexity": {
              "mechanical": {"model": "global-mechanical", "thinking": "low"}
            },
            "byRepo": {
              "GISCE/ERP": {
                "default": {"model": "repo-default", "thinking": "high"},
                "byIntent": {
                  "review_only": {"model": "repo-review", "thinking": "low"}
                },
                "byAction": {
                  "sync_after_merge": {"model": "repo-sync", "thinking": "minimal"}
                },
                "byComplexity": {
                  "mechanical": {"model": "repo-mechanical", "thinking": "low"}
                }
              }
            }
          }
        }"""
    )

    policy = Policy.from_file(policy_file)

    route = policy.model_route_for("gisce/erp", "sync_after_merge", "work_allowed")
    assert route.model == "repo-sync"
    assert route.thinking == "minimal"

    route = policy.model_route_for("gisce/erp", "reply_comment", "review_only")
    assert route.model == "repo-review"
    assert route.thinking == "low"

    route = policy.model_route_for("gisce/erp", "reply_comment", "work_allowed")
    assert route.model == "repo-default"
    assert route.thinking == "high"

    route = policy.model_route_for("gisce/erp", "reply_comment", "work_allowed", "mechanical")
    assert route.model == "repo-mechanical"
    assert route.thinking == "low"

    route = policy.model_route_for("other/repo", "sync_after_merge", "work_allowed")
    assert route.model == "openai/gpt-5.4-mini"
    assert route.thinking == "low"

    route = policy.model_route_for("other/repo", "reply_comment", "review_only")
    assert route.model == "openai/gpt-5.4-mini"
    assert route.thinking == "medium"

    route = policy.model_route_for("other/repo", "reply_comment", "work_allowed")
    assert route.model == "openai/gpt-5.5"
    assert route.thinking == "medium"

    route = policy.model_route_for("other/repo", "reply_comment", "work_allowed", "mechanical")
    assert route.model == "global-mechanical"
    assert route.thinking == "low"


def test_policy_from_file_rejects_invalid_model_route_thinking(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"modelRoutes": {"byAction": {"sync_after_merge": {"thinking": "turbo"}}}}')

    try:
        Policy.from_file(policy_file)
    except ValueError as exc:
        assert "modelRoutes.byAction.sync_after_merge.thinking" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid model route thinking")


def test_policy_from_file_rejects_unknown_model_route_complexity(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        '{"modelRoutes": {"byComplexity": {"easy": {"model": "cheap"}}}}'
    )

    try:
        Policy.from_file(policy_file)
    except ValueError as exc:
        assert "unknown complexity" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown model route complexity")


def test_policy_from_file_rejects_invalid_feedback_learning_confidence(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"feedbackLearning": {"minConfidence": 1.7}}')

    try:
        Policy.from_file(policy_file)
    except ValueError as exc:
        assert "feedbackLearning.minConfidence" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid feedback confidence")


def test_policy_from_file_rejects_invalid_feedback_learning_auto_approve(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"feedbackLearning": {"autoApproveConfidence": -0.1}}')

    try:
        Policy.from_file(policy_file)
    except ValueError as exc:
        assert "feedbackLearning.autoApproveConfidence" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid auto approve confidence")


def test_policy_from_file_loads_prompt_overrides_relative_to_policy(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "base.md").write_text("custom base {repo}\n")
    (prompts / "owner.md").write_text("custom owner\n")
    (prompts / "review_only.md").write_text("custom review only\n")
    (prompts / "feedback_classifier.md").write_text("custom feedback {event_json}\n")
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        """{
          "promptOverrides": {
            "base": "prompts/base.md",
            "roles": {"owner": "prompts/owner.md"},
            "intents": {"review_only": "prompts/review_only.md"},
            "rules": {"feedback_classifier": "prompts/feedback_classifier.md"}
          }
        }"""
    )

    policy = Policy.from_file(policy_file)

    assert policy.prompt_overrides.base == prompts / "base.md"
    assert policy.prompt_overrides.role_path("OWNER") == prompts / "owner.md"
    assert policy.prompt_overrides.intent_path("review_only") == prompts / "review_only.md"
    assert policy.prompt_overrides.rule_path("feedback_classifier") == prompts / "feedback_classifier.md"


def test_policy_from_file_rejects_invalid_prompt_overrides(tmp_path):
    missing = tmp_path / "missing.json"
    missing.write_text('{"promptOverrides": {"base": "nope.md"}}')
    try:
        Policy.from_file(missing)
    except ValueError as exc:
        assert "prompt override file does not exist" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing prompt override")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("content")
    unknown_role = tmp_path / "unknown-role.json"
    unknown_role.write_text('{"promptOverrides": {"roles": {"boss": "prompt.md"}}}')
    try:
        Policy.from_file(unknown_role)
    except ValueError as exc:
        assert "unknown prompt override role" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown prompt override role")

    unknown_rule = tmp_path / "unknown-rule.json"
    unknown_rule.write_text('{"promptOverrides": {"rules": {"boss": "prompt.md"}}}')
    try:
        Policy.from_file(unknown_rule)
    except ValueError as exc:
        assert "unknown prompt override rule" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown prompt override rule")

    empty = tmp_path / "empty.md"
    empty.write_text("   \n")
    empty_policy = tmp_path / "empty-policy.json"
    empty_policy.write_text('{"promptOverrides": {"intents": {"review_only": "empty.md"}}}')
    try:
        Policy.from_file(empty_policy)
    except ValueError as exc:
        assert "prompt override file is empty" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty prompt override")


def test_sync_after_merge_is_trusted_auto_by_default_not_auto():
    n = Notification(1, "<x@github.com>", "subj", "notifications@github.com", "", auth={"spf": True, "dkim": True, "dmarc": True})
    ctx = extract_github_context("https://github.com/gisce/erp/pull/1")

    assert Policy(trusted_orgs={"gisce"}).decision(n, ctx, "sync_after_merge") == "auto_trusted"
    assert Policy().decision(n, ctx, "sync_after_merge") == "ask"


def test_workflow_run_failed_is_trusted_auto_by_default_not_auto():
    body = "https://github.com/gisce/erp/actions/runs/26325244472"
    n = Notification(1, "<x@github.com>", "subj", "notifications@github.com", body, auth={"spf": True, "dkim": True, "dmarc": True})
    ctx = extract_github_context(body)

    assert Policy(trusted_orgs={"gisce"}).decision(n, ctx, "workflow_run_failed") == "auto_trusted"
    assert Policy().decision(n, ctx, "workflow_run_failed") == "ask"
