from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager, suppress
import json
import os
import secrets
import shlex
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .autoupdate import apply_update_plan, complete_pending_reload, load_update_state, plan_update, record_update_plan, save_update_state
from .cancellation import cancel_running_job
from .cli import DEFAULT_DB
from .feedback import (
    approve_proposal,
    delete_rule,
    list_applicable_rules,
    list_events,
    list_proposals,
    list_repositories,
    list_rules,
    reject_proposal,
    update_rule_scope,
)
from .dashboard_data import (
    get_job_detail,
    inspect_db_read_only,
    job_logs,
    job_session,
    job_session_events,
    job_session_transcript,
    list_all_job_actor_logins,
    list_job_actors,
    list_jobs,
    metrics_summary,
    transcript_entry_from_session_event,
)
from .monitor import monitor
from .mcp import MCPServer, authenticate_token, create_token, list_tokens, revoke_token, update_token_owner
from .observability import configure_sentry, list_alerts, recent_process_samples
from .queue import JobQueue
from .systemd_status import allowed_unit_names, stream_journal_lines, systemd_status
from .web_push import delete_subscription, save_subscription, subscription_status


DEFAULT_HOST = os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_PORT", "8765"))
SESSION_COOKIE = "gab_dashboard_session"
OAUTH_STATE_COOKIE = "gab_dashboard_oauth_state"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_TEAMS_URL = "https://api.github.com/user/teams"
PROJECT_REPOSITORY_URL = "https://github.com/pilipilisbot/github-agent-bridge"
SESSION_VERSION = 1


def _knowledge_actor(item: dict[str, Any]) -> str:
    actor = item.get("trigger_actor") or (item.get("actor") if item.get("actor") != "github" else "")
    return str(actor or "").strip().lower()


def _knowledge_item_owned_by(item: dict[str, Any], login: str) -> bool:
    user = str(login or "").strip().lower()
    if not user:
        return False
    actor = _knowledge_actor(item)
    if actor == user:
        return True
    source_event = item.get("source_event")
    if isinstance(source_event, dict) and _knowledge_item_owned_by(source_event, user):
        return True
    source_events = item.get("source_event_details")
    if isinstance(source_events, list):
        return any(isinstance(event, dict) and _knowledge_item_owned_by(event, user) for event in source_events)
    return False


def _mark_manageable_knowledge(items: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    if profile.get("is_admin"):
        return [{**item, "can_manage": True} for item in items]
    login = str(profile.get("login") or "")
    return [{**item, "can_manage": _knowledge_item_owned_by(item, login)} for item in items]


class DashboardConfig:
    def __init__(
        self,
        *,
        db: str | Path = DEFAULT_DB,
        secret_key: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        allowed_users: set[str] | None = None,
        allowed_orgs: set[str] | None = None,
        allowed_teams: set[str] | None = None,
        admin_users: set[str] | None = None,
        admin_teams: set[str] | None = None,
        require_auth: bool = True,
        static_dir: str | Path | None = None,
        public_url: str | None = None,
        web_push_public_key: str | None = None,
    ) -> None:
        self.db = Path(db).expanduser()
        self.secret_key = secret_key or os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_SECRET_KEY", "")
        self.oauth_client_id = oauth_client_id or os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
        self.oauth_client_secret = oauth_client_secret or os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
        self.allowed_users = allowed_users if allowed_users is not None else _csv_env("GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_USERS")
        self.allowed_orgs = allowed_orgs if allowed_orgs is not None else _csv_env("GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_ORGS")
        self.allowed_teams = allowed_teams if allowed_teams is not None else _csv_env("GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_TEAMS")
        self.admin_users = admin_users if admin_users is not None else _csv_env("GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_USERS")
        self.admin_teams = admin_teams if admin_teams is not None else _csv_env("GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_TEAMS")
        self.require_auth = require_auth
        self.static_dir = Path(static_dir or os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_STATIC_DIR", Path(__file__).with_name("dashboard_static"))).expanduser()
        self.public_url = (public_url if public_url is not None else os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL", "")).rstrip("/")
        self.web_push_public_key = web_push_public_key if web_push_public_key is not None else os.getenv("GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PUBLIC_KEY", "")

    @property
    def oauth_ready(self) -> bool:
        return bool(self.secret_key and self.oauth_client_id and self.oauth_client_secret)

    @property
    def has_authorization_policy(self) -> bool:
        return bool(self.allowed_users or self.allowed_orgs or self.allowed_teams or self.admin_users or self.admin_teams)

    @property
    def has_admin_policy(self) -> bool:
        return bool(self.admin_users or self.admin_teams)


def _csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _autoupdate_systemd_units() -> dict[str, str]:
    return {
        "executor": _env("GITHUB_AGENT_BRIDGE_EXECUTOR_UNIT", "github-agent-bridge.service"),
        "dashboard": _env("GITHUB_AGENT_BRIDGE_DASHBOARD_UNIT", "github-agent-bridge-dashboard.service"),
        "reader": _env("GITHUB_AGENT_BRIDGE_READER_TIMER_UNIT", "github-agent-bridge-reader.timer"),
        "monitor": _env("GITHUB_AGENT_BRIDGE_MONITOR_TIMER_UNIT", "github-agent-bridge-monitor.timer"),
        "feedback": _env("GITHUB_AGENT_BRIDGE_FEEDBACK_TIMER_UNIT", "github-agent-bridge-feedback.timer"),
    }


def _dashboard_autoupdate_plan(db: str | Path) -> dict[str, Any]:
    return plan_update(
        db,
        repo=_env("GITHUB_AGENT_BRIDGE_AUTOUPDATE_REPO", "pilipilisbot/github-agent-bridge"),
        repo_dir=_env("GITHUB_AGENT_BRIDGE_AUTOUPDATE_REPO_DIR", "."),
        target_tag=_env("GITHUB_AGENT_BRIDGE_AUTOUPDATE_TARGET_TAG") or None,
        gh_bin=_env("GITHUB_AGENT_BRIDGE_GH_BIN", "gh"),
        systemd_units=_autoupdate_systemd_units(),
    )


def _dashboard_apply_autoupdate(plan: dict[str, Any], db: str | Path) -> dict[str, Any]:
    install_command = _env("GITHUB_AGENT_BRIDGE_AUTOUPDATE_INSTALL_COMMAND")
    return apply_update_plan(
        plan,
        db=db,
        repo=_env("GITHUB_AGENT_BRIDGE_AUTOUPDATE_REPO", "pilipilisbot/github-agent-bridge"),
        backup_dir=_env("GITHUB_AGENT_BRIDGE_AUTOUPDATE_BACKUP_DIR") or None,
        install_command=shlex.split(install_command) if install_command else None,
        systemctl_bin=_env("GITHUB_AGENT_BRIDGE_SYSTEMCTL_BIN", "systemctl"),
    )


def _record_dashboard_autoupdate_plan(db: str | Path, plan: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    state = record_update_plan(db, plan)
    if applied:
        state["dashboard_applied_at"] = state["updated_at"]
    else:
        state.pop("dashboard_applied_at", None)
        state["executor_reload_pending"] = False
    save_update_state(JobQueue(db), state)
    return state


def _redacted_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _dashboard_public_url(request: Request) -> str:
    url, _ = _dashboard_public_url_with_source(request)
    return url


def _dashboard_public_url_with_source(request: Request) -> tuple[str, str]:
    cfg: DashboardConfig = request.app.state.dashboard_config
    if cfg.public_url:
        return cfg.public_url, "configured"

    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    host = forwarded_host or request.headers.get("host", "").strip()
    if not host:
        return str(request.base_url).rstrip("/"), "request"

    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme
    forwarded_prefix = request.headers.get("x-forwarded-prefix", "").split(",", 1)[0].strip().rstrip("/")
    source = "forwarded" if forwarded_host or forwarded_proto or forwarded_prefix else "request"
    return f"{scheme}://{host}{forwarded_prefix}", source


def _sse_headers() -> dict[str, str]:
    return {
        **_redacted_headers(),
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def _sse_event(event: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _transcript_sse_key(entry: dict[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="mcp_token_required")
    return token.strip()


async def _sleep_or_shutdown(shutdown_event: asyncio.Event | None, sleep_seconds: float) -> bool:
    if shutdown_event is None:
        await asyncio.sleep(sleep_seconds)
        return False
    if shutdown_event.is_set():
        return True
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_seconds)
    except asyncio.TimeoutError:
        return False
    return True


async def _session_stream_events(db: str | Path, job_id: int, *, after_id: int | None = None, sleep_seconds: float = 2.0, shutdown_event: asyncio.Event | None = None):
    last_id = after_id or 0
    sent_transcript_keys: set[str] = set()
    while shutdown_event is None or not shutdown_event.is_set():
        emitted = False
        events = job_session_events(db, job_id, after_id=last_id, limit=100)
        for event in events:
            if shutdown_event is not None and shutdown_event.is_set():
                return
            last_id = int(event["id"])
            emitted = True
            yield _sse_event("session_event", event, event_id=last_id)
            entry = transcript_entry_from_session_event(event)
            if entry is not None:
                key = _transcript_sse_key(entry)
                if key not in sent_transcript_keys:
                    sent_transcript_keys.add(key)
                    yield _sse_event("transcript_entry", {"job_id": job_id, "entry": entry})
        transcript = job_session_transcript(db, job_id, limit=500)
        for entry in transcript:
            if shutdown_event is not None and shutdown_event.is_set():
                return
            key = _transcript_sse_key(entry)
            if key in sent_transcript_keys:
                continue
            sent_transcript_keys.add(key)
            emitted = True
            yield _sse_event("transcript_entry", {"job_id": job_id, "entry": entry})
        if not emitted:
            yield _sse_event("session_heartbeat", {"job_id": job_id, "last_event_id": last_id})
        if await _sleep_or_shutdown(shutdown_event, sleep_seconds):
            return


async def _journal_stream_events(unit: str, *, shutdown_event: asyncio.Event | None = None):
    try:
        stream = stream_journal_lines(unit)
        line_task: asyncio.Task | None = None
        shutdown_task: asyncio.Task | None = None
        try:
            while shutdown_event is None or not shutdown_event.is_set():
                line_task = asyncio.create_task(anext(stream))
                if shutdown_event is None:
                    try:
                        line = await line_task
                    except StopAsyncIteration:
                        return
                else:
                    shutdown_task = asyncio.create_task(shutdown_event.wait())
                    done, pending = await asyncio.wait({line_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if shutdown_task in done:
                        line_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await line_task
                        return
                    try:
                        line = line_task.result()
                    except StopAsyncIteration:
                        return
                yield _sse_event("journal_line", {"unit": unit, "line": line})
        finally:
            pending_tasks = [task for task in (line_task, shutdown_task) if task is not None and not task.done()]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            await stream.aclose()
    except FileNotFoundError:
        yield _sse_event("journal_error", {"unit": unit, "error": "journalctl_not_found"})


def _sign(config: DashboardConfig, value: str) -> str:
    import hmac
    import hashlib

    digest = hmac.new(config.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{value}.{digest}"


def _unsign(config: DashboardConfig, value: str) -> str | None:
    import hmac

    try:
        raw, digest = value.rsplit(".", 1)
    except ValueError:
        return None
    expected = _sign(config, raw).rsplit(".", 1)[1]
    return raw if hmac.compare_digest(digest, expected) else None


def _encode_session(user: dict[str, Any], *, is_admin: bool = False) -> str:
    payload = {
        "v": SESSION_VERSION,
        "login": str(user.get("login", "")).lower(),
        "avatar_url": str(user.get("avatar_url", "")),
        "html_url": str(user.get("html_url", "")),
        "is_admin": bool(is_admin),
    }
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_session(value: str) -> dict[str, Any] | None:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return _profile_from_login(value) if value else None
    login = str(payload.get("login", "")).lower()
    if not login:
        return None
    fallback = _profile_from_login(login)
    return {
        "login": login,
        "avatar_url": str(payload.get("avatar_url") or fallback["avatar_url"]),
        "html_url": str(payload.get("html_url") or fallback["html_url"]),
        "is_admin": bool(payload.get("is_admin", False)),
    }


def _profile_from_login(login: str) -> dict[str, Any]:
    user = str(login).lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", user):
        return {"login": user, "avatar_url": "", "html_url": "", "is_admin": False}
    return {
        "login": user,
        "avatar_url": f"https://github.com/{user}.png?size=80",
        "html_url": f"https://github.com/{user}",
        "is_admin": False,
    }


def _known_mcp_user_profiles(config: DashboardConfig, *, current_login: str = "") -> list[dict[str, Any]]:
    logins = {login.lower() for login in config.allowed_users | config.admin_users if login}
    if current_login:
        logins.add(current_login.lower())
    for actor_login in list_all_job_actor_logins(config.db):
        login = str(actor_login).strip().lower()
        if login:
            logins.add(login)
    for token in list_tokens(config.db, include_revoked=True):
        login = str(token.get("user_login") or "").strip().lower()
        if login:
            logins.add(login)
    return sorted((_profile_from_login(login) for login in logins), key=lambda item: item["login"])


def _require_known_mcp_owner(config: DashboardConfig, owner: str, *, current_login: str) -> str:
    clean_owner = owner.strip().lower().lstrip("@")
    known = {profile["login"] for profile in _known_mcp_user_profiles(config, current_login=current_login)}
    if clean_owner not in known:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mcp_token_owner_unknown")
    return clean_owner


def _github_json(url: str, token: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "User-Agent": "github-agent-bridge-dashboard"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _team_key(team: dict[str, Any]) -> str | None:
    org = team.get("organization")
    if not isinstance(org, dict):
        return None
    org_login = str(org.get("login", "")).lower()
    slug = str(team.get("slug", "")).lower()
    if not org_login or not slug:
        return None
    return f"{org_login}/{slug}"


def _exchange_code(config: DashboardConfig, code: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": config.oauth_client_id,
        "client_secret": config.oauth_client_secret,
        "code": code,
    }).encode("utf-8")
    req = urllib.request.Request(GITHUB_TOKEN_URL, data=data, headers={"Accept": "application/json", "User-Agent": "github-agent-bridge-dashboard"})
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="oauth_token_exchange_failed")
    return str(token)


def _is_allowed(config: DashboardConfig, login: str, token: str | None = None) -> bool:
    user = login.lower()
    if _is_admin(config, login, token):
        return True
    if config.allowed_users and user in config.allowed_users:
        return True
    if config.allowed_orgs and token:
        try:
            orgs = _github_json("https://api.github.com/user/orgs", token)
        except (urllib.error.URLError, TimeoutError):
            return False
        if any(str(org.get("login", "")).lower() in config.allowed_orgs for org in orgs if isinstance(org, dict)):
            return True
    if config.allowed_teams and token:
        try:
            teams = _github_json(GITHUB_TEAMS_URL, token)
        except (urllib.error.URLError, TimeoutError):
            return False
        return any(key in config.allowed_teams for key in (_team_key(team) for team in teams if isinstance(team, dict)) if key)
    return not config.has_authorization_policy


def _is_admin(config: DashboardConfig, login: str, token: str | None = None) -> bool:
    user = login.lower()
    if config.admin_users and user in config.admin_users:
        return True
    if config.admin_teams and token:
        try:
            teams = _github_json(GITHUB_TEAMS_URL, token)
        except (urllib.error.URLError, TimeoutError):
            return False
        return any(key in config.admin_teams for key in (_team_key(team) for team in teams if isinstance(team, dict)) if key)
    return False


def create_app(config: DashboardConfig | None = None) -> FastAPI:
    configure_sentry(service="dashboard")
    config = config or DashboardConfig()
    shutdown_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.dashboard_shutdown_event = shutdown_event
        try:
            yield
        finally:
            shutdown_event.set()

    app = FastAPI(title="GitHub Agent Bridge Dashboard API", lifespan=lifespan)
    app.state.dashboard_config = config
    app.state.dashboard_shutdown_event = shutdown_event
    assets_dir = config.static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="dashboard-assets")

    async def current_user(request: Request) -> str:
        profile = await current_profile(request)
        return str(profile["login"])

    async def current_admin_profile(request: Request) -> dict[str, Any]:
        profile = await current_profile(request)
        if not profile.get("is_admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")
        return profile

    async def current_profile(request: Request) -> dict[str, Any]:
        cfg: DashboardConfig = request.app.state.dashboard_config
        if not cfg.require_auth:
            return {"login": "test", "avatar_url": "", "html_url": "", "is_admin": True}
        signed = request.cookies.get(SESSION_COOKIE)
        if not signed or not cfg.secret_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not_authenticated")
        raw = _unsign(cfg, signed)
        profile = _decode_session(raw) if raw else None
        if not profile:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_authorized")
        return profile

    def can_cancel_job(job_id: int, profile: dict[str, Any]) -> bool:
        if profile.get("is_admin"):
            return True
        login = str(profile.get("login") or "").strip().lower()
        if not login:
            return False
        job = JobQueue(config.db).get(job_id)
        if job is None:
            return False
        actors = [job.trigger_actor, *JobQueue(config.db).coalesced_trigger_actors(job_id)]
        return login in {str(actor or "").strip().lstrip("@").lower() for actor in actors if actor}

    async def require_dashboard_profile_or_login(request: Request) -> RedirectResponse | None:
        try:
            await current_profile(request)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED and config.oauth_ready:
                return RedirectResponse("/auth/login", status_code=status.HTTP_302_FOUND)
            raise
        return None

    @app.exception_handler(sqlite3.OperationalError)
    async def database_unavailable(_: Request, exc: sqlite3.OperationalError) -> JSONResponse:
        return JSONResponse({"error": "database_unavailable", "detail": str(exc)}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, headers=_redacted_headers())

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        metrics = inspect_db_read_only(config.db)
        return {
            "ok": bool(metrics.get("db_exists") and metrics.get("schema_ok", True)),
            "service": "github-agent-bridge-dashboard",
            "db_exists": bool(metrics.get("db_exists")),
            "schema_ok": bool(metrics.get("schema_ok", True)),
            "oauth_configured": config.oauth_ready,
            "read_only": False,
        }

    def dashboard_index() -> FileResponse:
        index = config.static_dir / "index.html"
        if not index.exists():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dashboard_ui_not_built")
        return FileResponse(index, headers=_redacted_headers())

    @app.get("/")
    async def dashboard(request: Request) -> Response:
        redirect = await require_dashboard_profile_or_login(request)
        if redirect is not None:
            return redirect
        return dashboard_index()

    @app.get("/service-worker.js")
    async def service_worker(request: Request) -> Response:
        redirect = await require_dashboard_profile_or_login(request)
        if redirect is not None:
            return redirect
        worker = config.static_dir / "service-worker.js"
        if not worker.exists():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dashboard_service_worker_not_built")
        return FileResponse(worker, headers={**_redacted_headers(), "Service-Worker-Allowed": "/"})

    @app.get("/jobs/{job_path:path}")
    async def dashboard_job(job_path: str, request: Request) -> Response:
        redirect = await require_dashboard_profile_or_login(request)
        if redirect is not None:
            return redirect
        return dashboard_index()

    @app.get("/knowledge/{knowledge_path:path}")
    async def dashboard_knowledge(knowledge_path: str, request: Request) -> Response:
        redirect = await require_dashboard_profile_or_login(request)
        if redirect is not None:
            return redirect
        return dashboard_index()

    @app.get("/mcp")
    @app.get("/mcp/{mcp_path:path}")
    async def dashboard_mcp(request: Request, mcp_path: str = "") -> Response:
        redirect = await require_dashboard_profile_or_login(request)
        if redirect is not None:
            return redirect
        return dashboard_index()

    @app.get("/system/{system_path:path}")
    async def dashboard_system(system_path: str, request: Request) -> Response:
        redirect = await require_dashboard_profile_or_login(request)
        if redirect is not None:
            return redirect
        return dashboard_index()

    @app.get("/api/status")
    def api_status(request: Request, profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        queue = JobQueue(config.db)
        dashboard_url, dashboard_url_source = _dashboard_public_url_with_source(request)
        admin_actions = [
            "retry_job",
            "dismiss_job",
            "cancel_job",
            "approve_knowledge_proposal",
            "reject_knowledge_proposal",
            "update_knowledge_rule_scope",
            "delete_knowledge_rule",
            "create_mcp_token",
            "revoke_mcp_token",
        ]
        if profile.get("is_admin"):
            admin_actions.extend(["view_autoupdate_plan", "refresh_autoupdate_plan", "apply_autoupdate", "complete_autoupdate_reload"])
        return {
            "service": "github-agent-bridge-dashboard",
            "read_only": False,
            "dashboard_url": dashboard_url,
            "dashboard_url_source": dashboard_url_source,
            "admin_actions": admin_actions,
            "metrics": inspect_db_read_only(config.db),
            "autoupdate": load_update_state(queue) if profile.get("is_admin") else {},
        }

    @app.post("/api/autoupdate/refresh")
    def api_autoupdate_refresh(_: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        plan = _dashboard_autoupdate_plan(config.db)
        state = _record_dashboard_autoupdate_plan(config.db, plan, applied=False)
        return {"plan": plan, "state": state}

    @app.post("/api/autoupdate/apply")
    def api_autoupdate_apply(_: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        plan = _dashboard_autoupdate_plan(config.db)
        execution = _dashboard_apply_autoupdate(plan, config.db)
        state = _record_dashboard_autoupdate_plan(
            config.db,
            plan,
            applied=bool(execution.get("applied") and not execution.get("blocked")),
        )
        payload = {"plan": plan, "execution": execution, "state": state}
        if execution.get("blocked") or not execution.get("applied"):
            return JSONResponse(payload, status_code=status.HTTP_409_CONFLICT)
        return payload

    @app.post("/api/autoupdate/complete-pending")
    def api_autoupdate_complete_pending(_: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        state = load_update_state(JobQueue(config.db))
        if not state.get("dashboard_applied_at"):
            payload = {
                "completion": {
                    "completed": False,
                    "blocked": ["autoupdate_not_applied"],
                    "commands": [],
                    "state": state,
                },
                "state": state,
            }
            return JSONResponse(payload, status_code=status.HTTP_409_CONFLICT)
        completion = complete_pending_reload(
            config.db,
            systemctl_bin=_env("GITHUB_AGENT_BRIDGE_SYSTEMCTL_BIN", "systemctl"),
        )
        payload = {"completion": completion, "state": load_update_state(JobQueue(config.db))}
        if completion.get("blocked") or not completion.get("completed"):
            return JSONResponse(payload, status_code=status.HTTP_409_CONFLICT)
        return payload

    @app.get("/api/about")
    def api_about(_: str = Depends(current_user)) -> dict[str, Any]:
        return {
            "service": "github-agent-bridge-dashboard",
            "version": __version__,
            "repository_url": PROJECT_REPOSITORY_URL,
        }

    @app.get("/api/me")
    def api_me(profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        return {"user": profile}

    @app.get("/api/web-push/config")
    def api_web_push_config(profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        return {
            "public_key": config.web_push_public_key,
            "configured": bool(config.web_push_public_key),
            "status": subscription_status(config.db, str(profile["login"])),
        }

    @app.post("/api/web-push/subscriptions")
    async def api_web_push_subscribe(request: Request, profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        if not config.web_push_public_key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="web_push_not_configured")
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription_required")
        try:
            subscription = save_subscription(config.db, str(profile["login"]), payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"subscription": subscription, "status": subscription_status(config.db, str(profile["login"]))}

    @app.delete("/api/web-push/subscriptions")
    async def api_web_push_unsubscribe(request: Request, profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc
        endpoint = str(payload.get("endpoint") or "") if isinstance(payload, dict) else ""
        if not endpoint:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription_endpoint_required")
        removed = delete_subscription(config.db, str(profile["login"]), endpoint)
        return {"removed": removed, "status": subscription_status(config.db, str(profile["login"]))}

    @app.get("/api/jobs")
    def api_jobs(
        _: str = Depends(current_user),
        status_filter: str | None = Query(default=None, alias="status"),
        repo: str | None = None,
        thread: int | None = None,
        action: str | None = None,
        intent: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return {
            "jobs": list_jobs(
                config.db,
                status_filter=status_filter,
                repo=repo,
                thread=thread,
                action=action,
                intent=intent,
                actor=actor,
                since=since,
                until=until,
                limit=limit,
            )
        }

    @app.get("/api/jobs/actors")
    def api_job_actors(_: str = Depends(current_user), limit: int = 100) -> dict[str, Any]:
        return {"actors": list_job_actors(config.db, limit=limit)}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: int, _: str = Depends(current_user)) -> dict[str, Any]:
        job = get_job_detail(config.db, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        return {"job": job}

    @app.get("/api/jobs/{job_id}/logs")
    def api_job_logs(job_id: int, limit: int = 100, _: str = Depends(current_user)) -> dict[str, Any]:
        return {"logs": job_logs(config.db, job_id, limit=limit)}

    @app.post("/api/jobs/{job_id}/retry")
    def api_job_retry(job_id: int, profile: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        if get_job_detail(config.db, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        if not JobQueue(config.db).retry(job_id, actor=str(profile["login"])):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_not_retryable")
        job = get_job_detail(config.db, job_id)
        return {"job": job, "detail": "job_requeued"}

    @app.post("/api/jobs/{job_id}/dismiss")
    def api_job_dismiss(job_id: int, profile: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        if get_job_detail(config.db, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        if not JobQueue(config.db).dismiss(job_id, f"dismissed by @{profile['login']}"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_not_dismissable")
        job = get_job_detail(config.db, job_id)
        return {"job": job, "detail": "job_dismissed"}

    @app.post("/api/jobs/{job_id}/cancel")
    async def api_job_cancel(job_id: int, request: Request, profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        if get_job_detail(config.db, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        if not can_cancel_job(job_id, profile):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="job_cancel_not_allowed")
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cancel_payload_required")
        reason = str(payload.get("reason") or "").strip() or None
        result = cancel_running_job(JobQueue(config.db), job_id, actor=str(profile["login"]), reason=reason)
        if not result.cancelled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job_not_running")
        job = get_job_detail(config.db, job_id)
        return {
            "job": job,
            "detail": "job_cancelled",
            "signalled": result.signalled,
            "signal_detail": result.detail,
            "followup_url": result.followup_url,
        }

    @app.get("/api/jobs/{job_id}/session")
    def api_job_session(job_id: int, _: str = Depends(current_user)) -> dict[str, Any]:
        session = job_session(config.db, job_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        return {"session": session}

    @app.get("/api/jobs/{job_id}/session/events")
    def api_job_session_events(job_id: int, after_id: int | None = None, limit: int = 100, _: str = Depends(current_user)) -> dict[str, Any]:
        if job_session(config.db, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        return {"events": job_session_events(config.db, job_id, after_id=after_id, limit=limit)}

    @app.get("/api/jobs/{job_id}/session/transcript")
    def api_job_session_transcript(job_id: int, limit: int = 500, _: str = Depends(current_user)) -> dict[str, Any]:
        if job_session(config.db, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        return {"entries": job_session_transcript(config.db, job_id, limit=limit)}

    @app.get("/api/jobs/{job_id}/session/stream")
    def api_job_session_stream(job_id: int, after_id: int | None = None, _: str = Depends(current_user)) -> StreamingResponse:
        if job_session(config.db, job_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")

        return StreamingResponse(
            _session_stream_events(config.db, job_id, after_id=after_id, shutdown_event=app.state.dashboard_shutdown_event),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    @app.get("/api/metrics/summary")
    def api_metrics(timezone: str = "UTC", _: str = Depends(current_user)) -> dict[str, Any]:
        return {"metrics": metrics_summary(config.db, timezone_name=timezone)}

    @app.get("/api/processes")
    def api_processes(_: str = Depends(current_user)) -> dict[str, Any]:
        report = monitor(config.db)
        metrics = report.metrics
        samples = recent_process_samples(config.db, limit=60)
        latest_sample = samples[-1] if samples else None
        running_jobs = metrics.get("running_jobs", [])
        return {
            "running_jobs": running_jobs,
            "executor": {
                "service": metrics.get("executor_service", "unknown"),
                "pid": metrics.get("executor_pid"),
                "children": metrics.get("executor_children", []),
            },
            "signals": {
                "live_process": {
                    "state": "live" if metrics.get("executor_children") else "no_child_process",
                    "child_count": len(metrics.get("executor_children", []) or []),
                },
                "process_activity": {
                    "state": "active" if latest_sample and latest_sample.get("active_since_last_sample") else "quiet",
                    "idle_seconds": latest_sample.get("idle_seconds") if latest_sample else None,
                    "sample_ts": latest_sample.get("ts") if latest_sample else None,
                },
                "semantic_progress": [job for job in running_jobs if job.get("semantic_progress")],
                "visible_progress": [job for job in running_jobs if job.get("visible_progress")],
            },
            "alerts": report.alerts,
            "samples": samples,
            "detail": "Live process state, persisted process activity samples, semantic job heartbeats and visible OpenClaw output are reported separately.",
        }

    @app.get("/api/systemd")
    def api_systemd(_: str = Depends(current_user)) -> dict[str, Any]:
        return systemd_status()

    @app.get("/api/systemd/journal/stream")
    def api_systemd_journal_stream(unit: str, _: str = Depends(current_user)) -> StreamingResponse:
        if unit not in allowed_unit_names():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="systemd_unit_not_allowed")
        return StreamingResponse(_journal_stream_events(unit, shutdown_event=app.state.dashboard_shutdown_event), media_type="text/event-stream", headers=_sse_headers())

    @app.get("/api/alerts")
    def api_alerts(include_resolved: bool = False, limit: int = 50, _: str = Depends(current_user)) -> dict[str, Any]:
        return {
            "alerts": list_alerts(config.db, include_resolved=include_resolved, limit=limit),
            "detail": "Persistent monitor alert observations; unresolved alerts are active.",
        }

    @app.get("/api/knowledge")
    def api_knowledge(
        profile: dict[str, Any] = Depends(current_profile),
        repo: str | None = None,
        proposal_status: str | None = Query(default=None, alias="status"),
        limit: int = 50,
    ) -> dict[str, Any]:
        scope = f"repo:{repo.strip().lower()}" if repo and repo.strip() else ""
        status_filter = (proposal_status or "").strip().lower()
        proposals = list_proposals(config.db, status=status_filter, limit=limit)
        if scope:
            proposals = [item for item in proposals if item["scope"] == scope or item["scope"].startswith(f"{scope}:")]
        events = list_events(config.db, scope=scope, limit=limit)
        rules = list_applicable_rules(config.db, repo.strip().lower(), min_confidence=0) if scope else list_rules(config.db, min_confidence=0)
        proposals = _mark_manageable_knowledge(proposals, profile)
        events = _mark_manageable_knowledge(events, profile)
        rules = _mark_manageable_knowledge(rules, profile)
        return {
            "repositories": list_repositories(config.db),
            "events": events,
            "proposals": proposals,
            "rules": rules,
            "summary": {
                "events": len(events),
                "rules": len(rules),
                "proposed": sum(1 for item in proposals if item["status"] == "proposed"),
                "approved": sum(1 for item in proposals if item["status"] == "approved"),
                "rejected": sum(1 for item in proposals if item["status"] == "rejected"),
                "errors": sum(1 for item in proposals if item["status"] == "error"),
            },
        }

    @app.post("/api/knowledge/proposals/{proposal_id}/approve")
    def api_knowledge_approve(proposal_id: str, _: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        proposal = approve_proposal(config.db, proposal_id, react=True, gh_bin=os.getenv("GITHUB_AGENT_BRIDGE_GH_BIN", "gh"))
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge_proposal_not_found")
        return {"proposal": proposal, "detail": "knowledge_proposal_approved"}

    @app.post("/api/knowledge/proposals/{proposal_id}/reject")
    def api_knowledge_reject(proposal_id: str, _: dict[str, Any] = Depends(current_admin_profile)) -> dict[str, Any]:
        proposal = reject_proposal(config.db, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge_proposal_not_found")
        return {"proposal": proposal, "detail": "knowledge_proposal_rejected"}

    @app.delete("/api/knowledge/rules/{rule_id}")
    def api_knowledge_rule_delete(rule_id: str, profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        rule = next((item for item in list_rules(config.db, min_confidence=0) if item["id"] == rule_id), None)
        if rule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge_rule_not_found")
        if not profile.get("is_admin") and not _knowledge_item_owned_by(rule, str(profile.get("login") or "")):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="knowledge_rule_owner_required")
        if not delete_rule(config.db, rule_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge_rule_not_found")
        return {"detail": "knowledge_rule_deleted"}

    @app.patch("/api/knowledge/rules/{rule_id}")
    def api_knowledge_rule_update(rule_id: str, payload: dict[str, Any], profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        existing = next((item for item in list_rules(config.db, min_confidence=0) if item["id"] == rule_id), None)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge_rule_not_found")
        if not profile.get("is_admin") and not _knowledge_item_owned_by(existing, str(profile.get("login") or "")):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="knowledge_rule_owner_required")
        try:
            rule = update_rule_scope(config.db, rule_id, str(payload.get("scope") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if rule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge_rule_not_found")
        return {"rule": {**rule, "can_manage": True}, "detail": "knowledge_rule_updated"}

    @app.get("/api/mcp/tokens")
    def api_mcp_tokens(profile: dict[str, Any] = Depends(current_profile), include_revoked: bool = False) -> dict[str, Any]:
        owner = None if profile.get("is_admin") else str(profile.get("login") or "")
        return {"tokens": list_tokens(config.db, include_revoked=include_revoked, user_login=owner)}

    @app.get("/api/mcp/users")
    def api_mcp_users(profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        if not profile.get("is_admin"):
            return {"users": [_profile_from_login(str(profile.get("login") or ""))]}
        return {"users": _known_mcp_user_profiles(config, current_login=str(profile.get("login") or ""))}

    @app.post("/api/mcp/tokens")
    def api_mcp_token_create(payload: dict[str, Any], profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        login = str(profile.get("login") or "")
        requested_owner = str(payload.get("user_login") or "").strip()
        if requested_owner and not profile.get("is_admin") and requested_owner.lower().lstrip("@") != login.lower():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")
        owner = _require_known_mcp_owner(config, requested_owner or login, current_login=login) if profile.get("is_admin") else requested_owner or login
        try:
            created = create_token(config.db, str(payload.get("name") or ""), expires_at=payload.get("expires_at"), user_login=owner, created_by=login)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"token": created["token"], "record": created["record"], "detail": "mcp_token_created"}

    @app.patch("/api/mcp/tokens/{token_id}")
    def api_mcp_token_update(token_id: str, payload: dict[str, Any], profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        if not profile.get("is_admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")
        login = str(profile.get("login") or "")
        owner = _require_known_mcp_owner(config, str(payload.get("user_login") or ""), current_login=login)
        try:
            record = update_token_owner(config.db, token_id, owner)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mcp_token_not_found")
        return {"token": record, "detail": "mcp_token_updated"}

    @app.delete("/api/mcp/tokens/{token_id}")
    def api_mcp_token_revoke(token_id: str, profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        owner = None if profile.get("is_admin") else str(profile.get("login") or "")
        if not revoke_token(config.db, token_id, user_login=owner):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mcp_token_not_found")
        return {"detail": "mcp_token_revoked"}

    @app.post("/api/mcp")
    @app.post("/api/mcp/")
    async def api_mcp_http(request: Request) -> Response:
        if authenticate_token(config.db, _bearer_token(request)) is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_mcp_token")
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mcp_request_must_be_object")

        response = MCPServer(config.db).handle(payload)
        if response is None:
            return Response(status_code=status.HTTP_202_ACCEPTED, headers=_redacted_headers())
        return JSONResponse(response, headers=_redacted_headers())

    @app.get("/api/events/stream")
    def api_events(_: str = Depends(current_user)) -> Response:
        return Response("event: ready\ndata: {}\n\n", media_type="text/event-stream", headers=_sse_headers())

    @app.get("/auth/login")
    def login() -> RedirectResponse:
        if not config.oauth_ready:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="oauth_not_configured")
        state = secrets.token_urlsafe(24)
        scopes = ["read:user"]
        if config.allowed_orgs or config.allowed_teams or config.admin_teams:
            scopes.append("read:org")
        params = urllib.parse.urlencode({"client_id": config.oauth_client_id, "scope": " ".join(scopes), "state": state})
        response = RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{params}", status_code=status.HTTP_302_FOUND)
        response.set_cookie(OAUTH_STATE_COOKIE, _sign(config, state), httponly=True, secure=True, samesite="lax", max_age=600)
        return response

    @app.get("/auth/callback")
    def callback(code: str, state: str, request: Request) -> RedirectResponse:
        if not config.oauth_ready:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="oauth_not_configured")
        signed_state = request.cookies.get(OAUTH_STATE_COOKIE)
        if not signed_state or _unsign(config, signed_state) != state:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="oauth_state_mismatch")
        token = _exchange_code(config, code)
        user = _github_json(GITHUB_USER_URL, token)
        login = str(user.get("login", ""))
        if not login or not _is_allowed(config, login, token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_authorized")
        is_admin = _is_admin(config, login, token)
        response = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(SESSION_COOKIE, _sign(config, _encode_session(user, is_admin=is_admin)), httponly=True, secure=True, samesite="lax")
        response.delete_cookie(OAUTH_STATE_COOKIE)
        return response

    return app


app = create_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=Path(sys.argv[0]).name)
    parser.add_argument("--db", default=os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_DB", os.getenv("GITHUB_AGENT_BRIDGE_DB", DEFAULT_DB)))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-auth", action="store_true", help="disable auth for isolated local development only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required; install github-agent-bridge[dashboard]", file=sys.stderr)
        return 2
    uvicorn.run(create_app(DashboardConfig(db=args.db, require_auth=not args.no_auth)), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
