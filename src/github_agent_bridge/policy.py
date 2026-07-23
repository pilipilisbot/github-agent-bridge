from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import GitHubContext, Notification

ALLOWED_REPO_ROLES = {"owner", "maintainer", "contributor", "reviewer"}
ALLOWED_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"}
ALLOWED_PROMPT_INTENTS = {"review_only"}
ALLOWED_PROMPT_RULES = {
    "comment_value",
    "feedback_classifier",
    "feedback_learning",
    "human_reviewer",
    "intent_classifier",
    "pr_metadata",
    "pr_review",
    "prompt_injection",
    "repo_instructions",
    "sync_after_merge",
    "worktree",
}
DEFAULT_REPO_ROLE = "contributor"
DEFAULT_BOT_LOGINS = frozenset({"pilipilisbot"})


@dataclass(frozen=True)
class Route:
    agent: str | None = None
    channel: str | None = None
    to: str | None = None


@dataclass(frozen=True)
class ModelRoute:
    model: str | None = None
    thinking: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.model or self.thinking)

    def summary(self) -> str:
        parts = []
        if self.model:
            parts.append(f"model={self.model}")
        if self.thinking:
            parts.append(f"thinking={self.thinking}")
        return " ".join(parts) if parts else "OpenClaw default model route"


@dataclass(frozen=True)
class RepoModelRoutes:
    default: ModelRoute | None = None
    by_action: dict[str, ModelRoute] = field(default_factory=dict)
    by_intent: dict[str, ModelRoute] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRoutes:
    default: ModelRoute | None = None
    by_action: dict[str, ModelRoute] = field(default_factory=dict)
    by_intent: dict[str, ModelRoute] = field(default_factory=dict)
    by_repo: dict[str, RepoModelRoutes] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptOverrides:
    base: Path | None = None
    roles: dict[str, Path] = field(default_factory=dict)
    intents: dict[str, Path] = field(default_factory=dict)
    rules: dict[str, Path] = field(default_factory=dict)

    def role_path(self, role: str) -> Path | None:
        return self.roles.get(role.lower())

    def intent_path(self, intent: str) -> Path | None:
        return self.intents.get(intent.lower())

    def rule_path(self, rule: str) -> Path | None:
        return self.rules.get(rule.lower())


@dataclass(frozen=True)
class FeedbackLearning:
    enabled: bool = True
    min_confidence: float = 0.5
    auto_approve_confidence: float = 0.8
    max_events_per_run: int = 10
    model: str | None = None
    thinking: str = "low"
    session_id: str = "github-agent-bridge-feedback"
    fallback_to_default_model: bool = True


@dataclass(frozen=True)
class IntentClassifier:
    enabled: bool = False
    model: str | None = None
    thinking: str = "low"
    min_confidence: float = 0.75
    only_when_parser_defaulted: bool = True
    openclaw_bin: str = "openclaw"
    session_id: str = "github-agent-bridge-intent"
    timeout: int = 60


@dataclass(frozen=True)
class Policy:
    source_from: str | tuple[str, ...] = "notifications@github.com"
    required_url_prefix: str = "https://github.com/"
    message_id_domain: str = "github.com"
    trusted_repos: set[str] = field(default_factory=set)
    trusted_orgs: set[str] = field(default_factory=set)
    enabled_repos: set[str] = field(default_factory=set)
    auto_actions: set[str] = field(default_factory=lambda: {"archive_notification"})
    ask_actions: set[str] = field(default_factory=lambda: {"reply_comment", "open_issue", "docs_update", "content_change"})
    trusted_auto_actions: set[str] = field(default_factory=lambda: {"reply_comment", "open_issue", "submit_review", "sync_after_merge", "workflow_run_failed"})
    repo_routes: dict[str, Route] = field(default_factory=dict)
    org_routes: dict[str, Route] = field(default_factory=dict)
    repo_roles: dict[str, str] = field(default_factory=dict)
    org_roles: dict[str, str] = field(default_factory=dict)
    bot_logins: set[str] = field(default_factory=lambda: set(DEFAULT_BOT_LOGINS))
    model_routes: ModelRoutes = field(default_factory=ModelRoutes)
    prompt_overrides: PromptOverrides = field(default_factory=PromptOverrides)
    feedback_learning: FeedbackLearning = field(default_factory=FeedbackLearning)
    intent_classifier: IntentClassifier = field(default_factory=IntentClassifier)

    @classmethod
    def from_file(cls, path: str | Path) -> "Policy":
        policy_path = Path(path).expanduser()
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        source = data.get("source", {}); actions = data.get("actions", {})

        def routes(raw: dict) -> dict[str, Route]:
            return {k.lower(): Route(**v) for k, v in (raw or {}).items() if isinstance(v, dict)}

        def roles(raw: dict) -> dict[str, str]:
            result = {k.lower(): str(v).lower() for k, v in (raw or {}).items()}
            unknown = sorted(set(result.values()) - ALLOWED_REPO_ROLES)
            if unknown:
                raise ValueError(f"unknown repo role(s): {unknown}; allowed roles: {sorted(ALLOWED_REPO_ROLES)}")
            return result

        def model_route(raw: dict | None, path: str) -> ModelRoute | None:
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise ValueError(f"{path} must be an object")
            model = raw.get("model")
            thinking = raw.get("thinking")
            if thinking is not None:
                thinking = str(thinking).lower()
                if thinking not in ALLOWED_THINKING_LEVELS:
                    raise ValueError(f"{path}.thinking must be one of {sorted(ALLOWED_THINKING_LEVELS)}")
            return ModelRoute(
                model=str(model) if model else None,
                thinking=thinking,
            )

        def model_route_map(raw: dict | None, path: str) -> dict[str, ModelRoute]:
            if raw is not None and not isinstance(raw, dict):
                raise ValueError(f"{path} must be an object")
            routes: dict[str, ModelRoute] = {}
            for key, value in (raw or {}).items():
                route = model_route(value, f"{path}.{key}")
                if route:
                    routes[str(key).lower()] = route
            return routes

        def repo_model_routes(raw: dict | None, path: str) -> RepoModelRoutes:
            raw = raw or {}
            if not isinstance(raw, dict):
                raise ValueError(f"{path} must be an object")
            return RepoModelRoutes(
                default=model_route(raw.get("default"), f"{path}.default"),
                by_action=model_route_map(raw.get("byAction"), f"{path}.byAction"),
                by_intent=model_route_map(raw.get("byIntent"), f"{path}.byIntent"),
            )

        def model_routes(raw: dict | None) -> ModelRoutes:
            raw = raw or {}
            if not isinstance(raw, dict):
                raise ValueError("modelRoutes must be an object")
            by_repo = raw.get("byRepo") or {}
            if not isinstance(by_repo, dict):
                raise ValueError("modelRoutes.byRepo must be an object")
            return ModelRoutes(
                default=model_route(raw.get("default"), "modelRoutes.default"),
                by_action=model_route_map(raw.get("byAction"), "modelRoutes.byAction"),
                by_intent=model_route_map(raw.get("byIntent"), "modelRoutes.byIntent"),
                by_repo={str(repo).lower(): repo_model_routes(value, f"modelRoutes.byRepo.{repo}") for repo, value in by_repo.items()},
            )

        def prompt_path(raw_path: str) -> Path:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = policy_path.parent / candidate
            if not candidate.is_file():
                raise ValueError(f"prompt override file does not exist: {candidate}")
            if not candidate.read_text(encoding="utf-8").strip():
                raise ValueError(f"prompt override file is empty: {candidate}")
            return candidate

        def prompt_overrides(raw: dict) -> PromptOverrides:
            raw = raw or {}
            base = prompt_path(raw["base"]) if raw.get("base") else None
            raw_roles = raw.get("roles", {}) or {}
            role_names = {str(k).lower() for k in raw_roles}
            unknown_roles = sorted(role_names - ALLOWED_REPO_ROLES)
            if unknown_roles:
                raise ValueError(f"unknown prompt override role(s): {unknown_roles}; allowed roles: {sorted(ALLOWED_REPO_ROLES)}")
            raw_intents = raw.get("intents", {}) or {}
            intent_names = {str(k).lower() for k in raw_intents}
            unknown_intents = sorted(intent_names - ALLOWED_PROMPT_INTENTS)
            if unknown_intents:
                raise ValueError(f"unknown prompt override intent(s): {unknown_intents}; allowed intents: {sorted(ALLOWED_PROMPT_INTENTS)}")
            raw_rules = raw.get("rules", {}) or {}
            rule_names = {str(k).lower() for k in raw_rules}
            unknown_rules = sorted(rule_names - ALLOWED_PROMPT_RULES)
            if unknown_rules:
                raise ValueError(f"unknown prompt override rule(s): {unknown_rules}; allowed rules: {sorted(ALLOWED_PROMPT_RULES)}")
            return PromptOverrides(
                base=base,
                roles={str(k).lower(): prompt_path(v) for k, v in raw_roles.items()},
                intents={str(k).lower(): prompt_path(v) for k, v in raw_intents.items()},
                rules={str(k).lower(): prompt_path(v) for k, v in raw_rules.items()},
            )

        def feedback_learning(raw: dict) -> FeedbackLearning:
            raw = raw or {}
            min_confidence = float(raw.get("minConfidence", 0.5))
            if min_confidence < 0 or min_confidence > 1:
                raise ValueError("feedbackLearning.minConfidence must be between 0 and 1")
            auto_approve_confidence = float(raw.get("autoApproveConfidence", 0.8))
            if auto_approve_confidence < 0 or auto_approve_confidence > 1:
                raise ValueError("feedbackLearning.autoApproveConfidence must be between 0 and 1")
            max_events_per_run = int(raw.get("maxEventsPerRun", 10))
            if max_events_per_run < 1:
                raise ValueError("feedbackLearning.maxEventsPerRun must be at least 1")
            model = raw.get("model")
            return FeedbackLearning(
                enabled=bool(raw.get("enabled", True)),
                min_confidence=min_confidence,
                auto_approve_confidence=auto_approve_confidence,
                max_events_per_run=max_events_per_run,
                model=str(model) if model else None,
                thinking=str(raw.get("thinking", "low")),
                session_id=str(raw.get("sessionId", "github-agent-bridge-feedback")),
                fallback_to_default_model=bool(raw.get("fallbackToDefaultModel", True)),
            )

        def intent_classifier(raw: dict) -> IntentClassifier:
            raw = raw or {}
            min_confidence = float(raw.get("minConfidence", 0.75))
            if min_confidence < 0 or min_confidence > 1:
                raise ValueError("intentClassifier.minConfidence must be between 0 and 1")
            timeout = int(raw.get("timeout", 60))
            if timeout < 1:
                raise ValueError("intentClassifier.timeout must be at least 1")
            thinking = str(raw.get("thinking", "low")).lower()
            if thinking not in ALLOWED_THINKING_LEVELS:
                raise ValueError(f"intentClassifier.thinking must be one of {sorted(ALLOWED_THINKING_LEVELS)}")
            model = raw.get("model")
            return IntentClassifier(
                enabled=bool(raw.get("enabled", False)),
                model=str(model) if model else None,
                thinking=thinking,
                min_confidence=min_confidence,
                only_when_parser_defaulted=bool(raw.get("onlyWhenParserDefaulted", True)),
                openclaw_bin=str(raw.get("openclawBin", "openclaw")),
                session_id=str(raw.get("sessionId", "github-agent-bridge-intent")),
                timeout=timeout,
            )

        return cls(
            source_from=tuple(source.get("from")) if isinstance(source.get("from"), list) else source.get("from", cls.source_from),
            required_url_prefix=source.get("requiredUrlPrefix", cls.required_url_prefix),
            message_id_domain=source.get("messageIdDomain", cls.message_id_domain),
            trusted_repos={r.lower() for r in data.get("trustedRepos", [])},
            trusted_orgs={o.lower() for o in data.get("trustedOrgs", [])},
            enabled_repos={r.lower() for r in data.get("enabledRepos", [])},
            auto_actions=set(actions.get("auto", ["archive_notification"])),
            ask_actions=set(actions.get("ask", ["reply_comment", "open_issue", "docs_update", "content_change"])),
            trusted_auto_actions=set(actions.get("trustedAuto", ["reply_comment", "open_issue", "submit_review", "sync_after_merge", "workflow_run_failed"])),
            repo_routes=routes(data.get("repoRoutes", {})), org_routes=routes(data.get("orgRoutes", {})),
            repo_roles=roles(data.get("repoRoles", {})), org_roles=roles(data.get("orgRoles", {})),
            bot_logins=(
                {str(login).lower().lstrip("@") for login in data.get("botLogins", []) if str(login).strip()}
                if "botLogins" in data
                else set(DEFAULT_BOT_LOGINS)
            ),
            model_routes=model_routes(data.get("modelRoutes", {})),
            prompt_overrides=prompt_overrides(data.get("promptOverrides", {})),
            feedback_learning=feedback_learning(data.get("feedbackLearning", {})),
            intent_classifier=intent_classifier(data.get("intentClassifier", {})),
        )

    def trusted_source(self, n: Notification, ctx: GitHubContext) -> bool:
        auth_ok = all(bool(n.auth.get(k)) for k in ("spf", "dkim", "dmarc")) if n.auth else True
        sources = (self.source_from,) if isinstance(self.source_from, str) else self.source_from
        from_addr = n.from_addr.lower()
        source_ok = any(str(source).lower() in from_addr for source in sources)
        return source_ok and auth_ok and any(u.startswith(self.required_url_prefix) for u in ctx.urls) and self.message_id_domain in n.message_id

    def repo_trusted(self, repo: str | None) -> bool:
        if not repo:
            return False
        repo = repo.lower(); org = repo.split("/", 1)[0]
        return repo in self.trusted_repos or org in self.trusted_orgs

    def decision(self, n: Notification, ctx: GitHubContext, action: str) -> str:
        if self.enabled_repos and (ctx.repo or "").lower() not in self.enabled_repos:
            return "deny"
        if not self.trusted_source(n, ctx):
            return "deny"
        if action in self.auto_actions:
            return "auto"
        if action in self.trusted_auto_actions:
            return "auto_trusted" if self.repo_trusted(ctx.repo) else "ask"
        if action in self.ask_actions:
            return "ask"
        return "deny"

    def route_for(self, repo: str | None) -> Route:
        repo = (repo or "").lower(); org = repo.split("/", 1)[0] if "/" in repo else ""
        return self.repo_routes.get(repo) or self.org_routes.get(org) or Route()

    def role_for(self, repo: str | None) -> str:
        repo = (repo or "").lower(); org = repo.split("/", 1)[0] if "/" in repo else ""
        return self.repo_roles.get(repo) or self.org_roles.get(org) or DEFAULT_REPO_ROLE

    def model_route_for(self, repo: str | None, action: str, work_intent: str) -> ModelRoute:
        repo_key = (repo or "").lower()
        action_key = (action or "").lower()
        intent_key = (work_intent or "").lower()
        repo_routes = self.model_routes.by_repo.get(repo_key)
        if repo_routes:
            route = (
                repo_routes.by_action.get(action_key)
                or repo_routes.by_intent.get(intent_key)
                or repo_routes.default
            )
            if route:
                return route
        return (
            self.model_routes.by_action.get(action_key)
            or self.model_routes.by_intent.get(intent_key)
            or self.model_routes.default
            or ModelRoute()
        )
