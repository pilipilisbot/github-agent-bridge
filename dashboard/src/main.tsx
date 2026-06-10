import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, ArrowLeft, Brain, CheckCircle2, ChevronDown, Clock3, Cpu, ExternalLink, Filter, Gauge, Link, RefreshCw, RotateCcw, Search, ShieldCheck, TerminalSquare, TimerReset, Trash2, UserCircle2, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import "./styles.css";

type StatusCounts = Record<string, number>;

type MetricsSummary = {
  db_exists: boolean;
  schema_ok?: boolean;
  status_counts: StatusCounts;
  by_repo: Record<string, number>;
  by_action: Record<string, number>;
  by_intent: Record<string, number>;
  by_created_day: Record<string, number>;
  runtime_usage: RuntimeUsage;
  runtime_seconds: Percentiles;
  queue_wait_seconds: Percentiles;
};

type RuntimeUsage = {
  day: RuntimeUsageBucket[];
  month: RuntimeUsageBucket[];
};

type RuntimeUsageBucket = {
  bucket: string;
  seconds: number;
  minutes: number;
  jobs: number;
};

type Percentiles = {
  median: number | null;
  p90: number | null;
  p99: number | null;
};

type About = {
  service: string;
  version: string;
  repository_url: string;
};

type DashboardStatus = {
  service: string;
  read_only: boolean;
  admin_actions: string[];
  autoupdate: AutoupdateState;
  metrics?: {
    knowledge?: {
      proposed?: number;
      approved?: number;
      rejected?: number;
      errors?: number;
    };
  };
};

type AutoupdateState = {
  updated_at?: string;
  installed_version?: string;
  installed_tag?: string;
  target?: {
    tag_name?: string;
    name?: string;
    url?: string;
    body?: string;
    published_at?: string;
    source?: string;
  };
  decision?: string;
  executor_reload_pending?: boolean;
  dashboard_applied_at?: string;
  blocked_reason?: string;
  queue?: {
    active_counts?: Record<string, number>;
    active_total?: number;
  };
  classification?: {
    risk?: string;
    migration_files?: string[];
    risky_files?: string[];
  };
  warnings?: string[];
};

type Job = {
  id: number;
  work_key: string;
  repo: string | null;
  thread: number | null;
  status: string;
  action: string;
  decision: string;
  intent: string;
  subject: string;
  trigger_actor: string | null;
  trigger_actor_avatar_url: string | null;
  attempts: number;
  coalesced_count: number;
  last_error: string | null;
  locked_by: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  queue_wait_seconds: number | null;
  runtime_seconds: number | null;
  github_urls: string[];
  worklog?: WorklogEntry[];
};

type WorklogEntry = {
  id: number;
  ts: string;
  phase: string;
  summary: string;
  detail: string | null;
};

type ProcessSample = {
  pid: number;
  ppid: number;
  state: string;
  cmd: string;
  cpu_ticks: number;
  io_bytes: { read_bytes?: number; write_bytes?: number } | null;
  children?: ProcessSample[];
};

type PersistedProcessSample = {
  id: number;
  ts: string;
  executor_pid: number | null;
  root_pid: number | null;
  running_job_ids: number[];
  cpu_ticks: number;
  io_bytes: number;
  active_since_last_sample: boolean;
  idle_seconds: number | null;
};

type ProgressEntry = {
  id: number;
  ts: string;
  kind: "semantic" | "visible";
  phase: string;
  summary: string;
  detail: string | null;
  age_seconds?: number | null;
};

type ProcessesResponse = {
  running_jobs: Array<{
    id: number;
    work_key: string;
    work_intent: string;
    locked_by: string | null;
    age_seconds: number | null;
    idle_seconds: number | null;
    semantic_progress?: ProgressEntry | null;
    visible_progress?: ProgressEntry | null;
  }>;
  executor: {
    service: string;
    pid: number | null;
    children: ProcessSample[];
  };
  signals: {
    live_process: { state: string; child_count: number };
    process_activity: { state: string; idle_seconds: number | null; sample_ts: string | null };
    semantic_progress: Array<{ id: number; semantic_progress?: ProgressEntry | null }>;
    visible_progress: Array<{ id: number; visible_progress?: ProgressEntry | null }>;
  };
  alerts: string[];
  samples: PersistedProcessSample[];
  detail: string;
};

type SystemdUnit = {
  role: string;
  kind: string;
  unit: string;
  load_state: string;
  active_state: string;
  sub_state: string;
  result: string;
  exec_main_status: string | null;
  main_pid: number | null;
  uptime_seconds: number | null;
  active_enter_timestamp: string;
  inactive_enter_timestamp: string;
  next_elapse: string;
  last_trigger: string;
  unit_file_state: string;
  ok: boolean;
};

type SystemdResponse = {
  available: boolean;
  units: SystemdUnit[];
  errors: string[];
};

type JournalLine = {
  unit: string;
  line: string;
};

type AlertRecord = {
  fingerprint: string;
  source: string;
  severity: string;
  message: string;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  observations: number;
};

type SessionCorrelation = {
  id: string;
  source: string;
  transcript_available: boolean;
  transcript_exposure: string;
  job_id: number;
  work_key: string;
  status: string;
  detail: string;
};

type SessionEvent = {
  id: number;
  ts: string;
  job_id: number;
  work_key: string | null;
  session_id: string;
  event_type: string;
  summary: string;
  detail: string | null;
};

type TranscriptEntry = {
  timestamp: string | null;
  role: string;
  kind: string;
  title: string;
  text: string;
};

type SessionEventGroup = {
  id: string;
  badge: string;
  meta: string | null;
  summary: string;
  detail: string | null;
  eventType: string;
  count: number;
};

type TranscriptEntryGroup = {
  id: string;
  badge: string;
  meta: string | null;
  summary: string;
  text: string;
  kind: string;
  count: number;
};

type UserProfile = {
  login: string;
  avatar_url: string;
  html_url: string;
  is_admin: boolean;
};

type JobActor = {
  login: string;
  avatar_url: string | null;
  job_count: number;
  last_seen: string | null;
};

type JobFilters = {
  status: string;
  repo: string;
  thread: string;
  action: string;
  intent: string;
  actor: string;
};

const emptyJobFilters: JobFilters = { status: "", repo: "", thread: "", action: "", intent: "", actor: "" };

type KnowledgeProposal = {
  id: string;
  event_id: string;
  created_at: string;
  updated_at: string;
  status: string;
  scope: string;
  type: string;
  confidence: number;
  rule: string;
  reason: string;
  model: string;
  error: string | null;
  source_event: KnowledgeEvent | null;
};

type KnowledgeRule = {
  id: string;
  scope: string;
  type: string;
  confidence: number;
  rule: string;
  created_at: string;
  last_seen: string;
  source_events: string[];
  source_event_details: KnowledgeEvent[];
  observations: number;
};

type KnowledgeEvent = {
  id: string;
  occurred_at: string;
  captured_at: string;
  source: string;
  scope: string;
  actor: string;
  trigger_actor: string | null;
  trigger_actor_avatar_url: string | null;
  github_urls: string[];
  source_url: string | null;
  source_job_id: number | null;
  source_table: string | null;
  github_context: Record<string, unknown>;
  comment: string;
  context: Record<string, unknown>;
  classification: string;
  confidence: number;
  memorable: boolean;
};

type KnowledgeResponse = {
  repositories: string[];
  events: KnowledgeEvent[];
  proposals: KnowledgeProposal[];
  rules: KnowledgeRule[];
  summary: Record<string, number>;
};

type KnowledgeTab = "proposals" | "rules" | "events";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
    },
  },
});

const initialJobLimit = 12;
const jobLimitStep = 12;
const staleRelativeDateMs = 7 * 24 * 60 * 60 * 1000;
const liveTickMs = 1000;
const dashboardTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function cn(...values: Array<string | false | null | undefined>) {
  return twMerge(clsx(values));
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function formatSeconds(value: number | null | undefined) {
  if (value === null || value === undefined) return "n/a";
  const seconds = Math.max(0, Math.floor(value));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
});

const compactDateFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function parseDate(value: string | null | undefined) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateTime(value: string | null | undefined) {
  const date = parseDate(value);
  return date ? dateTimeFormatter.format(date) : (value ?? "");
}

function compactDate(value: string | null | undefined) {
  const date = parseDate(value);
  return date ? compactDateFormatter.format(date) : (value ?? "");
}

function formatRelativeTime(value: string | null | undefined, now: number) {
  const date = parseDate(value);
  if (!date) return value ?? "";
  const diffMs = now - date.getTime();
  const absMs = Math.abs(diffMs);
  if (absMs > staleRelativeDateMs) return compactDate(value);
  const suffix = diffMs >= 0 ? "ago" : "from now";
  const seconds = Math.round(absMs / 1000);
  if (seconds < 45) return diffMs >= 0 ? "just now" : "soon";
  if (seconds < 90) return `1m ${suffix}`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ${suffix}`;
  if (minutes < 90) return `1h ${suffix}`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ${suffix}`;
  if (hours < 36) return `1d ${suffix}`;
  return `${Math.round(hours / 24)}d ${suffix}`;
}

function TimeText({
  value,
  compact = false,
  relative = false,
  now = Date.now(),
}: {
  value: string | null | undefined;
  compact?: boolean;
  relative?: boolean;
  now?: number;
}) {
  const date = parseDate(value);
  if (!date) return <>{value ?? ""}</>;
  return (
    <time dateTime={date.toISOString()} title={`UTC: ${date.toISOString()}`}>
      {relative ? formatRelativeTime(value, now) : compact ? compactDate(value) : formatDateTime(value)}
    </time>
  );
}

function elapsedSecondsSince(value: string | null | undefined, now: number) {
  const date = parseDate(value);
  if (!date) return null;
  return Math.max(0, Math.floor((now - date.getTime()) / 1000));
}

function jobRuntimeSeconds(job: Job, now: number) {
  if (job.status === "running") return elapsedSecondsSince(job.started_at, now) ?? job.runtime_seconds;
  return job.runtime_seconds;
}

function queueWaitSeconds(job: Job, now: number) {
  if (job.status === "pending") return elapsedSecondsSince(job.created_at, now) ?? job.queue_wait_seconds;
  return job.queue_wait_seconds;
}

function useNow(enabled: boolean) {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), liveTickMs);
    return () => window.clearInterval(timer);
  }, [enabled]);
  return now;
}

function firstLogLine(value: string | null | undefined) {
  const line = (value ?? "").split(/\r?\n/).map((item) => item.trim()).find(Boolean);
  return line ?? "";
}

function compactLogSummary(label: string, text: string | null | undefined, count = 1) {
  const preview = firstLogLine(text);
  const countLabel = count > 1 ? ` (${count})` : "";
  return preview ? `${label}${countLabel}: ${preview}` : `${label}${countLabel}`;
}

function isCliOutputKind(kind: string) {
  return kind === "openclaw_stdout" || kind === "openclaw_stderr";
}

function joinLogText(items: Array<string | null | undefined>) {
  return items.map((item) => item?.trim()).filter(Boolean).join("\n");
}

function groupSessionEvents(events: SessionEvent[]) {
  const groups: SessionEventGroup[] = [];
  for (const event of events) {
    const previous = groups[groups.length - 1];
    if (previous && isCliOutputKind(event.event_type) && previous.eventType === event.event_type) {
      previous.count += 1;
      previous.meta = event.ts;
      previous.detail = joinLogText([previous.detail, event.detail]);
      previous.summary = compactLogSummary(event.summary, previous.detail, previous.count);
      continue;
    }
    groups.push({
      id: String(event.id),
      badge: event.event_type,
      meta: event.ts,
      summary: isCliOutputKind(event.event_type) ? compactLogSummary(event.summary, event.detail) : event.summary,
      detail: event.detail,
      eventType: event.event_type,
      count: 1,
    });
  }
  return groups;
}

function groupTranscriptEntries(entries: TranscriptEntry[]) {
  const groups: TranscriptEntryGroup[] = [];
  entries.forEach((entry, index) => {
    const previous = groups[groups.length - 1];
    if (previous && isCliOutputKind(entry.kind) && previous.kind === entry.kind) {
      previous.count += 1;
      previous.meta = entry.timestamp;
      previous.text = joinLogText([previous.text, entry.text]);
      previous.summary = compactLogSummary(`${entry.role} · ${entry.kind}`, previous.text, previous.count);
      return;
    }
    groups.push({
      id: `${entry.timestamp ?? "entry"}-${index}`,
      badge: entry.title,
      meta: entry.timestamp,
      summary: isCliOutputKind(entry.kind) ? compactLogSummary(`${entry.role} · ${entry.kind}`, entry.text) : `${entry.role} · ${entry.kind}`,
      text: entry.text,
      kind: entry.kind,
      count: 1,
    });
  });
  return groups;
}

function defaultLogOpen(kind: string, running: boolean, index: number, total: number) {
  if (kind === "openclaw_stdout") return false;
  return running || index >= total - 2;
}

function statusTone(status: string) {
  return {
    pending: { badge: "border-amber-300 bg-amber-50 text-amber-800", dot: "bg-amber-500" },
    running: { badge: "border-blue-300 bg-blue-50 text-blue-700", dot: "bg-blue-600" },
    blocked: { badge: "border-red-300 bg-red-50 text-red-700", dot: "bg-red-600" },
    denied: { badge: "border-red-300 bg-red-50 text-red-700", dot: "bg-red-600" },
    done: { badge: "border-emerald-300 bg-emerald-50 text-emerald-700", dot: "bg-emerald-600" },
    waiting_approval: { badge: "border-slate-300 bg-slate-50 text-slate-700", dot: "bg-slate-500" },
  }[status] ?? { badge: "border-slate-300 bg-slate-50 text-slate-700", dot: "bg-slate-500" };
}

function buildJobQuery(filters: JobFilters, limit: number) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value.trim()) params.set(key, value.trim());
  }
  params.set("limit", String(limit));
  return `/api/jobs?${params.toString()}`;
}

function buildKnowledgeQuery(repo: string, status: string, limit = 50) {
  const params = new URLSearchParams();
  if (repo.trim()) params.set("repo", repo.trim());
  if (status.trim()) params.set("status", status.trim());
  params.set("limit", String(limit));
  return `/api/knowledge?${params.toString()}`;
}

function metricsSummaryPath(timezone = dashboardTimeZone) {
  return `/api/metrics/summary?timezone=${encodeURIComponent(timezone)}`;
}

function safeExternalUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch {
    return "#";
  }
}

function jobPath(jobId: number) {
  return `/jobs/${jobId}`;
}

function parseSseData<T>(message: MessageEvent): T | null {
  try {
    return JSON.parse(message.data) as T;
  } catch {
    return null;
  }
}

function appendUniqueById<T extends { id: number }>(items: T[], item: T) {
  if (items.some((existing) => existing.id === item.id)) return items;
  return [...items, item];
}

function appendTranscriptEntry(items: TranscriptEntry[], item: TranscriptEntry) {
  const key = transcriptKey(item);
  if (items.some((existing) => transcriptKey(existing) === key)) return items;
  return [...items, item];
}

function transcriptKey(item: TranscriptEntry) {
  return `${item.timestamp ?? ""}:${item.role}:${item.kind}:${item.title}:${item.text}`;
}

function shouldRefreshJobForSessionEvent(eventType: string) {
  return ["claimed", "dispatch_started", "dispatch_finished", "done", "blocked", "denied", "waiting_approval"].includes(eventType);
}

function isRetryableStatus(status: string) {
  return ["blocked", "denied", "waiting_approval"].includes(status);
}

function selectedJobIdFromPath(pathname = window.location.pathname) {
  const match = pathname.match(/^\/jobs\/(\d+)\/?$/);
  return match ? Number(match[1]) : null;
}

function isKnowledgePath(pathname = window.location.pathname) {
  return /^\/knowledge\/?$/.test(pathname);
}

function isSystemPath(pathname = window.location.pathname) {
  return /^\/system\/?$/.test(pathname);
}

function repoFromScope(scope: string) {
  return scope.startsWith("repo:") ? scope.slice("repo:".length) : scope;
}

function hasActiveJobFilters(filters: JobFilters) {
  return Object.values(filters).some((value) => value.trim() !== "");
}

function App() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = React.useState<JobFilters>(emptyJobFilters);
  const [jobLimit, setJobLimit] = React.useState(initialJobLimit);
  const jobLoadPendingRef = React.useRef(false);
  const [knowledgeRepo, setKnowledgeRepo] = React.useState("");
  const [knowledgeStatus, setKnowledgeStatus] = React.useState("proposed");
  const [autoupdateAction, setAutoupdateAction] = React.useState<"refresh" | "apply" | "complete" | null>(null);
  const [autoupdateError, setAutoupdateError] = React.useState("");
  const [pathname, setPathname] = React.useState(() => window.location.pathname);
  const jobRouteId = selectedJobIdFromPath(pathname);
  const isJobDetailRoute = jobRouteId !== null;
  const isKnowledgeRoute = isKnowledgePath(pathname);
  const isSystemRoute = isSystemPath(pathname);
  const isDashboardRoute = !isJobDetailRoute && !isKnowledgeRoute && !isSystemRoute;
  const selectedJobId = jobRouteId;
  const metrics = useQuery({ queryKey: ["metrics", dashboardTimeZone], queryFn: () => api<{ metrics: MetricsSummary }>(metricsSummaryPath()), enabled: isDashboardRoute || isSystemRoute });
  const dashboardStatus = useQuery({ queryKey: ["dashboard-status"], queryFn: () => api<DashboardStatus>("/api/status") });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<{ user: UserProfile }>("/api/me"), refetchInterval: false });
  const about = useQuery({ queryKey: ["about"], queryFn: () => api<About>("/api/about") });
  const actorOptions = useQuery({ queryKey: ["job-actors"], queryFn: () => api<{ actors: JobActor[] }>("/api/jobs/actors"), enabled: isDashboardRoute });
  const jobs = useQuery({
    queryKey: ["jobs", filters, jobLimit],
    queryFn: () => api<{ jobs: Job[] }>(buildJobQuery(filters, jobLimit)),
    enabled: isDashboardRoute,
    placeholderData: (previousData) => previousData,
  });
  const processes = useQuery({ queryKey: ["processes"], queryFn: () => api<ProcessesResponse>("/api/processes"), enabled: isSystemRoute });
  const systemd = useQuery({ queryKey: ["systemd"], queryFn: () => api<SystemdResponse>("/api/systemd"), enabled: isSystemRoute });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api<{ alerts: AlertRecord[] }>("/api/alerts"), enabled: isSystemRoute });
  const knowledge = useQuery({
    queryKey: ["knowledge", knowledgeRepo, knowledgeStatus],
    queryFn: () => api<KnowledgeResponse>(buildKnowledgeQuery(knowledgeRepo, knowledgeStatus)),
    enabled: isKnowledgeRoute,
  });
  const detail = useQuery({
    queryKey: ["job", selectedJobId],
    queryFn: () => api<{ job: Job }>(`/api/jobs/${selectedJobId}`),
    enabled: selectedJobId !== null,
  });
  const session = useQuery({
    queryKey: ["job-session", selectedJobId],
    queryFn: () => api<{ session: SessionCorrelation }>(`/api/jobs/${selectedJobId}/session`),
    enabled: selectedJobId !== null,
  });
  const sessionEvents = useQuery({
    queryKey: ["job-session-events", selectedJobId],
    queryFn: () => api<{ events: SessionEvent[] }>(`/api/jobs/${selectedJobId}/session/events`),
    enabled: selectedJobId !== null,
  });
  const transcript = useQuery({
    queryKey: ["job-session-transcript", selectedJobId],
    queryFn: () => api<{ entries: TranscriptEntry[] }>(`/api/jobs/${selectedJobId}/session/transcript`),
    enabled: selectedJobId !== null,
  });
  const retryJob = React.useCallback(async (jobId: number) => {
    const payload = await api<{ job: Job }>(`/api/jobs/${jobId}/retry`, { method: "POST" });
    queryClient.setQueryData<{ job: Job }>(["job", jobId], { job: payload.job });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
    queryClient.invalidateQueries({ queryKey: ["metrics"] });
  }, [queryClient]);
  const dismissJob = React.useCallback(async (jobId: number) => {
    const payload = await api<{ job: Job }>(`/api/jobs/${jobId}/dismiss`, { method: "POST" });
    queryClient.setQueryData<{ job: Job }>(["job", jobId], { job: payload.job });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
    queryClient.invalidateQueries({ queryKey: ["metrics"] });
  }, [queryClient]);
  const moderateKnowledgeProposal = React.useCallback(async (proposalId: string, action: "approve" | "reject") => {
    await api<{ proposal: KnowledgeProposal }>(`/api/knowledge/proposals/${encodeURIComponent(proposalId)}/${action}`, { method: "POST" });
    queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-status"] });
  }, [queryClient]);
  const deleteKnowledgeRule = React.useCallback(async (ruleId: string) => {
    await api<{ detail: string }>(`/api/knowledge/rules/${encodeURIComponent(ruleId)}`, { method: "DELETE" });
    queryClient.invalidateQueries({ queryKey: ["knowledge"] });
  }, [queryClient]);
  const runAutoupdateAction = React.useCallback(async (action: "refresh" | "apply" | "complete") => {
    const path = {
      refresh: "/api/autoupdate/refresh",
      apply: "/api/autoupdate/apply",
      complete: "/api/autoupdate/complete-pending",
    }[action];
    setAutoupdateAction(action);
    setAutoupdateError("");
    try {
      await api(path, { method: "POST" });
      queryClient.invalidateQueries({ queryKey: ["dashboard-status"] });
    } catch (error) {
      queryClient.invalidateQueries({ queryKey: ["dashboard-status"] });
      setAutoupdateError(error instanceof Error ? error.message : String(error));
    } finally {
      setAutoupdateAction(null);
    }
  }, [queryClient]);

  React.useEffect(() => {
    if (selectedJobId === null) return;
    const source = new EventSource(`/api/jobs/${selectedJobId}/session/stream`);
    source.addEventListener("session_event", (message) => {
      const event = parseSseData<SessionEvent>(message);
      if (!event) return;
      queryClient.setQueryData<{ events: SessionEvent[] }>(["job-session-events", selectedJobId], (current) => ({
        events: appendUniqueById(current?.events ?? [], event),
      }));
      if (shouldRefreshJobForSessionEvent(event.event_type)) {
        queryClient.invalidateQueries({ queryKey: ["job", selectedJobId] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
      }
    });
    source.addEventListener("transcript_entry", (message) => {
      const payload = parseSseData<{ job_id: number; entry: TranscriptEntry }>(message);
      if (!payload || payload.job_id !== selectedJobId) return;
      queryClient.setQueryData<{ entries: TranscriptEntry[] }>(["job-session-transcript", selectedJobId], (current) => ({
        entries: appendTranscriptEntry(current?.entries ?? [], payload.entry),
      }));
    });
    source.onerror = () => {
      queryClient.invalidateQueries({ queryKey: ["job", selectedJobId] });
      queryClient.invalidateQueries({ queryKey: ["job-session-events", selectedJobId] });
      queryClient.invalidateQueries({ queryKey: ["job-session-transcript", selectedJobId] });
    };
    return () => source.close();
  }, [selectedJobId, queryClient]);

  React.useEffect(() => {
    const syncFromPath = () => {
      setPathname(window.location.pathname);
    };
    window.addEventListener("popstate", syncFromPath);
    return () => window.removeEventListener("popstate", syncFromPath);
  }, []);

  const viewJob = React.useCallback((jobId: number) => {
    window.history.pushState({}, "", jobPath(jobId));
    setPathname(window.location.pathname);
  }, []);

  const counts = metrics.data?.metrics.status_counts ?? {};
  const jobRows = jobs.data?.jobs ?? [];
  const applyFilters = React.useCallback((nextFilters: JobFilters) => {
    setFilters(nextFilters);
    setJobLimit(initialJobLimit);
    jobLoadPendingRef.current = false;
  }, []);
  React.useEffect(() => {
    if (!jobs.isFetching) jobLoadPendingRef.current = false;
  }, [jobs.isFetching]);
  const loadMoreJobs = React.useCallback(() => {
    if (jobLoadPendingRef.current) return;
    jobLoadPendingRef.current = true;
    setJobLimit((current) => current + jobLimitStep);
  }, []);
  const selectedJob = selectedJobId ? (detail.data?.job ?? null) : null;
  const hasLiveJob = jobRows.some((job) => job.status === "running" || job.status === "pending") || selectedJob?.status === "running" || selectedJob?.status === "pending" || Boolean(processes.data?.running_jobs.length);
  const now = useNow(hasLiveJob);
  const detailStatus = <JobDetailStatus selectedJobId={selectedJobId} selectedJob={selectedJob} loading={detail.isLoading} error={detail.error} session={session.data?.session} sessionEvents={sessionEvents.data?.events} transcript={transcript.data?.entries} now={now} />;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-slate-800 bg-slate-950 text-white">
        <div className="mx-auto flex w-full max-w-[1440px] items-center justify-between gap-3 px-4 py-4 md:px-6">
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold">GitHub Agent Bridge</h1>
            <ProductMeta about={about.data} />
          </div>
          <UserMenu user={me.data?.user} loading={me.isLoading} />
        </div>
      </header>

      <main className="mx-auto grid w-full max-w-[1440px] gap-4 px-3 py-4 sm:px-4 md:px-6 md:py-5">
        <SectionNav isDashboardRoute={isDashboardRoute} isSystemRoute={isSystemRoute} isKnowledgeRoute={isKnowledgeRoute} knowledgeBadgeCount={dashboardStatus.data?.metrics?.knowledge?.proposed ?? 0} />
        {jobRouteId !== null ? (
          <JobDetailPage
            jobId={jobRouteId}
            detail={detailStatus}
            selectedJob={selectedJob}
            user={me.data?.user}
            onRetry={retryJob}
            onDismiss={dismissJob}
            onRefresh={() => {
              detail.refetch();
              session.refetch();
              sessionEvents.refetch();
              transcript.refetch();
            }}
          />
        ) : isKnowledgeRoute ? (
          <KnowledgePage
            data={knowledge.data}
            loading={knowledge.isLoading}
            error={knowledge.error}
            repo={knowledgeRepo}
            status={knowledgeStatus}
            user={me.data?.user}
            now={now}
            onRepoChange={setKnowledgeRepo}
            onStatusChange={setKnowledgeStatus}
            onApprove={(proposalId) => moderateKnowledgeProposal(proposalId, "approve")}
            onReject={(proposalId) => moderateKnowledgeProposal(proposalId, "reject")}
            onDeleteRule={deleteKnowledgeRule}
            onRefresh={() => knowledge.refetch()}
          />
        ) : isSystemRoute ? (
          <SystemPage
            processes={processes.data}
            processesLoading={processes.isLoading}
            processesError={processes.error}
            systemd={systemd.data}
            systemdLoading={systemd.isLoading}
            systemdError={systemd.error}
            alerts={alerts.data?.alerts}
            alertsLoading={alerts.isLoading}
            alertsError={alerts.error}
            now={now}
            onRefreshProcesses={() => processes.refetch()}
            onRefreshSystemd={() => systemd.refetch()}
            onRefreshAlerts={() => alerts.refetch()}
          />
        ) : (
          <>
            {metrics.error ? <Banner tone="error" text={metrics.error.message} /> : null}
            {dashboardStatus.error ? <Banner tone="error" text={dashboardStatus.error.message} /> : null}
            <AutoupdateNotice
              state={dashboardStatus.data?.autoupdate}
              isAdmin={Boolean(me.data?.user?.is_admin)}
              runningAction={autoupdateAction}
              actionError={autoupdateError}
              onRefresh={() => runAutoupdateAction("refresh")}
              onApply={() => runAutoupdateAction("apply")}
              onCompletePending={() => runAutoupdateAction("complete")}
            />
            <section className="grid grid-cols-2 gap-3 xl:grid-cols-4" aria-label="Summary metrics">
              <Metric title="Pending" value={counts.pending ?? 0} icon={<Clock3 className="h-5 w-5" />} />
              <Metric title="Running" value={counts.running ?? 0} icon={<Activity className="h-5 w-5" />} />
              <Metric title="Blocked" value={counts.blocked ?? 0} icon={<AlertTriangle className="h-5 w-5" />} />
              <Metric title="Done" value={counts.done ?? 0} icon={<CheckCircle2 className="h-5 w-5" />} />
            </section>

            <section className="grid gap-3">
              <JobsHeader count={jobRows.length} limit={jobLimit} loading={jobs.isLoading} onRefresh={() => jobs.refetch()} />
              <Panel title="Recent jobs" flushHeader>
                <Filters filters={filters} actorOptions={actorOptions.data?.actors ?? []} onChange={applyFilters} />
                {jobs.error ? <Banner tone="error" text={jobs.error.message} /> : null}
                <JobsList
                  jobs={jobRows}
                  loading={jobs.isLoading}
                  loadingMore={jobs.isFetching && !jobs.isLoading}
                  hasMore={jobRows.length >= jobLimit}
                  onLoadMore={loadMoreJobs}
                  onViewJob={viewJob}
                  now={now}
                  user={me.data?.user}
                  onRetry={retryJob}
                  onDismiss={dismissJob}
                />
              </Panel>
              <Panel title="Runtime usage" action={<RefreshButton onClick={() => metrics.refetch()} />}>
                <RuntimeUsageChart usage={metrics.data?.metrics.runtime_usage} loading={metrics.isLoading} totalJobs={totalJobs(counts)} />
              </Panel>
            </section>

            <section className="grid gap-4 xl:grid-cols-3">
              <Panel title="Runtime percentiles">
                <PercentileChart label="runtime" values={metrics.data?.metrics.runtime_seconds} />
              </Panel>
              <Panel title="Jobs per day">
                <JobsPerDayChart values={metrics.data?.metrics.by_created_day} loading={metrics.isLoading} totalJobs={totalJobs(counts)} />
              </Panel>
              <Panel title="Queue wait percentiles">
                <PercentileChart label="queue wait" values={metrics.data?.metrics.queue_wait_seconds} />
              </Panel>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function ProductMeta({ about }: { about: About | undefined }) {
  const version = about?.version ? `v${about.version}` : "version loading";
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-300">
      <span>Operational dashboard</span>
      <span className="font-mono text-xs text-slate-400">{version}</span>
      {about?.repository_url ? (
        <a className="inline-flex items-center gap-1 text-xs font-semibold text-slate-200 hover:underline" href={safeExternalUrl(about.repository_url)} rel="noreferrer" target="_blank">
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          GitHub
        </a>
      ) : null}
    </p>
  );
}

function AutoupdateNotice({
  state,
  isAdmin,
  runningAction = null,
  actionError = "",
  onRefresh,
  onApply,
  onCompletePending,
}: {
  state: AutoupdateState | undefined;
  isAdmin: boolean;
  runningAction?: "refresh" | "apply" | "complete" | null;
  actionError?: string;
  onRefresh?: () => Promise<void> | void;
  onApply?: () => Promise<void> | void;
  onCompletePending?: () => Promise<void> | void;
}) {
  if (!state) return null;
  const targetTag = state?.target?.tag_name?.trim();
  if (!isAdmin || !targetTag || state?.decision === "noop") return null;
  const decision = autoupdateDecisionLabel(state.decision);
  const activeTotal = state.queue?.active_total ?? 0;
  const risk = autoupdateRiskLabel(state.classification?.risk);
  const changelog = changelogMarkdown(state.target?.body);
  const migrationCount = state.classification?.migration_files?.length ?? 0;
  const riskyCount = state.classification?.risky_files?.length ?? 0;
  const canComplete = Boolean(state.executor_reload_pending && state.dashboard_applied_at && onCompletePending);
  const canApply = Boolean(onApply && migrationCount === 0);

  return (
    <section className="rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-950 shadow-sm" aria-label="Update available">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-700" aria-hidden />
            <h2 className="text-sm font-semibold">Update available</h2>
            <span className="rounded-sm border border-amber-300 bg-white px-1.5 py-0.5 font-mono text-[11px] text-amber-800">{targetTag}</span>
            {state.target?.url ? (
              <a className="inline-flex items-center gap-1 text-xs font-semibold text-amber-800 hover:underline" href={safeExternalUrl(state.target.url)} rel="noreferrer" target="_blank">
                <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                Release
              </a>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-amber-900">
            {decision}
            {state.installed_tag ? <span className="font-mono"> from {state.installed_tag}</span> : null}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {onRefresh ? (
              <button
                className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-amber-300 bg-white px-2.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                type="button"
                disabled={runningAction !== null}
                onClick={onRefresh}
              >
                <RefreshCw className={cn("h-3.5 w-3.5", runningAction === "refresh" && "animate-spin")} aria-hidden />
                {runningAction === "refresh" ? "Checking..." : "Check now"}
              </button>
            ) : null}
            {canApply ? (
              <button
                className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md bg-amber-800 px-2.5 text-xs font-semibold text-white hover:bg-amber-900 disabled:cursor-not-allowed disabled:opacity-60"
                type="button"
                disabled={runningAction !== null}
                onClick={() => {
                  if (!window.confirm(`Apply ${targetTag}? This can restart bridge services according to the recorded safe plan.`)) return;
                  onApply?.();
                }}
              >
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                {runningAction === "apply" ? "Applying..." : "Apply update"}
              </button>
            ) : null}
            {canComplete ? (
              <button
                className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-amber-300 bg-white px-2.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                type="button"
                disabled={runningAction !== null}
                onClick={onCompletePending}
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                {runningAction === "complete" ? "Completing..." : "Complete reload"}
              </button>
            ) : null}
          </div>
        </div>
        <div className="grid gap-2 text-xs sm:grid-cols-3 lg:min-w-[420px]">
          <AutoupdateStat label="Impact" value={risk} />
          <AutoupdateStat label="Active jobs" value={String(activeTotal)} />
          <AutoupdateStat label="Admin only" value="autoupdate" />
        </div>
      </div>
      {state.blocked_reason || state.executor_reload_pending || migrationCount > 0 || riskyCount > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {state.executor_reload_pending ? <span className="rounded-sm border border-amber-300 bg-white px-2 py-1 font-semibold">executor reload pending</span> : null}
          {state.blocked_reason ? <span className="rounded-sm border border-amber-300 bg-white px-2 py-1 font-mono">{state.blocked_reason}</span> : null}
          {migrationCount > 0 ? <span className="rounded-sm border border-amber-300 bg-white px-2 py-1">{migrationCount} migration file{migrationCount === 1 ? "" : "s"}</span> : null}
          {riskyCount > 0 ? <span className="rounded-sm border border-amber-300 bg-white px-2 py-1">{riskyCount} executor/shared file{riskyCount === 1 ? "" : "s"}</span> : null}
        </div>
      ) : null}
      {changelog ? (
        <div className="mt-3 rounded-md border border-amber-200 bg-white/70 p-2.5">
          <div className="text-[11px] font-semibold uppercase text-amber-800">Changelog preview</div>
          <ReleaseChangelogMarkdown markdown={changelog} />
        </div>
      ) : null}
      {state.warnings?.length ? <div className="mt-2 font-mono text-xs text-amber-800">{state.warnings[0]}</div> : null}
      {actionError ? <div className="mt-2 rounded-sm border border-amber-300 bg-white px-2 py-1 font-mono text-xs text-amber-900">Action failed: {actionError}</div> : null}
    </section>
  );
}

function AutoupdateStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-amber-200 bg-white/80 px-2 py-1.5">
      <div className="text-[11px] font-semibold uppercase text-amber-700">{label}</div>
      <div className="mt-0.5 truncate font-mono text-xs text-amber-950">{value}</div>
    </div>
  );
}

function autoupdateDecisionLabel(decision: string | undefined) {
  return ({
    stage_dashboard_reload: "Dashboard reload can be staged now",
    stage_defer_executor_reload: "Dashboard reload can be staged; executor reload waits for the queue",
    stage_full_reload: "Full reload can be staged now",
    defer_migration: "Migration release is waiting for a quiet queue",
  }[decision ?? ""] ?? "Update plan recorded");
}

function autoupdateRiskLabel(risk: string | undefined) {
  return ({
    dashboard_only: "dashboard only",
    executor_or_queue: "executor or queue",
    executor_or_shared: "executor or shared",
    migration_required: "migration",
    none: "none",
  }[risk ?? ""] ?? "unknown");
}

function changelogMarkdown(body: string | undefined) {
  return (body ?? "").trim();
}

function ReleaseChangelogMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="mt-1 text-sm leading-relaxed text-amber-950">
      <ReactMarkdown
        allowedElements={["a", "blockquote", "code", "em", "h1", "h2", "h3", "h4", "li", "ol", "p", "strong", "ul"]}
        components={{
          a: ({ href, children }) => (
            <a className="font-semibold text-amber-800 underline underline-offset-2" href={safeExternalUrl(href ?? "")} rel="noreferrer" target="_blank">
              {children}
            </a>
          ),
          blockquote: ({ children }) => <blockquote className="mt-2 border-l-2 border-amber-300 pl-2 text-amber-900">{children}</blockquote>,
          code: ({ children }) => <code className="rounded-sm bg-amber-100 px-1 py-0.5 font-mono text-[0.85em] text-amber-950">{children}</code>,
          h1: ({ children }) => <h3 className="mt-2 text-sm font-semibold text-amber-950 first:mt-0">{children}</h3>,
          h2: ({ children }) => <h3 className="mt-2 text-sm font-semibold text-amber-950 first:mt-0">{children}</h3>,
          h3: ({ children }) => <h3 className="mt-2 text-sm font-semibold text-amber-950 first:mt-0">{children}</h3>,
          h4: ({ children }) => <h4 className="mt-2 text-xs font-semibold uppercase text-amber-800 first:mt-0">{children}</h4>,
          li: ({ children }) => <li className="break-words pl-0.5 [overflow-wrap:anywhere]">{children}</li>,
          ol: ({ children }) => <ol className="mt-1 list-decimal space-y-1 pl-5 first:mt-0">{children}</ol>,
          p: ({ children }) => <p className="mt-1 break-words [overflow-wrap:anywhere] first:mt-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          ul: ({ children }) => <ul className="mt-1 list-disc space-y-1 pl-5 first:mt-0">{children}</ul>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

function SectionNav({
  isDashboardRoute,
  isSystemRoute = false,
  isKnowledgeRoute,
  knowledgeBadgeCount = 0,
}: {
  isDashboardRoute: boolean;
  isSystemRoute?: boolean;
  isKnowledgeRoute: boolean;
  knowledgeBadgeCount?: number;
}) {
  return (
    <nav className="flex min-w-0 rounded-lg border border-border bg-panel p-1 shadow-sm" aria-label="Dashboard sections">
      <SectionLink href="/" active={isDashboardRoute}>
        <TerminalSquare className="h-4 w-4" aria-hidden />
        <span>Jobs</span>
      </SectionLink>
      <SectionLink href="/system" active={isSystemRoute}>
        <Gauge className="h-4 w-4" aria-hidden />
        <span>System</span>
      </SectionLink>
      <SectionLink href="/knowledge" active={isKnowledgeRoute}>
        <Brain className="h-4 w-4" aria-hidden />
        <span>Knowledge</span>
        {knowledgeBadgeCount > 0 ? (
          <span
            className={cn(
              "inline-flex h-5 min-w-5 items-center justify-center rounded-full border px-1 font-mono text-[11px] leading-none",
              isKnowledgeRoute ? "border-white/40 bg-white/15 text-white" : "border-amber-200 bg-amber-100 text-amber-800",
            )}
            aria-label={`${knowledgeBadgeCount} proposed knowledge ${knowledgeBadgeCount === 1 ? "item" : "items"}`}
          >
            {knowledgeBadgeCount}
          </span>
        ) : null}
      </SectionLink>
    </nav>
  );
}

function SystemPage({
  processes,
  processesLoading,
  processesError,
  systemd,
  systemdLoading,
  systemdError,
  alerts,
  alertsLoading,
  alertsError,
  now,
  onRefreshProcesses,
  onRefreshSystemd,
  onRefreshAlerts,
}: {
  processes: ProcessesResponse | undefined;
  processesLoading: boolean;
  processesError: Error | null;
  systemd: SystemdResponse | undefined;
  systemdLoading: boolean;
  systemdError: Error | null;
  alerts: AlertRecord[] | undefined;
  alertsLoading: boolean;
  alertsError: Error | null;
  now: number;
  onRefreshProcesses: () => void;
  onRefreshSystemd: () => void;
  onRefreshAlerts: () => void;
}) {
  return (
    <section className="grid gap-4" aria-label="Bridge system">
      <Panel title="Systemd" action={<RefreshButton onClick={onRefreshSystemd} />}>
        {systemdError ? <Banner tone="error" text={systemdError.message} /> : null}
        <SystemdUnits data={systemd} loading={systemdLoading} />
      </Panel>
      <Panel title="Process activity" action={<RefreshButton onClick={onRefreshProcesses} />}>
        {processesError ? <Banner tone="error" text={processesError.message} /> : null}
        <ProcessActivity data={processes} loading={processesLoading} />
      </Panel>
      <Panel title="Monitor alerts" action={<RefreshButton onClick={onRefreshAlerts} />}>
        {alertsError ? <Banner tone="error" text={alertsError.message} /> : null}
        <AlertsPanel alerts={alerts} loading={alertsLoading} now={now} />
      </Panel>
    </section>
  );
}

function SectionLink({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <a className={cn("inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md px-3 text-sm font-semibold sm:flex-none", active ? "bg-primary text-white shadow-sm" : "text-muted hover:bg-slate-50 hover:text-foreground")} href={href}>
      {children}
    </a>
  );
}

function JobDetailPage({
  jobId,
  detail,
  selectedJob,
  user,
  onRetry,
  onDismiss,
  onRefresh,
}: {
  jobId: number;
  detail: React.ReactNode;
  selectedJob: Job | null;
  user: UserProfile | undefined;
  onRetry: (jobId: number) => Promise<void>;
  onDismiss: (jobId: number) => Promise<void>;
  onRefresh: () => void;
}) {
  const [retrying, setRetrying] = React.useState(false);
  const [dismissing, setDismissing] = React.useState(false);
  const canRetry = Boolean(user?.is_admin && selectedJob && isRetryableStatus(selectedJob.status));
  const retryLabel = retrying ? "Retrying..." : "Retry";
  const dismissLabel = dismissing ? "Dismissing..." : "Dismiss";
  return (
    <div className="grid min-w-0 gap-3 sm:gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <a className="inline-flex h-9 items-center gap-2 rounded-md border border-border px-3 text-sm font-semibold text-foreground hover:bg-slate-50" href="/">
          <ArrowLeft className="h-4 w-4" aria-hidden />
          Dashboard
        </a>
        <div className="flex items-center gap-2">
          {canRetry ? (
            <button
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
              type="button"
              disabled={retrying}
              onClick={async () => {
                if (!window.confirm(`Retry job #${jobId}?`)) return;
                setRetrying(true);
                try {
                  await onRetry(jobId);
                } finally {
                  setRetrying(false);
                }
              }}
            >
              <RotateCcw className="h-4 w-4" aria-hidden />
              {retryLabel}
            </button>
          ) : null}
          {canRetry ? (
            <button
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border bg-white px-3 text-sm font-semibold text-foreground hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
              type="button"
              disabled={dismissing}
              onClick={async () => {
                if (!window.confirm(`Dismiss job #${jobId}?`)) return;
                setDismissing(true);
                try {
                  await onDismiss(jobId);
                } finally {
                  setDismissing(false);
                }
              }}
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              {dismissLabel}
            </button>
          ) : null}
          <RefreshButton onClick={onRefresh} />
        </div>
      </div>
      <Panel title={`Job #${jobId}`} className="p-3 sm:p-4">
        {detail}
      </Panel>
    </div>
  );
}

function JobDetailStatus({
  selectedJobId,
  selectedJob,
  loading,
  error,
  session,
  sessionEvents,
  transcript,
  now,
}: {
  selectedJobId: number | null;
  selectedJob: Job | null;
  loading: boolean;
  error: Error | null;
  session: SessionCorrelation | undefined;
  sessionEvents: SessionEvent[] | undefined;
  transcript: TranscriptEntry[] | undefined;
  now: number;
}) {
  if (selectedJob) return <JobDetail job={selectedJob} session={session} sessionEvents={sessionEvents} transcript={transcript} now={now} />;
  if (selectedJobId !== null && loading) return <EmptyState text="Loading selected job..." />;
  if (selectedJobId !== null && error) return <Banner tone="error" text={`Job #${selectedJobId}: ${error.message}`} />;
  return <EmptyState text="Select a job to inspect its timeline, worklog and GitHub links." />;
}

function KnowledgePage({
  data,
  loading,
  error,
  repo,
  status,
  user,
  now,
  onRepoChange,
  onStatusChange,
  onApprove,
  onReject,
  onDeleteRule,
  onRefresh,
}: {
  data: KnowledgeResponse | undefined;
  loading: boolean;
  error: Error | null;
  repo: string;
  status: string;
  user: UserProfile | undefined;
  now: number;
  onRepoChange: (repo: string) => void;
  onStatusChange: (status: string) => void;
  onApprove: (proposalId: string) => Promise<void>;
  onReject: (proposalId: string) => Promise<void>;
  onDeleteRule: (ruleId: string) => Promise<void>;
  onRefresh: () => void;
}) {
  const summary = data?.summary ?? {};
  const [activeTab, setActiveTab] = React.useState<KnowledgeTab>("proposals");
  const tabs: Array<{ id: KnowledgeTab; label: string; count: number }> = [
    { id: "proposals", label: "Proposals", count: data?.proposals.length ?? 0 },
    { id: "rules", label: "Rules", count: data?.rules.length ?? 0 },
    { id: "events", label: "Events", count: data?.events.length ?? 0 },
  ];
  return (
    <div className="grid min-w-0 gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Brain className="h-5 w-5 text-muted" aria-hidden />
            Acquired knowledge
          </h2>
          <p className="text-xs text-muted">Captured feedback, proposed rules and curated agent memory.</p>
        </div>
        <RefreshButton onClick={onRefresh} />
      </div>

      {error ? <Banner tone="error" text={error.message} /> : null}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Knowledge metrics">
        <Metric title="Proposed" value={summary.proposed ?? 0} icon={<Clock3 className="h-5 w-5" />} />
        <Metric title="Approved" value={summary.approved ?? 0} icon={<CheckCircle2 className="h-5 w-5" />} />
        <Metric title="Rules" value={summary.rules ?? 0} icon={<ShieldCheck className="h-5 w-5" />} />
        <Metric title="Events" value={summary.events ?? 0} icon={<Activity className="h-5 w-5" />} />
      </section>

      <Panel title="Filters" className="p-3">
        <div className={cn("grid gap-3", activeTab === "proposals" ? "md:grid-cols-[minmax(0,1fr)_220px]" : "md:grid-cols-[minmax(0,1fr)]")}>
          <Field label="Repository">
            <select className="control" value={repo} onChange={(event) => onRepoChange(event.target.value)}>
              <option value="">All repositories</option>
              {(data?.repositories ?? []).map((item) => (
                <option value={item} key={item}>{item}</option>
              ))}
            </select>
          </Field>
          {activeTab === "proposals" ? (
            <Field label="Proposal status">
              <select className="control" value={status} onChange={(event) => onStatusChange(event.target.value)}>
                <option value="">All statuses</option>
                <option value="proposed">proposed</option>
                <option value="approved">approved</option>
                <option value="rejected">rejected</option>
                <option value="error">error</option>
              </select>
            </Field>
          ) : null}
        </div>
      </Panel>

      <Panel
        title="Knowledge records"
        action={
          <div className="flex max-w-full flex-wrap rounded-md border border-border bg-white p-0.5" role="tablist" aria-label="Knowledge record type">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                className={cn("inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-semibold", activeTab === tab.id ? "bg-primary text-white" : "text-muted hover:bg-slate-50 hover:text-foreground")}
                type="button"
                role="tab"
                aria-label={`${tab.label} (${tab.count})`}
                aria-selected={activeTab === tab.id}
                onClick={() => setActiveTab(tab.id)}
              >
                <span>{tab.label}</span>
                <span className={cn("rounded-sm border px-1 font-mono text-[10px]", activeTab === tab.id ? "border-white/40 text-white" : "border-border text-muted")}>{tab.count}</span>
              </button>
            ))}
          </div>
        }
      >
        {activeTab === "proposals" ? <KnowledgeProposals proposals={data?.proposals ?? []} loading={loading} isAdmin={Boolean(user?.is_admin)} now={now} onApprove={onApprove} onReject={onReject} /> : null}
        {activeTab === "rules" ? <KnowledgeRules rules={data?.rules ?? []} loading={loading} isAdmin={Boolean(user?.is_admin)} now={now} onDeleteRule={onDeleteRule} /> : null}
        {activeTab === "events" ? <KnowledgeEvents events={data?.events ?? []} loading={loading} now={now} /> : null}
      </Panel>
    </div>
  );
}

function KnowledgeProposals({
  proposals,
  loading,
  isAdmin,
  now,
  onApprove,
  onReject,
}: {
  proposals: KnowledgeProposal[];
  loading: boolean;
  isAdmin: boolean;
  now: number;
  onApprove: (proposalId: string) => Promise<void>;
  onReject: (proposalId: string) => Promise<void>;
}) {
  const [busyId, setBusyId] = React.useState<string | null>(null);
  if (loading && proposals.length === 0) return <EmptyState text="Loading proposals..." />;
  if (proposals.length === 0) return <EmptyState text="No proposals match the current filters." />;
  return (
    <div className="grid gap-2">
      {proposals.map((proposal) => (
        <article key={proposal.id} className="grid min-w-0 gap-2 rounded-md border border-border bg-white p-3">
          <KnowledgeRowHeader scope={proposal.scope} type={proposal.type} confidence={proposal.confidence} status={proposal.status} timestamp={proposal.updated_at} now={now} />
          <p className="min-w-0 break-words text-sm font-medium [overflow-wrap:anywhere]">{proposal.rule || proposal.reason || "No reusable rule proposed."}</p>
          {proposal.reason ? <p className="min-w-0 break-words text-xs text-muted [overflow-wrap:anywhere]">{proposal.reason}</p> : null}
          {proposal.source_event ? <KnowledgeSourceEvent event={proposal.source_event} /> : <p className="font-mono text-xs text-muted">Source event {proposal.event_id}</p>}
          {proposal.error ? <Banner tone="error" text={proposal.error} /> : null}
          {isAdmin && proposal.status === "proposed" ? (
            <div className="flex flex-wrap gap-2">
              <button
                className="inline-flex h-8 items-center gap-2 rounded-md bg-primary px-3 text-sm font-semibold text-white disabled:opacity-60"
                type="button"
                disabled={busyId === proposal.id}
                onClick={async () => {
                  setBusyId(proposal.id);
                  try {
                    await onApprove(proposal.id);
                  } finally {
                    setBusyId(null);
                  }
                }}
              >
                <CheckCircle2 className="h-4 w-4" aria-hidden />
                Approve
              </button>
              <button
                className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-sm font-semibold text-foreground hover:bg-slate-50 disabled:opacity-60"
                type="button"
                disabled={busyId === proposal.id}
                onClick={async () => {
                  setBusyId(proposal.id);
                  try {
                    await onReject(proposal.id);
                  } finally {
                    setBusyId(null);
                  }
                }}
              >
                <X className="h-4 w-4" aria-hidden />
                Reject
              </button>
            </div>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function KnowledgeSourceEvent({ event }: { event: KnowledgeEvent }) {
  const actor = event.trigger_actor || (event.actor !== "github" ? event.actor : null);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-border bg-slate-50 px-2 py-1.5">
      <ActorLabel actor={actor} avatarUrl={event.trigger_actor_avatar_url} framed />
      {event.source_job_id ? (
        <a className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-white px-2 text-xs font-semibold text-foreground hover:bg-slate-50" href={jobPath(event.source_job_id)}>
          <Link className="h-3.5 w-3.5" aria-hidden />
          Job #{event.source_job_id}
        </a>
      ) : null}
      {event.github_urls.length > 0 ? <GitHubLinkList urls={event.github_urls} compact /> : <span className="font-mono text-xs text-muted">No GitHub link</span>}
    </div>
  );
}

function KnowledgeRules({ rules, loading, isAdmin, now, onDeleteRule }: { rules: KnowledgeRule[]; loading: boolean; isAdmin: boolean; now: number; onDeleteRule: (ruleId: string) => Promise<void> }) {
  const [busyId, setBusyId] = React.useState<string | null>(null);
  if (loading && rules.length === 0) return <EmptyState text="Loading curated rules..." />;
  if (rules.length === 0) return <EmptyState text="No curated rules match the current filters." />;
  return (
    <div className="grid gap-2">
      {rules.map((rule) => {
        const sources = rule.source_event_details ?? [];
        const primarySource = sources[0];
        const actor = primarySource ? primarySource.trigger_actor || (primarySource.actor !== "github" ? primarySource.actor : null) : null;
        const sourceUrls = sources.flatMap((source) => source.github_urls ?? []);
        return (
          <article key={rule.id} className="grid min-w-0 gap-2 rounded-md border border-border bg-white p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <KnowledgeRowHeader scope={rule.scope} type={rule.type} confidence={rule.confidence} status={`${rule.observations} observation${rule.observations === 1 ? "" : "s"}`} timestamp={rule.last_seen} now={now} />
              {isAdmin ? (
                <button
                  className="inline-flex h-8 items-center gap-2 rounded-md border border-red-200 px-3 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-60"
                  type="button"
                  disabled={busyId === rule.id}
                  onClick={async () => {
                    if (!window.confirm("Delete this curated rule?")) return;
                    setBusyId(rule.id);
                    try {
                      await onDeleteRule(rule.id);
                    } finally {
                      setBusyId(null);
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                  Delete
                </button>
              ) : null}
            </div>
            <p className="min-w-0 break-words text-sm font-medium [overflow-wrap:anywhere]">{rule.rule}</p>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <ActorLabel actor={actor} avatarUrl={primarySource?.trigger_actor_avatar_url} framed />
              {primarySource?.source_job_id ? (
                <a className="inline-flex h-7 items-center gap-1 rounded-md border border-border px-2 text-xs font-semibold text-foreground hover:bg-white" href={jobPath(primarySource.source_job_id)}>
                  <Link className="h-3.5 w-3.5" aria-hidden />
                  Job #{primarySource.source_job_id}
                </a>
              ) : null}
              {sourceUrls.length > 0 ? <GitHubLinkList urls={sourceUrls} compact /> : <span className="font-mono text-xs text-muted">No GitHub link</span>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function KnowledgeEvents({ events, loading, now }: { events: KnowledgeEvent[]; loading: boolean; now: number }) {
  if (loading && events.length === 0) return <EmptyState text="Loading captured events..." />;
  if (events.length === 0) return <EmptyState text="No captured feedback events match the current filters." />;
  return (
    <div className="grid gap-2">
      {events.map((event) => {
        const actor = event.trigger_actor || (event.actor !== "github" ? event.actor : null);
        return (
          <details key={event.id} className="group rounded-md border border-border bg-white">
            <summary className="grid cursor-pointer list-none gap-2 px-3 py-2 marker:hidden hover:bg-slate-50">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="inline-flex min-w-0 items-center gap-2 font-mono text-xs font-semibold text-muted">
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-180" aria-hidden />
                  <span className="truncate">{repoFromScope(event.scope)}</span>
                </span>
                <span className="shrink-0 font-mono text-xs text-muted"><TimeText value={event.occurred_at} relative now={now} /></span>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <ActorLabel actor={actor} avatarUrl={event.trigger_actor_avatar_url} framed />
                {event.source_job_id ? (
                  <a className="inline-flex h-7 items-center gap-1 rounded-md border border-border px-2 text-xs font-semibold text-foreground hover:bg-white" href={jobPath(event.source_job_id)}>
                    <Link className="h-3.5 w-3.5" aria-hidden />
                    Job #{event.source_job_id}
                  </a>
                ) : null}
                {event.github_urls.length > 0 ? <GitHubLinkList urls={event.github_urls} compact /> : <span className="font-mono text-xs text-muted">No GitHub link</span>}
              </div>
              <p className="line-clamp-2 break-words text-sm [overflow-wrap:anywhere]">{event.comment}</p>
            </summary>
            <div className="grid gap-2 border-t border-border px-3 py-2">
              <div>
                <h3 className="mb-1 text-xs font-semibold text-muted">GitHub links</h3>
                {event.github_urls.length > 0 ? <GitHubLinkList urls={event.github_urls} /> : <p className="text-xs text-muted">No links recorded.</p>}
              </div>
              <pre className="max-h-72 overflow-auto rounded-md bg-slate-950 px-3 py-2 font-mono text-xs leading-relaxed text-slate-100">{JSON.stringify(event.context, null, 2)}</pre>
            </div>
          </details>
        );
      })}
    </div>
  );
}

function GitHubLinkList({ urls, compact = false }: { urls: string[]; compact?: boolean }) {
  const visible = compact ? urls.slice(0, 2) : urls;
  const extra = urls.length - visible.length;
  return (
    <div className="flex min-w-0 max-w-full flex-wrap gap-1.5">
      {visible.map((url) => (
        <a
          key={url}
          className={cn(
            "inline-flex max-w-full items-center gap-1 rounded-md border border-border font-semibold text-primary hover:bg-white hover:underline",
            compact ? "h-7 px-2 text-xs" : "min-h-7 px-2 py-1 text-xs",
          )}
          href={safeExternalUrl(url)}
          aria-label={url}
          rel="noreferrer"
          target="_blank"
        >
          <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{compact ? githubLinkLabel(url) : url}</span>
        </a>
      ))}
      {extra > 0 ? <span className="inline-flex h-7 items-center rounded-md border border-border px-2 font-mono text-xs text-muted">+{extra}</span> : null}
    </div>
  );
}

function githubLinkLabel(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.pathname.replace(/^\//, "") + parsed.hash;
  } catch {
    return url;
  }
}

function KnowledgeRowHeader({ scope, type, confidence, status, timestamp, now }: { scope: string; type: string; confidence: number; status: string; timestamp: string; now: number }) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted">
      <span className="truncate font-mono font-semibold text-foreground">{repoFromScope(scope)}</span>
      <span className="rounded-sm border border-border px-1.5 py-0.5 font-mono">{type}</span>
      <span className="rounded-sm border border-border px-1.5 py-0.5 font-mono">{Math.round(confidence * 100)}%</span>
      <span className="rounded-sm border border-border px-1.5 py-0.5 font-mono">{status}</span>
      <span className="font-mono"><TimeText value={timestamp} relative now={now} /></span>
    </div>
  );
}

function UserMenu({ user, loading }: { user: UserProfile | undefined; loading: boolean }) {
  const login = user?.login ? `@${user.login}` : loading ? "Loading profile..." : "GitHub OAuth";
  const mode = user?.is_admin ? "admin" : "read-only";
  const avatar = user?.avatar_url ? (
    <img className="h-10 w-10 rounded-full border border-slate-700 bg-slate-800" src={user.avatar_url} alt={user.login ? `${user.login} avatar` : ""} referrerPolicy="no-referrer" />
  ) : (
    <span className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-700 bg-slate-900">
      <UserCircle2 className="h-5 w-5" aria-hidden />
    </span>
  );
  const identity = user?.html_url ? (
    <a className="truncate font-semibold text-white hover:underline" href={safeExternalUrl(user.html_url)} rel="noreferrer" target="_blank">
      {login}
    </a>
  ) : (
    <div className="truncate font-semibold text-white">{login}</div>
  );
  return (
    <div className="flex max-w-full shrink-0 items-center gap-3 text-sm text-slate-300" aria-label={user?.login ? `Signed in as ${user.login}` : "Dashboard account"}>
      <ShieldCheck className="hidden h-4 w-4 shrink-0 sm:block" aria-hidden />
      <div className="hidden min-w-0 text-right sm:block">
        {identity}
        <div className="text-xs text-slate-400">Signed in · {mode}</div>
      </div>
      {avatar}
    </div>
  );
}

function JobsHeader({ count, limit, loading, onRefresh }: { count: number; limit: number; loading: boolean; onRefresh: () => void }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-lg border border-border bg-white px-3 py-3 shadow-sm md:px-4">
      <div className="min-w-0">
        <h2 className="text-base font-semibold">Jobs</h2>
        <p className="text-xs text-muted">
          {loading ? "Refreshing latest jobs..." : `Showing ${count} of the latest ${limit} requested jobs`}
        </p>
      </div>
      <RefreshButton onClick={onRefresh} compactOnMobile />
    </div>
  );
}

function Panel({ title, action, children, className, flushHeader = false }: { title: string; action?: React.ReactNode; children: React.ReactNode; className?: string; flushHeader?: boolean }) {
  return (
    <section className={cn("min-w-0 rounded-lg border border-border bg-panel p-4 shadow-sm", className)}>
      <div className={cn("flex flex-wrap items-center justify-between gap-3", !flushHeader && "mb-4")}>
        <h2 className="text-sm font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Metric({ title, value, icon }: { title: string; value: number; icon: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-panel p-3 shadow-sm md:p-4">
      <div className="flex items-center justify-between text-muted">
        <span className="text-sm font-medium">{title}</span>
        {icon}
      </div>
      <strong className="mt-3 block text-2xl leading-none md:mt-4 md:text-3xl">{value}</strong>
    </div>
  );
}

function Filters({ filters, actorOptions, onChange }: { filters: JobFilters; actorOptions: JobActor[]; onChange: (filters: JobFilters) => void }) {
  const [draft, setDraft] = React.useState(filters);
  React.useEffect(() => setDraft(filters), [filters]);
  const canClear = hasActiveJobFilters(filters) || hasActiveJobFilters(draft);
  const clearFilters = () => {
    setDraft(emptyJobFilters);
    onChange(emptyJobFilters);
  };

  return (
    <details className="my-3 rounded-md border border-border bg-slate-50/70">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm font-semibold marker:hidden">
        <span className="inline-flex items-center gap-2">
          <Filter className="h-4 w-4 text-muted" aria-hidden />
          Filters
        </span>
        <ChevronDown className="h-4 w-4 text-muted" aria-hidden />
      </summary>
      <form
        className="grid gap-3 border-t border-border bg-white p-3 md:grid-cols-3 xl:grid-cols-9"
        onSubmit={(event) => {
          event.preventDefault();
          onChange(draft);
        }}
      >
        <Field label="Status">
          <select className="control" value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
            <option value="">All</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="blocked">blocked</option>
            <option value="done">done</option>
            <option value="denied">denied</option>
            <option value="waiting_approval">waiting_approval</option>
          </select>
        </Field>
        <Field label="Repository">
          <input className="control" value={draft.repo} placeholder="owner/repo" onChange={(event) => setDraft({ ...draft, repo: event.target.value })} />
        </Field>
        <Field label="Thread">
          <input className="control" value={draft.thread} inputMode="numeric" placeholder="issue or PR" onChange={(event) => setDraft({ ...draft, thread: event.target.value })} />
        </Field>
        <Field label="Action">
          <input className="control" value={draft.action} placeholder="reply_comment" onChange={(event) => setDraft({ ...draft, action: event.target.value })} />
        </Field>
        <Field label="Actor" className="xl:col-span-2">
          <ActorFilter value={draft.actor} options={actorOptions} onChange={(actor) => setDraft({ ...draft, actor })} />
        </Field>
        <Field label="Intent">
          <select className="control" value={draft.intent} onChange={(event) => setDraft({ ...draft, intent: event.target.value })}>
            <option value="">All</option>
            <option value="review_only">review_only</option>
            <option value="work_allowed">work_allowed</option>
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-2 self-end xl:col-span-2">
          <button className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border px-3 text-sm font-semibold text-foreground hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white" type="button" disabled={!canClear} onClick={clearFilters}>
            <RotateCcw className="h-4 w-4" aria-hidden />
            Clear
          </button>
          <button className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-semibold text-white" type="submit">
            <Search className="h-4 w-4" aria-hidden />
            Apply
          </button>
        </div>
      </form>
    </details>
  );
}

function ActorFilter({ value, options, onChange }: { value: string; options: JobActor[]; onChange: (actor: string) => void }) {
  const [open, setOpen] = React.useState(false);
  const normalizedValue = value.trim().replace(/^@/, "").toLowerCase();
  const matches = options
    .filter((actor) => !normalizedValue || actor.login.toLowerCase().includes(normalizedValue))
    .slice(0, 8);
  const selected = options.find((actor) => actor.login.toLowerCase() === normalizedValue);

  return (
    <div className="relative min-w-0">
      <div className="control flex items-center gap-2 px-2">
        {selected ? (
          <img className="h-5 w-5 shrink-0 rounded-full bg-slate-100" src={safeExternalUrl(selected.avatar_url ?? "")} alt={`${selected.login} avatar`} referrerPolicy="no-referrer" />
        ) : (
          <UserCircle2 className="h-4 w-4 shrink-0 text-muted" aria-hidden />
        )}
        <input
          className="min-w-0 flex-1 bg-transparent font-mono text-sm outline-none"
          value={value}
          placeholder="@login"
          onChange={(event) => {
            onChange(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 100)}
        />
        {value ? (
          <button className="rounded-sm p-1 text-muted hover:bg-slate-100" type="button" aria-label="Clear actor filter" onClick={() => onChange("")}>
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
      </div>
      {open && matches.length > 0 ? (
        <div className="absolute left-0 right-0 z-20 mt-1 max-h-72 overflow-auto rounded-md border border-border bg-white p-1 shadow-lg">
          {matches.map((actor) => (
            <button
              key={actor.login}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-slate-50"
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange(actor.login);
                setOpen(false);
              }}
            >
              {actor.avatar_url ? (
                <img className="h-6 w-6 shrink-0 rounded-full bg-slate-100" src={safeExternalUrl(actor.avatar_url)} alt={`${actor.login} avatar`} referrerPolicy="no-referrer" />
              ) : (
                <UserCircle2 className="h-5 w-5 shrink-0 text-muted" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">@{actor.login}</span>
              <span className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-muted">{actor.job_count}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("grid min-w-0 gap-1 text-xs font-semibold text-muted", className)}>
      {label}
      {children}
    </label>
  );
}

function JobsList({
  jobs,
  loading,
  loadingMore = false,
  hasMore = false,
  onLoadMore,
  onViewJob,
  now,
  user,
  onRetry,
  onDismiss,
}: {
  jobs: Job[];
  loading: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
  onViewJob: (id: number) => void;
  now: number;
  user?: UserProfile;
  onRetry?: (jobId: number) => Promise<void>;
  onDismiss?: (jobId: number) => Promise<void>;
}) {
  const [retryingJobId, setRetryingJobId] = React.useState<number | null>(null);
  const [dismissingJobId, setDismissingJobId] = React.useState<number | null>(null);
  const loadMoreRequestedRef = React.useRef(false);
  const wasLoadingMoreRef = React.useRef(loadingMore);
  const canRetryFromList = Boolean(user?.is_admin && onRetry);
  const canDismissFromList = Boolean(user?.is_admin && onDismiss);
  React.useEffect(() => {
    if (wasLoadingMoreRef.current && !loadingMore) loadMoreRequestedRef.current = false;
    wasLoadingMoreRef.current = loadingMore;
  }, [loadingMore]);
  const requestMoreJobs = React.useCallback(() => {
    if (loadMoreRequestedRef.current) return;
    loadMoreRequestedRef.current = true;
    onLoadMore?.();
  }, [onLoadMore]);
  const retryJobFromList = React.useCallback(async (jobId: number) => {
    if (!onRetry) return;
    setRetryingJobId(jobId);
    try {
      await onRetry(jobId);
    } finally {
      setRetryingJobId(null);
    }
  }, [onRetry]);
  const dismissJobFromList = React.useCallback(async (jobId: number) => {
    if (!onDismiss) return;
    setDismissingJobId(jobId);
    try {
      await onDismiss(jobId);
    } finally {
      setDismissingJobId(null);
    }
  }, [onDismiss]);

  if (loading && jobs.length === 0) return <EmptyState text="Loading jobs..." />;
  if (jobs.length === 0) return <EmptyState text="No jobs match the current filters." />;
  return (
    <>
      <div className="grid gap-2 md:hidden">
        {jobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            onViewJob={onViewJob}
            now={now}
            canRetry={canRetryFromList && isRetryableStatus(job.status)}
            retrying={retryingJobId === job.id}
            onRetry={retryJobFromList}
            canDismiss={canDismissFromList && isRetryableStatus(job.status)}
            dismissing={dismissingJobId === job.id}
            onDismiss={dismissJobFromList}
          />
        ))}
        <MobileLoadMoreJobs hasMore={hasMore} loading={loadingMore} onLoadMore={requestMoreJobs} />
      </div>
      <div className="hidden max-h-[640px] overflow-auto rounded-md border border-border md:block">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr className="sticky top-0 z-10 border-b border-border bg-panel text-left text-xs text-muted">
              <th className="px-2 py-2 font-semibold">ID</th>
              <th className="px-2 py-2 font-semibold">Status</th>
              <th className="px-2 py-2 font-semibold">Repo / thread</th>
              <th className="px-2 py-2 font-semibold">Action</th>
              <th className="px-2 py-2 font-semibold">Actor</th>
              <th className="px-2 py-2 font-semibold">Attempts</th>
              <th className="px-2 py-2 font-semibold">Queue wait</th>
              <th className="px-2 py-2 font-semibold">Runtime</th>
              <th className="px-2 py-2 font-semibold">Updated</th>
              <th className="px-2 py-2 text-right font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr
                key={job.id}
                className="cursor-pointer border-b border-border hover:bg-slate-50"
                onClick={() => onViewJob(job.id)}
              >
                <td className="px-2 py-3 font-mono">#{job.id}</td>
                <td className="px-2 py-3">
                  <StatusBadge status={job.status} />
                </td>
                <td className="px-2 py-3">
                  <div className="font-mono">{job.repo ?? job.work_key}</div>
                  <div className="text-xs text-muted">thread {job.thread ?? "n/a"}</div>
                </td>
                <td className="px-2 py-3">
                  <div>{job.action}</div>
                  <div className="text-xs text-muted">{job.intent}</div>
                </td>
                <td className="px-2 py-3">
                  <ActorLabel actor={job.trigger_actor} avatarUrl={job.trigger_actor_avatar_url} />
                </td>
                <td className="px-2 py-3">{job.attempts}</td>
                <td className="px-2 py-3">{formatSeconds(queueWaitSeconds(job, now))}</td>
                <td className="px-2 py-3">{formatSeconds(jobRuntimeSeconds(job, now))}</td>
                <td className="px-2 py-3 font-mono text-xs"><TimeText value={job.updated_at} compact relative now={now} /></td>
                <td className="px-2 py-3 text-right">
                  <div className="inline-flex items-center gap-1">
                    <RetryJobButton jobId={job.id} canRetry={canRetryFromList && isRetryableStatus(job.status)} retrying={retryingJobId === job.id} onRetry={retryJobFromList} compact />
                    <DismissJobButton jobId={job.id} canDismiss={canDismissFromList && isRetryableStatus(job.status)} dismissing={dismissingJobId === job.id} onDismiss={dismissJobFromList} compact />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <JobsLoadSentinel hasMore={hasMore} loading={loadingMore} onLoadMore={requestMoreJobs} />
      </div>
    </>
  );
}

function MobileLoadMoreJobs({ hasMore, loading, onLoadMore }: { hasMore: boolean; loading: boolean; onLoadMore?: () => void }) {
  if (!hasMore && !loading) return null;
  return (
    <button
      className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-md border border-border px-3 py-2 text-sm font-semibold text-foreground hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 md:hidden"
      type="button"
      disabled={loading || !hasMore}
      onClick={() => onLoadMore?.()}
    >
      <ChevronDown className="h-4 w-4" aria-hidden />
      {loading ? "Loading more jobs..." : "Load more jobs"}
    </button>
  );
}

function JobsLoadSentinel({ hasMore, loading, onLoadMore }: { hasMore: boolean; loading: boolean; onLoadMore?: () => void }) {
  const ref = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    const node = ref.current;
    if (!node || !hasMore || loading || !onLoadMore) return;
    if (!("IntersectionObserver" in window)) {
      onLoadMore();
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
    }, { rootMargin: "240px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, loading, onLoadMore]);

  if (!hasMore && !loading) return null;
  return (
    <div ref={ref} className="flex min-h-10 items-center justify-center px-3 py-2 text-xs font-medium text-muted" aria-live="polite">
      {loading ? "Loading more jobs..." : "Scroll for more jobs"}
    </div>
  );
}

function JobCard({
  job,
  onViewJob,
  now,
  canRetry,
  retrying,
  onRetry,
  canDismiss,
  dismissing,
  onDismiss,
}: {
  job: Job;
  onViewJob: (id: number) => void;
  now: number;
  canRetry: boolean;
  retrying: boolean;
  onRetry: (jobId: number) => Promise<void>;
  canDismiss: boolean;
  dismissing: boolean;
  onDismiss: (jobId: number) => Promise<void>;
}) {
  return (
    <article className="rounded-md border border-border bg-white shadow-[0_1px_0_rgba(15,23,42,0.03)]">
      <button className="grid w-full gap-2 p-3 text-left hover:bg-slate-50" type="button" onClick={() => onViewJob(job.id)}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 space-y-1">
            <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-2">
              <span className="shrink-0 font-mono text-xs font-semibold text-muted">#{job.id}</span>
              <span className="truncate font-mono text-sm">{job.repo ?? job.work_key}</span>
            </div>
            <div className="line-clamp-2 text-sm leading-snug text-foreground">{job.subject}</div>
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
              <span>thread {job.thread ?? "n/a"} · {job.action}</span>
              <ActorLabel actor={job.trigger_actor} avatarUrl={job.trigger_actor_avatar_url} />
            </div>
          </div>
          <StatusBadge status={job.status} />
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <MiniStat label="Wait" value={formatSeconds(queueWaitSeconds(job, now))} />
          <MiniStat label="Runtime" value={formatSeconds(jobRuntimeSeconds(job, now))} />
          <MiniStat label="Updated" value={<TimeText value={job.updated_at} compact relative now={now} />} />
        </div>
      </button>
      {canRetry || canDismiss ? (
        <div className="flex flex-wrap gap-2 border-t border-border px-3 py-2">
          <RetryJobButton jobId={job.id} canRetry={canRetry} retrying={retrying} onRetry={onRetry} />
          <DismissJobButton jobId={job.id} canDismiss={canDismiss} dismissing={dismissing} onDismiss={onDismiss} />
        </div>
      ) : null}
    </article>
  );
}

function RetryJobButton({
  jobId,
  canRetry,
  retrying,
  onRetry,
  compact = false,
}: {
  jobId: number;
  canRetry: boolean;
  retrying: boolean;
  onRetry: (jobId: number) => Promise<void>;
  compact?: boolean;
}) {
  if (!canRetry) return null;
  const label = retrying ? "Retrying..." : "Retry";
  return (
    <button
      className={cn(
        "inline-flex h-8 items-center justify-center gap-2 rounded-md border border-border text-sm font-semibold text-foreground hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60",
        compact ? "w-8 px-0" : "px-3",
      )}
      type="button"
      disabled={retrying}
      aria-label={`Retry job #${jobId}`}
      title={`Retry job #${jobId}`}
      onClick={async (event) => {
        event.stopPropagation();
        if (!window.confirm(`Retry job #${jobId}?`)) return;
        await onRetry(jobId);
      }}
    >
      <RotateCcw className="h-4 w-4" aria-hidden />
      <span className={cn(compact && "sr-only")}>{label}</span>
    </button>
  );
}

function DismissJobButton({
  jobId,
  canDismiss,
  dismissing,
  onDismiss,
  compact = false,
}: {
  jobId: number;
  canDismiss: boolean;
  dismissing: boolean;
  onDismiss: (jobId: number) => Promise<void>;
  compact?: boolean;
}) {
  if (!canDismiss) return null;
  const label = dismissing ? "Dismissing..." : "Dismiss";
  return (
    <button
      className={cn(
        "inline-flex h-8 items-center justify-center gap-2 rounded-md border border-border text-sm font-semibold text-foreground hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60",
        compact ? "w-8 px-0" : "px-3",
      )}
      type="button"
      disabled={dismissing}
      aria-label={`Dismiss job #${jobId}`}
      title={`Dismiss job #${jobId}`}
      onClick={async (event) => {
        event.stopPropagation();
        if (!window.confirm(`Dismiss job #${jobId}?`)) return;
        await onDismiss(jobId);
      }}
    >
      <CheckCircle2 className="h-4 w-4" aria-hidden />
      <span className={cn(compact && "sr-only")}>{label}</span>
    </button>
  );
}

function ActorLabel({ actor, avatarUrl, framed = false }: { actor: string | null | undefined; avatarUrl?: string | null; framed?: boolean }) {
  const avatar = avatarUrl ? (
    <img className="h-4 w-4 shrink-0 rounded-full bg-slate-100" src={safeExternalUrl(avatarUrl)} alt={actor ? `${actor} avatar` : ""} referrerPolicy="no-referrer" />
  ) : (
    <UserCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden />
  );
  const content = (
    <>
      {avatar}
      <span className="min-w-0 truncate">{actor ? `@${actor}` : "unknown actor"}</span>
    </>
  );
  if (framed) {
    return <span className="inline-flex h-7 max-w-full items-center gap-1 rounded-md border border-border px-2 text-xs font-semibold text-muted">{content}</span>;
  }
  return <span className="inline-flex min-w-0 max-w-full items-center gap-1 font-mono text-xs text-muted">{content}</span>;
}

function JobDetail({ job, session, sessionEvents, transcript, now, compact = false }: { job: Job; session: SessionCorrelation | undefined; sessionEvents: SessionEvent[] | undefined; transcript: TranscriptEntry[] | undefined; now: number; compact?: boolean }) {
  const shareHref = jobPath(job.id);
  const eventRows = sessionEvents ?? [];
  const transcriptRows = transcript ?? [];
  const activityGroups = groupSessionEvents(eventRows);
  const transcriptGroups = groupTranscriptEntries(transcriptRows);
  const liveRuntime = jobRuntimeSeconds(job, now);
  const liveWait = queueWaitSeconds(job, now);
  return (
    <div className="grid min-w-0 gap-4">
      <div className="sticky top-0 z-20 -mx-3 -mt-3 grid gap-2 border-b border-border bg-panel/95 px-3 py-2 shadow-sm backdrop-blur sm:-mx-4 sm:-mt-4 sm:px-4" aria-label="Sticky job header">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <StatusBadge status={job.status} />
          <a className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-white px-2 text-xs font-semibold text-foreground hover:bg-slate-50" href={shareHref}>
            <Link className="h-3.5 w-3.5" aria-hidden />
            Job #{job.id}
          </a>
          <ActorLabel actor={job.trigger_actor} avatarUrl={job.trigger_actor_avatar_url} framed />
          <span className="min-w-0 break-words font-mono text-xs text-muted [overflow-wrap:anywhere]">{job.work_key}</span>
        </div>
        <div className="grid min-w-0 gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(280px,auto)] lg:items-start">
          <p className="min-w-0 break-words text-sm text-muted [overflow-wrap:anywhere]">{job.subject}</p>
          <div className="grid min-w-0 gap-1">
            <h3 className="text-xs font-semibold text-muted">GitHub links</h3>
            {job.github_urls.length > 0 ? <GitHubLinkList urls={job.github_urls} compact /> : <p className="text-xs text-muted">No links recorded.</p>}
          </div>
        </div>
      </div>
      <div className={cn("grid gap-2 text-sm sm:gap-3", compact ? "grid-cols-1" : "grid-cols-3")}>
        <MiniStat label="Queue wait" value={formatSeconds(liveWait)} />
        <MiniStat label={job.status === "running" ? "Running for" : "Runtime"} value={formatSeconds(liveRuntime)} />
        <MiniStat label="Coalesced" value={String(job.coalesced_count)} />
      </div>
      <div className={cn("grid gap-2 text-sm sm:gap-3", compact ? "grid-cols-1" : "grid-cols-2 xl:grid-cols-4")}>
        <MiniStat label="Created" value={<TimeText value={job.created_at} compact relative now={now} />} />
        <MiniStat label="Started" value={job.started_at ? <TimeText value={job.started_at} compact relative now={now} /> : "n/a"} />
        <MiniStat label="Updated" value={<TimeText value={job.updated_at} compact relative now={now} />} />
        <MiniStat label="Finished" value={job.finished_at ? <TimeText value={job.finished_at} compact relative now={now} /> : "n/a"} />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Timeline</h3>
        <div className="grid min-w-0 gap-3">
          {(job.worklog ?? []).length > 0 ? (
            job.worklog?.map((entry) => (
              <div key={entry.id} className="min-w-0 border-l-2 border-primary pl-3">
                <div className="text-sm font-semibold">{entry.phase}</div>
                <div className="font-mono text-xs text-muted"><TimeText value={entry.ts} relative now={now} /></div>
                <div className="break-words text-sm [overflow-wrap:anywhere]">{entry.summary}</div>
                {entry.detail ? <div className="mt-1 break-words font-mono text-xs text-muted [overflow-wrap:anywhere]">{entry.detail}</div> : null}
              </div>
            ))
          ) : (
            <EmptyState text="No worklog entries." />
          )}
        </div>
      </div>
      <div>
        <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <TerminalSquare className="h-4 w-4" aria-hidden />
          OpenClaw session
        </h3>
        {session ? (
          <div className="grid gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <MiniStat label="Session ID" value={session.id} />
              <MiniStat label="Source" value={session.source} />
            </div>
            <p className="break-words text-xs text-muted [overflow-wrap:anywhere]">{session.detail}</p>
          </div>
        ) : (
          <EmptyState text="Session correlation is loading." />
        )}
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Agent activity</h3>
        <div className="grid max-h-[460px] min-w-0 gap-2 overflow-auto pr-1">
          {activityGroups.length > 0 ? (
            activityGroups.map((event, index) => (
              <SessionEventRow key={event.id} event={event} defaultOpen={defaultLogOpen(event.eventType, job.status === "running", index, activityGroups.length)} now={now} />
            ))
          ) : (
            <EmptyState text={job.status === "running" ? "Waiting for live agent output..." : "No agent activity has been recorded for this session."} />
          )}
        </div>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Session transcript</h3>
        <div className="grid max-h-[620px] min-w-0 gap-2 overflow-auto pr-1">
          {transcriptGroups.length > 0 ? (
            transcriptGroups.map((entry, index) => (
              <TranscriptRow key={entry.id} entry={entry} defaultOpen={defaultLogOpen(entry.kind, job.status === "running", index, transcriptGroups.length)} now={now} />
            ))
          ) : (
            <EmptyState text={job.status === "running" ? "Waiting for live transcript entries..." : "No OpenClaw transcript entries are available for this session."} />
          )}
        </div>
      </div>
    </div>
  );
}

function TranscriptRow({ entry, defaultOpen, now }: { entry: TranscriptEntryGroup; defaultOpen?: boolean; now: number }) {
  return (
    <CollapsibleLogSection
      badge={entry.badge}
      meta={<TimeText value={entry.meta} relative now={now} />}
      count={entry.count}
      summary={entry.summary}
      defaultOpen={defaultOpen}
    >
      <pre className="max-h-72 max-w-full overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 px-2 py-1.5 font-mono text-xs leading-relaxed text-slate-100 [overflow-wrap:anywhere]">{entry.text}</pre>
    </CollapsibleLogSection>
  );
}

function SessionEventRow({ event, defaultOpen, now }: { event: SessionEventGroup; defaultOpen?: boolean; now: number }) {
  return (
    <CollapsibleLogSection badge={event.badge} meta={<TimeText value={event.meta} relative now={now} />} count={event.count} summary={event.summary} defaultOpen={defaultOpen}>
      {event.detail ? <pre className="max-h-56 max-w-full overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 px-2 py-1.5 font-mono text-xs leading-relaxed text-slate-100 [overflow-wrap:anywhere]">{event.detail}</pre> : null}
    </CollapsibleLogSection>
  );
}

function CollapsibleLogSection({
  badge,
  meta,
  count,
  summary,
  defaultOpen,
  children,
}: {
  badge: string;
  meta: React.ReactNode;
  count?: number;
  summary: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [isOpen, setIsOpen] = React.useState(Boolean(defaultOpen));
  return (
    <details className="group min-w-0 rounded border border-border bg-slate-50/60" open={isOpen} onToggle={(event) => setIsOpen(event.currentTarget.open)}>
      <summary className="grid cursor-pointer list-none gap-1 px-2 py-1.5 marker:hidden hover:bg-white">
        <div className="grid min-w-0 gap-1 sm:flex sm:items-center sm:justify-between sm:gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted transition-transform group-open:rotate-180" aria-hidden />
            <span className="truncate font-mono text-[11px] font-semibold text-muted">{badge}</span>
            {count && count > 1 ? <span className="rounded-sm border border-border px-1 font-mono text-[10px] text-muted">{count}</span> : null}
          </div>
          <span className="min-w-0 truncate pl-5 font-mono text-[11px] text-muted sm:shrink-0 sm:pl-0">{meta}</span>
        </div>
        <div className="min-w-0 break-words pl-5 text-xs text-foreground [overflow-wrap:anywhere] sm:truncate">{summary}</div>
      </summary>
      <div className="min-w-0 border-t border-border bg-white px-2 py-2">{children}</div>
    </details>
  );
}

function PercentileChart({ label, values }: { label: string; values: Percentiles | undefined }) {
  const data = [
    { name: "median", seconds: values?.median ?? 0 },
    { name: "p90", seconds: values?.p90 ?? 0 },
    { name: "p99", seconds: values?.p99 ?? 0 },
  ];
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis tickFormatter={formatSeconds} />
          <Tooltip formatter={(value) => [formatSeconds(Number(value)), label]} />
          <Bar dataKey="seconds" fill="#0969da" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function JobsPerDayChart({ values, loading, totalJobs }: { values: Record<string, number> | undefined; loading: boolean; totalJobs: number }) {
  const data = Object.entries(values ?? {}).map(([day, count]) => ({ day, count }));
  if (loading && data.length === 0) return <EmptyState text="Loading job history..." />;
  if (data.length === 0) return <EmptyState text={totalJobs > 0 ? "Job history has no valid creation dates." : "No job history available."} />;
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="day" minTickGap={16} />
          <YAxis allowDecimals={false} />
          <Tooltip formatter={(value) => [Number(value), "jobs"]} />
          <Bar dataKey="count" fill="#16a34a" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function RuntimeUsageChart({ usage, loading, totalJobs }: { usage: RuntimeUsage | undefined; loading: boolean; totalJobs: number }) {
  const [grouping, setGrouping] = React.useState<keyof RuntimeUsage>("day");
  const rows = usage?.[grouping] ?? [];
  const data = rows.map((row) => ({
    ...row,
    label: runtimeBucketLabel(row.bucket, grouping),
  }));
  const totalSeconds = rows.reduce((total, row) => total + row.seconds, 0);
  if (loading && data.length === 0) return <EmptyState text="Loading runtime usage..." />;
  if (data.length === 0) return <EmptyState text={totalJobs > 0 ? "No jobs have recorded runtime yet." : "No job history available."} />;
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted">
          <TimerReset className="h-4 w-4" aria-hidden />
          <span>{formatRuntimeUsageSeconds(totalSeconds)} consumed across {rows.reduce((total, row) => total + row.jobs, 0)} job{rows.reduce((total, row) => total + row.jobs, 0) === 1 ? "" : "s"}</span>
        </div>
        <div className="inline-flex h-8 rounded-md border border-border bg-white p-0.5" aria-label="Runtime grouping">
          {(["day", "month"] as const).map((value) => (
            <button
              key={value}
              className={cn("rounded px-2.5 text-xs font-semibold capitalize", grouping === value ? "bg-primary text-white" : "text-muted hover:bg-slate-50")}
              type="button"
              onClick={() => setGrouping(value)}
            >
              {value}
            </button>
          ))}
        </div>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="label" minTickGap={16} tick={{ fontSize: 11 }} />
            <YAxis tickFormatter={(value) => formatRuntimeUsageSeconds(Number(value))} />
            <Tooltip
              formatter={(value, name) => {
                if (name === "seconds") return [formatRuntimeUsageSeconds(Number(value)), "runtime"];
                return [Number(value), String(name)];
              }}
              labelFormatter={(label) => String(label)}
            />
            <Bar dataKey="seconds" fill="#0969da" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function runtimeBucketLabel(bucket: string, grouping: keyof RuntimeUsage) {
  if (grouping === "month") {
    const [year, month] = bucket.split("-").map(Number);
    if (!year || !month) return bucket;
    return new Intl.DateTimeFormat(undefined, { month: "short", year: "numeric" }).format(new Date(year, month - 1, 1));
  }
  const [year, month, day] = bucket.split("-").map(Number);
  if (!year || !month || !day) return bucket;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(year, month - 1, day));
}

function formatRuntimeUsageSeconds(value: number) {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (remainingMinutes === 0) return `${hours}h`;
  return `${hours}h ${remainingMinutes}m`;
}

function totalJobs(counts: StatusCounts) {
  return Object.values(counts).reduce((total, value) => total + value, 0);
}

function SystemdUnits({ data, loading }: { data: SystemdResponse | undefined; loading: boolean }) {
  if (loading && !data) return <EmptyState text="Loading systemd units..." />;
  if (!data) return <EmptyState text="No systemd snapshot available." />;
  const units = data.units ?? [];
  const activeUnits = units.filter((unit) => unit.active_state === "active").length;
  const failedUnits = units.filter((unit) => unit.active_state === "failed" || unit.result === "failed").length;
  const timers = units.filter((unit) => unit.kind === "timer").length;
  return (
    <div className="grid min-w-0 gap-3">
      {data.errors.length > 0 ? <Banner tone="error" text={data.errors[0]} /> : null}
      {units.length === 0 ? (
        <EmptyState text="No configured bridge units found." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <SystemdSummaryMetric label="Units" value={String(units.length)} />
            <SystemdSummaryMetric label="Active" value={String(activeUnits)} />
            <SystemdSummaryMetric label="Failed" value={String(failedUnits)} tone={failedUnits > 0 ? "bad" : "neutral"} />
            <SystemdSummaryMetric label="Timers" value={String(timers)} />
          </div>
          <div className="overflow-hidden rounded-md border border-border bg-white">
            <div className="hidden grid-cols-[minmax(0,1.35fr)_90px_120px_90px_minmax(0,1fr)] gap-3 border-b border-border bg-slate-50 px-3 py-2 text-[11px] font-semibold uppercase text-muted md:grid">
              <span>Unit</span>
              <span>Status</span>
              <span>State</span>
              <span>PID</span>
              <span>Schedule</span>
            </div>
            <div className="divide-y divide-border">
              {units.map((unit) => (
                <SystemdUnitRow key={unit.unit} unit={unit} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SystemdSummaryMetric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "bad" }) {
  return (
    <div className={cn("min-w-0 rounded-md border bg-white px-3 py-2", tone === "bad" ? "border-red-200" : "border-border")}>
      <div className={cn("font-mono text-lg font-semibold", tone === "bad" ? "text-red-700" : "text-foreground")}>{value}</div>
      <div className="mt-0.5 text-[11px] font-semibold uppercase text-muted">{label}</div>
    </div>
  );
}

function SystemdUnitRow({ unit }: { unit: SystemdUnit }) {
  const [isOpen, setIsOpen] = React.useState(false);
  const active = unit.active_state === "active";
  const failed = unit.active_state === "failed" || unit.result === "failed";
  const schedule = unit.next_elapse || unit.last_trigger || "n/a";
  return (
    <details className="group min-w-0" open={isOpen} onToggle={(event) => setIsOpen(event.currentTarget.open)}>
      <summary className="grid min-w-0 cursor-pointer list-none grid-cols-2 gap-2 px-3 py-3 text-sm marker:hidden hover:bg-slate-50 md:grid-cols-[minmax(0,1.35fr)_90px_120px_90px_minmax(0,1fr)] md:items-center md:gap-3">
        <div className="col-span-2 min-w-0 md:col-span-1">
          <div className="flex min-w-0 items-center gap-2">
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted transition-transform group-open:rotate-180" aria-hidden />
            <span className="truncate font-mono text-xs font-semibold text-foreground">{unit.unit}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 pl-5 text-[11px] font-semibold uppercase text-muted">
            <span>{unit.role}</span>
            <span>{unit.kind}</span>
            <span>{unit.load_state}</span>
            <span>{isOpen ? "following log" : "expand log"}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 md:block">
          <span className="text-[11px] font-semibold uppercase text-muted md:hidden">Status</span>
          <span className={cn("inline-flex h-6 items-center rounded-full border px-2 text-xs font-semibold", failed ? "border-red-300 bg-red-50 text-red-700" : active ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-slate-300 bg-slate-50 text-slate-700")}>
            {unit.active_state}
          </span>
        </div>
        <SystemdFact label="State" value={unit.sub_state} detail={unit.result || "unknown"} />
        <SystemdFact label="PID" value={unit.main_pid ? String(unit.main_pid) : "n/a"} detail={formatSeconds(unit.uptime_seconds)} />
        <div className="col-span-2 min-w-0 md:col-span-1">
          <div className="text-[11px] font-semibold uppercase text-muted md:hidden">Schedule</div>
          <div className="truncate font-mono text-[11px] text-foreground" title={schedule}>
            {unit.next_elapse ? `next ${unit.next_elapse}` : schedule}
          </div>
        </div>
      </summary>
      <div className="border-t border-border bg-slate-50 px-3 pb-3 pt-2">
        <LiveJournalTail unit={unit.unit} active={isOpen} />
      </div>
    </details>
  );
}

function SystemdFact({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-semibold uppercase text-muted md:hidden">{label}</div>
      <div className="truncate font-mono text-[11px] text-foreground">{value}</div>
      {detail ? <div className="truncate font-mono text-[11px] text-muted">{detail}</div> : null}
    </div>
  );
}

function LiveJournalTail({ unit, active }: { unit: string; active: boolean }) {
  const [lines, setLines] = React.useState<JournalLine[]>([]);
  const [error, setError] = React.useState("");
  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (!active) {
      setLines([]);
      setError("");
      return;
    }
    setLines([]);
    setError("");
    const source = new EventSource(`/api/systemd/journal/stream?unit=${encodeURIComponent(unit)}`);
    source.addEventListener("journal_line", (message) => {
      const payload = parseSseData<JournalLine>(message);
      if (!payload) return;
      setLines((current) => [...current.slice(-199), payload]);
    });
    source.addEventListener("journal_error", (message) => {
      const payload = parseSseData<{ unit: string; error: string }>(message);
      setError(payload?.error ?? "journal stream failed");
    });
    source.onerror = () => undefined;
    return () => source.close();
  }, [active, unit]);

  React.useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [lines]);

  return (
    <div className="grid min-w-0 gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase text-muted">Live journal</div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-muted">{lines.length > 0 ? `${lines.length} lines streamed` : "waiting for journal output"}</div>
        </div>
        <div className="font-mono text-[11px] text-muted">journalctl -f</div>
      </div>
      {error ? <Banner tone="error" text={error} /> : null}
      <div ref={scrollRef} className="h-72 overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-xs leading-relaxed text-slate-100">
        {lines.length > 0 ? (
          lines.map((item, index) => (
            <div key={`${item.unit}-${index}`} className="break-words [overflow-wrap:anywhere]">
              {item.line}
            </div>
          ))
        ) : (
          <div className="text-slate-400">No journal lines received yet.</div>
        )}
      </div>
    </div>
  );
}

function ProcessActivity({ data, loading }: { data: ProcessesResponse | undefined; loading: boolean }) {
  if (loading && !data) return <EmptyState text="Loading process activity..." />;
  if (!data) return <EmptyState text="No process snapshot available." />;
  const children = data.executor.children ?? [];
  const allProcesses = children.flatMap((process) => flattenProcessTree(process));
  const totalCpuTicks = allProcesses.reduce((total, process) => total + process.cpu_ticks, 0);
  const totalIoBytes = allProcesses.reduce((total, process) => total + totalIo(process), 0);
  const isActive = data.executor.service === "active";
  const currentChartData = allProcesses.slice(0, 8).map((process) => ({
    label: `pid ${process.pid}`,
    ticks: process.cpu_ticks,
  }));
  const sampleChartData = (data.samples ?? []).map((sample) => ({
    label: compactDate(sample.ts),
    ticks: sample.cpu_ticks,
    io: sample.io_bytes,
    active: sample.active_since_last_sample ? "active" : "quiet",
  }));
  const chartData = sampleChartData.length > 0 ? sampleChartData : currentChartData;
  const latestSample = data.samples?.[data.samples.length - 1];
  return (
    <div className="grid gap-4">
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={cn("inline-flex h-6 items-center rounded-full border px-2 text-xs font-semibold", isActive ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-slate-300 bg-white text-slate-600")}>
                {isActive ? "active" : "idle"}
              </span>
              <span className="font-mono text-xs text-muted">service {data.executor.service}</span>
            </div>
            <div className="mt-2 text-sm font-semibold text-foreground">
              {data.running_jobs.length > 0 ? `${data.running_jobs.length} running job${data.running_jobs.length === 1 ? "" : "s"}` : "No running jobs"}
            </div>
            {data.running_jobs.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {data.running_jobs.slice(0, 4).map((job) => (
                  <span key={job.id} className="inline-flex min-h-6 items-center gap-1.5 rounded-full border border-blue-200 bg-white px-2 font-mono text-[11px] font-semibold text-blue-700">
                    <span className="h-2 w-2 rounded-full bg-blue-600 animate-live-pulse" aria-hidden />
                    #{job.id} {formatSeconds(job.age_seconds)}
                  </span>
                ))}
              </div>
            ) : null}
            {latestSample ? (
              <p className="mt-1 text-xs text-muted">
                Last persisted sample {compactDate(latestSample.ts)} · {latestSample.active_since_last_sample ? "activity observed" : `quiet ${formatSeconds(latestSample.idle_seconds)}`}
              </p>
            ) : null}
            <p className="mt-1 text-xs text-muted">{data.detail}</p>
          </div>
          <div className="grid min-w-[190px] grid-cols-3 gap-2 text-center text-xs">
            <ProcessKpi label="PID" value={data.executor.pid ? String(data.executor.pid) : "n/a"} />
            <ProcessKpi label="Children" value={String(allProcesses.length)} />
            <ProcessKpi label="CPU ticks" value={String(totalCpuTicks)} />
          </div>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <SignalTile label="Live process" value={data.signals?.live_process.state ?? (allProcesses.length > 0 ? "live" : "no_child_process")} detail={`${data.signals?.live_process.child_count ?? allProcesses.length} children`} />
        <SignalTile label="Process activity" value={data.signals?.process_activity.state ?? (latestSample?.active_since_last_sample ? "active" : "quiet")} detail={latestSample ? `sample ${compactDate(latestSample.ts)}` : "no sample"} />
        <SignalTile label="Semantic progress" value={data.signals?.semantic_progress.length ? "recent" : "none"} detail={latestProgressSummary(data.running_jobs, "semantic_progress")} />
        <SignalTile label="Visible progress" value={data.signals?.visible_progress.length ? "streaming" : "none"} detail={latestProgressSummary(data.running_jobs, "visible_progress")} />
      </div>
      {data.alerts.length > 0 ? <Banner tone="error" text={data.alerts[0]} /> : null}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="min-w-0 rounded-md border border-border p-3">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Cpu className="h-4 w-4" aria-hidden />
              {sampleChartData.length > 0 ? "CPU history" : "CPU ticks"}
            </h3>
            <span className="font-mono text-xs text-muted">{formatBytes(totalIoBytes)} I/O</span>
          </div>
          {chartData.length > 0 ? (
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="label" tick={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value) => [Number(value), "cpu ticks"]} />
                  <Line type="monotone" dataKey="ticks" stroke="#0f766e" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState text="No executor CPU samples available." />
          )}
        </div>
        <div className="min-w-0">
          <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Cpu className="h-4 w-4" aria-hidden />
            Executor children
          </h3>
          {children.length > 0 ? (
            <div className="grid gap-2">
              {children.map((child) => (
                <ProcessRow key={child.pid} process={child} />
              ))}
            </div>
          ) : (
            <EmptyState text="No child process detected for the executor." />
          )}
        </div>
      </div>
    </div>
  );
}

function AlertsPanel({ alerts, loading, now }: { alerts: AlertRecord[] | undefined; loading: boolean; now: number }) {
  if (loading && !alerts) return <EmptyState text="Loading monitor alerts..." />;
  const rows = alerts ?? [];
  if (rows.length === 0) return <EmptyState text="No active monitor alerts." />;
  return (
    <div className="grid gap-2">
      {rows.slice(0, 5).map((alert) => (
        <div key={alert.fingerprint} className="rounded-md border border-red-200 bg-red-50 p-2.5">
          <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-red-700">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            <span>{alert.severity}</span>
            <span className="font-normal text-red-600">{formatRelativeTime(alert.last_seen, now)}</span>
            {alert.observations > 1 ? <span className="rounded-full border border-red-200 bg-white px-1.5">{alert.observations}x</span> : null}
          </div>
          <p className="mt-1 break-words text-sm font-medium text-red-950 [overflow-wrap:anywhere]">{alert.message}</p>
        </div>
      ))}
    </div>
  );
}

function ProcessRow({ process }: { process: ProcessSample }) {
  const read = process.io_bytes?.read_bytes ?? 0;
  const written = process.io_bytes?.write_bytes ?? 0;
  return (
    <div className="rounded-md border border-border bg-white p-2.5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono">pid {process.pid}</span>
        <span className="rounded-full border border-border px-2 text-xs text-muted">state {process.state}</span>
        <span className="rounded-full border border-border px-2 text-xs text-muted">cpu {process.cpu_ticks}</span>
        <span className="rounded-full border border-border px-2 text-xs text-muted">I/O {formatBytes(read + written)}</span>
      </div>
      <div className="mt-2 break-words font-mono text-xs text-muted">{process.cmd || "unknown command"}</div>
      {process.children && process.children.length > 0 ? (
        <div className="mt-3 border-l-2 border-border pl-3">
          {process.children.map((child) => (
            <ProcessRow key={child.pid} process={child} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ProcessKpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-white px-2 py-2">
      <div className="font-mono text-sm font-semibold text-foreground">{value}</div>
      <div className="mt-0.5 text-[11px] font-semibold uppercase text-muted">{label}</div>
    </div>
  );
}

function SignalTile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-white p-2.5">
      <div className="text-[11px] font-semibold uppercase text-muted">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-foreground">{value}</div>
      <div className="mt-1 truncate font-mono text-[11px] text-muted">{detail}</div>
    </div>
  );
}

function latestProgressSummary(jobs: ProcessesResponse["running_jobs"], key: "semantic_progress" | "visible_progress") {
  const job = jobs.find((item) => item[key]);
  const progress = job?.[key];
  if (!job || !progress) return "no running heartbeat";
  return `#${job.id} ${progress.phase} ${formatSeconds(progress.age_seconds ?? null)}`;
}

function flattenProcessTree(process: ProcessSample): ProcessSample[] {
  return [process, ...(process.children ?? []).flatMap((child) => flattenProcessTree(child))];
}

function totalIo(process: ProcessSample) {
  return (process.io_bytes?.read_bytes ?? 0) + (process.io_bytes?.write_bytes ?? 0);
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function MiniStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-border p-3">
      <div className="text-xs font-semibold text-muted">{label}</div>
      <div className="mt-1 min-w-0 break-words text-sm [overflow-wrap:anywhere]">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone = statusTone(status);
  const isLive = status === "running" || status === "pending";
  return (
    <span className={cn("inline-flex min-h-6 items-center gap-1.5 rounded-full border px-2 text-xs font-semibold", tone.badge)}>
      <span className={cn("h-2.5 w-2.5 rounded-full", tone.dot, isLive && "animate-live-pulse")} aria-hidden />
      {status}
    </span>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted">{text}</div>;
}

function Banner({ tone, text }: { tone: "error"; text: string }) {
  return <div className={cn("rounded-md border p-3 text-sm", tone === "error" && "border-red-300 bg-red-50 text-red-700")}>{text}</div>;
}

function RefreshButton({ onClick, compactOnMobile = false }: { onClick: () => void; compactOnMobile?: boolean }) {
  return (
    <button
      className={cn(
        "inline-flex h-8 items-center justify-center gap-2 rounded-md border border-border text-sm font-semibold text-foreground hover:bg-slate-50",
        compactOnMobile ? "w-8 px-0 sm:w-auto sm:px-3" : "px-3",
      )}
      onClick={onClick}
      type="button"
      aria-label="Refresh"
    >
      <RefreshCw className="h-4 w-4" aria-hidden />
      <span className={cn(compactOnMobile && "hidden sm:inline")}>Refresh</span>
    </button>
  );
}

export {
  ActorFilter,
  AutoupdateNotice,
  Filters,
  JobDetail,
  JobsList,
  ProductMeta,
  SectionNav,
  StatusBadge,
  SystemdUnits,
  UserMenu,
  KnowledgePage,
  KnowledgeProposals,
  buildJobQuery,
  buildKnowledgeQuery,
  changelogMarkdown,
  isKnowledgePath,
  isSystemPath,
  isRetryableStatus,
  groupSessionEvents,
  groupTranscriptEntries,
  formatRuntimeUsageSeconds,
  metricsSummaryPath,
  runtimeBucketLabel,
  selectedJobIdFromPath,
  shouldRefreshJobForSessionEvent,
};

const root = document.getElementById("root");

if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </React.StrictMode>,
  );
}
