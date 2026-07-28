import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  ActorFilter,
  AutoupdateNotice,
  Filters,
  JobDetail,
  JobDetailPage,
  JobsList,
  KnowledgePage,
  KnowledgeProposals,
  KnowledgeRules,
  McpPage,
  ProductMeta,
  SectionNav,
  StatusBadge,
  SystemdUnits,
  UserMenu,
  WebPushControl,
  buildJobQuery,
  buildKnowledgeQuery,
  changelogMarkdown,
  formatRuntimeUsageSeconds,
  groupSessionEvents,
  groupTranscriptEntries,
  isKnowledgePath,
  isMcpPath,
  isRetryableStatus,
  isSystemPath,
  metricsSummaryPath,
  runtimeBucketLabel,
  selectedJobIdFromPath,
  shouldRefreshJobForSessionEvent,
  urlBase64ToUint8Array,
} from "./main";

describe("dashboard routing and API query helpers", () => {
  it("builds trimmed job queries and preserves the requested limit", () => {
    expect(
      buildJobQuery(
        {
          status: " pending ",
          repo: " pilipilisbot/github-agent-bridge ",
          thread: "",
          action: " open_issue ",
          intent: " work_allowed ",
          actor: " ecarreras ",
        },
        24,
      ),
    ).toBe("/api/jobs?status=pending&repo=pilipilisbot%2Fgithub-agent-bridge&action=open_issue&intent=work_allowed&actor=ecarreras&limit=24");
  });

  it("builds knowledge queries and recognizes the knowledge route", () => {
    expect(buildKnowledgeQuery(" pilipilisbot/github-agent-bridge ", " proposed ", 25)).toBe("/api/knowledge?repo=pilipilisbot%2Fgithub-agent-bridge&status=proposed&limit=25");
    expect(isKnowledgePath("/knowledge")).toBe(true);
    expect(isKnowledgePath("/knowledge/")).toBe(true);
    expect(isKnowledgePath("/knowledge/extra")).toBe(false);
  });

  it("recognizes the MCP route", () => {
    expect(isMcpPath("/mcp")).toBe(true);
    expect(isMcpPath("/mcp/")).toBe(true);
    expect(isMcpPath("/mcp/tokens")).toBe(false);
  });

  it("recognizes the dedicated system route", () => {
    expect(isSystemPath("/system")).toBe(true);
    expect(isSystemPath("/system/")).toBe(true);
    expect(isSystemPath("/system/processes")).toBe(false);
  });

  it("recognizes only canonical job detail routes", () => {
    expect(selectedJobIdFromPath("/jobs/45")).toBe(45);
    expect(selectedJobIdFromPath("/jobs/45/")).toBe(45);
    expect(selectedJobIdFromPath("/jobs/not-a-number")).toBeNull();
    expect(selectedJobIdFromPath("/jobs/45/activity")).toBeNull();
  });

  it("shows a knowledge badge when proposed rules need moderation", () => {
    const onNavigate = vi.fn();
    const { rerender } = render(<SectionNav isDashboardRoute={true} isSystemRoute={false} isKnowledgeRoute={false} isMcpRoute={false} knowledgeBadgeCount={2} />);

    expect(screen.getByRole("link", { name: /Knowledge/i })).toContainElement(screen.getByLabelText("2 proposed knowledge items"));
    expect(screen.getByRole("link", { name: /Jobs/i })).toHaveClass("bg-primary");
    expect(screen.getByRole("link", { name: /System/i })).not.toHaveClass("bg-primary");
    expect(screen.getByRole("link", { name: /MCP/i })).not.toHaveClass("bg-primary");

    rerender(<SectionNav isDashboardRoute={false} isSystemRoute={true} isKnowledgeRoute={false} isMcpRoute={false} knowledgeBadgeCount={0} onNavigate={onNavigate} />);
    expect(screen.getByRole("link", { name: /System/i })).toHaveClass("bg-primary");

    rerender(<SectionNav isDashboardRoute={false} isSystemRoute={false} isKnowledgeRoute={true} isMcpRoute={false} knowledgeBadgeCount={0} />);
    expect(screen.queryByLabelText(/proposed knowledge/i)).not.toBeInTheDocument();

    rerender(<SectionNav isDashboardRoute={false} isSystemRoute={false} isKnowledgeRoute={false} isMcpRoute={true} knowledgeBadgeCount={0} />);
    expect(screen.getByRole("link", { name: /MCP/i })).toHaveClass("bg-primary");
  });

  it("uses client-side navigation for dashboard section links", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<SectionNav isDashboardRoute={false} isSystemRoute={false} isKnowledgeRoute={true} onNavigate={onNavigate} />);

    await user.click(screen.getByRole("link", { name: /Jobs/i }));

    expect(onNavigate).toHaveBeenCalledWith("/");
  });

  it("refreshes job data only for session events that can change job state", () => {
    expect(shouldRefreshJobForSessionEvent("claimed")).toBe(true);
    expect(shouldRefreshJobForSessionEvent("dispatch_finished")).toBe(true);
    expect(shouldRefreshJobForSessionEvent("done")).toBe(true);
    expect(shouldRefreshJobForSessionEvent("openclaw_stdout")).toBe(false);
    expect(shouldRefreshJobForSessionEvent("openclaw_stderr")).toBe(false);
  });

  it("limits retry actions to manually recoverable job states", () => {
    expect(isRetryableStatus("blocked")).toBe(true);
    expect(isRetryableStatus("denied")).toBe(true);
    expect(isRetryableStatus("waiting_approval")).toBe(true);
    expect(isRetryableStatus("pending")).toBe(false);
    expect(isRetryableStatus("running")).toBe(false);
    expect(isRetryableStatus("done")).toBe(false);
  });

  it("requests metrics using the browser timezone and labels runtime buckets", () => {
    expect(metricsSummaryPath("America/New_York")).toBe("/api/metrics/summary?timezone=America%2FNew_York");
    expect(runtimeBucketLabel("2026-06-02", "day")).toMatch(/Jun|2/);
    expect(runtimeBucketLabel("2026-06", "month")).toMatch(/Jun|2026/);
  });

  it("formats runtime usage as human-readable hours and minutes", () => {
    expect(formatRuntimeUsageSeconds(30)).toBe("30s");
    expect(formatRuntimeUsageSeconds(1800)).toBe("30m");
    expect(formatRuntimeUsageSeconds(5400)).toBe("1h 30m");
    expect(formatRuntimeUsageSeconds(7200)).toBe("2h");
  });

  it("decodes VAPID public keys for push manager subscriptions", () => {
    expect(Array.from(urlBase64ToUint8Array("AQIDBA"))).toEqual([1, 2, 3, 4]);
  });

  it("shows a compact disabled notification control before push is configured", () => {
    render(<WebPushControl config={{ configured: false, public_key: "", status: { enabled: false, subscriptions: [] } }} loading={false} onEnable={vi.fn()} onDisable={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Notifications unavailable" })).toBeDisabled();
  });
});

describe("MCP access page", () => {
  const admin = { login: "admin", avatar_url: "", html_url: "https://github.com/admin", is_admin: true };

  it("lets admins create and revoke MCP tokens", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue({
      token: "gab_mcp_secret",
      record: {
        id: "token-1",
        name: "local agent",
        created_at: "2026-06-23T11:00:00Z",
        last_used_at: null,
        revoked_at: null,
        expires_at: null,
      },
      detail: "mcp_token_created",
    });
    const onRevoke = vi.fn().mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <McpPage
        tokens={[
          {
            id: "token-1",
            name: "local agent",
            created_at: "2026-06-23T11:00:00Z",
            last_used_at: null,
            revoked_at: null,
            expires_at: null,
          },
        ]}
        loading={false}
        error={null}
        user={admin}
        dashboardUrl="https://bridge.example.com/ops"
        dashboardUrlSource="configured"
        now={Date.parse("2026-06-23T11:05:00Z")}
        onCreate={onCreate}
        onRevoke={onRevoke}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("Connect an agent")).toBeInTheDocument();
    expect(screen.getByText("Public dashboard URL")).toBeInTheDocument();
    expect(screen.getByText("Configured public URL")).toBeInTheDocument();
    expect(screen.getByText("https://bridge.example.com/ops/mcp")).toBeInTheDocument();
    expect(screen.getByText("https://bridge.example.com/ops/api/mcp")).toBeInTheDocument();
    expect(screen.queryByText(/Set GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL/)).not.toBeInTheDocument();
    expect(screen.getByText("Remote agents connect directly with a bearer token; no local `gab` binary is required on the agent host.")).toBeInTheDocument();
    expect(screen.getByText(/\"url\": \"https:\/\/bridge.example.com\/ops\/api\/mcp\"/)).toBeInTheDocument();
    expect(screen.getByText(/\"Authorization\": \"Bearer/)).toBeInTheDocument();
    expect(screen.queryByText("Local fallback")).not.toBeInTheDocument();
    expect(screen.queryByText(/mcp-serve/)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Token name"), "local agent");
    await user.click(screen.getByRole("button", { name: "Create token" }));

    expect(onCreate).toHaveBeenCalledWith("local agent");
    expect(await screen.findByText("gab_mcp_secret")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Revoke" })[0]);

    expect(confirm).toHaveBeenCalledWith("Revoke this MCP token?");
    expect(onRevoke).toHaveBeenCalledWith("token-1");
    confirm.mockRestore();
  });

  it("keeps MCP token management admin-only", () => {
    render(
      <McpPage
        tokens={[]}
        loading={false}
        error={null}
        user={{ login: "reader", avatar_url: "", html_url: "https://github.com/reader", is_admin: false }}
        dashboardUrl="https://bridge.example.com"
        dashboardUrlSource="configured"
        now={Date.parse("2026-06-23T11:05:00Z")}
        onCreate={vi.fn()}
        onRevoke={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("Admin access is required to manage MCP tokens.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create token" })).not.toBeInTheDocument();
  });

  it("requires a configured public URL before showing a remote MCP endpoint", () => {
    render(
      <McpPage
        tokens={[]}
        loading={false}
        error={null}
        user={admin}
        dashboardUrl="http://127.0.0.1:8765"
        dashboardUrlSource="request"
        now={Date.parse("2026-06-23T11:05:00Z")}
        onCreate={vi.fn()}
        onRevoke={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("Needs public URL")).toBeInTheDocument();
    expect(screen.getByText("Set GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL or forward X-Forwarded-* headers")).toBeInTheDocument();
    expect(screen.getByText("Public dashboard URL required before connecting remote agents")).toBeInTheDocument();
    expect(screen.queryByText("http://127.0.0.1:8765/mcp")).not.toBeInTheDocument();
    expect(screen.queryByText("http://127.0.0.1:8765/api/mcp")).not.toBeInTheDocument();
    expect(screen.getByText(/\"url\": \"https:\/\/bridge.example.com\/api\/mcp\"/)).toBeInTheDocument();
  });
});

describe("status badges", () => {
  const job = {
    id: 58,
    work_key: "pilipilisbot/github-agent-bridge#58",
    repo: "pilipilisbot/github-agent-bridge",
    thread: 58,
    status: "pending",
    action: "open_issue",
    decision: "allowed",
    intent: "work_allowed",
    subject: "El dot del badge queda per sobre del header de la taula",
    trigger_actor: "ecarreras",
    trigger_actor_avatar_url: null,
    attempts: 1,
    coalesced_count: 1,
    last_error: null,
    locked_by: null,
    created_at: "2026-05-31T19:11:06Z",
    updated_at: "2026-05-31T19:11:06Z",
    started_at: null,
    finished_at: null,
    queue_wait_seconds: null,
    runtime_seconds: null,
    github_urls: [],
    model_route: {
      configured: true,
      model: "openai/gpt-5.4-mini",
      thinking: "medium",
      summary: "model=openai/gpt-5.4-mini thinking=medium",
    },
  };

  it("pulses pending and running jobs, but leaves waiting approval static", () => {
    const { rerender } = render(<StatusBadge status="pending" />);
    expect(screen.getByText("pending").querySelector("span")).toHaveClass("animate-live-pulse");

    rerender(<StatusBadge status="running" />);
    expect(screen.getByText("running").querySelector("span")).toHaveClass("animate-live-pulse");

    rerender(<StatusBadge status="waiting_approval" />);
    expect(screen.getByText("waiting_approval").querySelector("span")).not.toHaveClass("animate-live-pulse");
  });

  it("keeps the jobs table header above animated status dots while hiding model routing from the list", () => {
    render(
      <JobsList
        jobs={[job]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    expect(screen.getByRole("columnheader", { name: "Status" }).parentElement).toHaveClass("sticky", "top-0", "z-10");
    expect(screen.getByRole("columnheader", { name: "Job" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Model" })).not.toBeInTheDocument();
    expect(screen.queryByText("openai/gpt-5.4-mini · medium")).not.toBeInTheDocument();
    expect(screen.getAllByText("El dot del badge queda per sobre del header de la taula").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByLabelText("Work allowed: work_allowed").length).toBeGreaterThanOrEqual(1);
  });

  it("keeps long desktop job titles in the flexible job column", () => {
    const longSubject = "Improve the desktop dashboard table so a very long GitHub issue title stays readable without pushing status, actor, timing, updated, or action controls off the row";
    render(
      <JobsList
        jobs={[
          {
            ...job,
            id: 154,
            thread: 154,
            subject: longSubject,
            action: "open_issue_with_an_unusually_long_action_name",
          },
        ]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    expect(screen.getByRole("columnheader", { name: "Job" })).toHaveClass("w-[42%]");
    expect(screen.getAllByText(longSubject).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTitle(longSubject)).toHaveClass("line-clamp-2", "[overflow-wrap:anywhere]");
    expect(screen.getByRole("columnheader", { name: "Timing" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Attempts" })).not.toBeInTheDocument();
  });

  it("shows review mode with an icon in the jobs list", () => {
    render(
      <JobsList
        jobs={[{ ...job, intent: "review_only", action_mode: "review_only" }]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    expect(screen.getAllByLabelText("Review only: review_only").length).toBeGreaterThanOrEqual(1);
  });

  it("uses the action mode for the visual state when it differs from the stored intent", () => {
    render(
      <JobDetail
        job={{ ...job, intent: "work_allowed", action_mode: "review_only", worklog: [] }}
        session={undefined}
        sessionEvents={[]}
        transcript={[]}
        now={Date.parse("2026-06-08T16:40:00Z")}
      />,
    );

    expect(screen.getByLabelText("Review only: review_only")).toBeInTheDocument();
  });

  it("shows GitHub links at the top of the job detail", () => {
    const githubUrl = "https://github.com/pilipilisbot/github-agent-bridge/issues/114#issuecomment-4651153034";
    const { container } = render(
      <JobDetail
        job={{ ...job, github_urls: [githubUrl], worklog: [] }}
        session={undefined}
        sessionEvents={[]}
        transcript={[]}
        now={Date.parse("2026-06-08T16:40:00Z")}
      />,
    );

    expect(screen.getByRole("link", { name: githubUrl })).toHaveAttribute("href", githubUrl);
    expect(screen.getByLabelText("Sticky job header")).toHaveClass("sticky", "top-0", "z-20");
    const content = container.textContent ?? "";
    expect(content.indexOf("GitHub links")).toBeLessThan(content.indexOf("Queue wait"));
    expect(content.indexOf("GitHub links")).toBeLessThan(content.indexOf("Timeline"));
    expect(screen.getByLabelText("Work allowed: fix_allowed")).toBeInTheDocument();
    expect(screen.getByText("Reasoning")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("shows intent classifier decisions in the job detail", () => {
    render(
      <JobDetail
        job={{
          ...job,
          action_mode: "fix_allowed",
          intent_classifier: {
            enabled: true,
            parser: { action: "reply_comment", work_intent: "review_only" },
            llm: {
              addressed_to_agent: true,
              action: "reply_comment",
              work_intent: "work_allowed",
              write_permission: "state_change_allowed",
              scope: "Update the PR tests.",
              main_request: "Please fix the failing tests.",
              confidence: 0.91,
              reason: "The request asks the configured agent to modify repository state.",
              applied: true,
            },
          },
          worklog: [],
        }}
        session={undefined}
        sessionEvents={[]}
        transcript={[]}
        now={Date.parse("2026-06-08T16:40:00Z")}
      />,
    );

    expect(screen.getByText("Action mode")).toBeInTheDocument();
    expect(screen.getByText("fix_allowed")).toBeInTheDocument();
    expect(screen.getByText("state_change_allowed")).toBeInTheDocument();
    expect(screen.getByText("reply_comment / review_only")).toBeInTheDocument();
    expect(screen.getByText("reply_comment / work_allowed")).toBeInTheDocument();
    expect(screen.getByText("Update the PR tests.")).toBeInTheDocument();
    expect(screen.getByText("Please fix the failing tests.")).toBeInTheDocument();
    expect(screen.getByText("91%")).toBeInTheDocument();
  });

  it("returns from job detail through client-side dashboard navigation", async () => {
    const user = userEvent.setup();
    const onBackToDashboard = vi.fn();
    render(
      <JobDetailPage
        jobId={58}
        detail={<div>Job detail content</div>}
        selectedJob={job}
        user={{ login: "reader", avatar_url: "", html_url: "https://github.com/reader", is_admin: false }}
        onBackToDashboard={onBackToDashboard}
        onRetry={vi.fn()}
        onDismiss={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("link", { name: /Dashboard/i }));

    expect(onBackToDashboard).toHaveBeenCalledTimes(1);
  });

  it("loads the next jobs batch when the list sentinel enters view", async () => {
    const onLoadMore = vi.fn();
    class ImmediateIntersectionObserver {
      constructor(private callback: IntersectionObserverCallback) {}
      observe(target: Element) {
        this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
      }
      disconnect() {}
      unobserve() {}
      takeRecords() {
        return [];
      }
    }
    vi.stubGlobal("IntersectionObserver", ImmediateIntersectionObserver);

    render(
      <JobsList
        jobs={[job]}
        loading={false}
        hasMore
        loadingMore={false}
        onLoadMore={onLoadMore}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    await waitFor(() => expect(onLoadMore).toHaveBeenCalledTimes(1));
    vi.unstubAllGlobals();
  });

  it("uses an explicit load more action for the mobile jobs list", async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();
    class IdleIntersectionObserver {
      observe() {}
      disconnect() {}
      unobserve() {}
      takeRecords() {
        return [];
      }
    }
    vi.stubGlobal("IntersectionObserver", IdleIntersectionObserver);

    render(
      <JobsList
        jobs={[job]}
        loading={false}
        hasMore
        loadingMore={false}
        onLoadMore={onLoadMore}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Load more jobs" }));

    expect(onLoadMore).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("does not request another jobs batch while one is already loading", () => {
    const onLoadMore = vi.fn();
    render(
      <JobsList
        jobs={[job]}
        loading={false}
        hasMore
        loadingMore
        onLoadMore={onLoadMore}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    expect(screen.getAllByText("Loading more jobs...").length).toBeGreaterThan(0);
    expect(onLoadMore).not.toHaveBeenCalled();
  });

  it("lets admins retry recoverable jobs from the jobs list without opening the detail page", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn().mockResolvedValue(undefined);
    const onViewJob = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <JobsList
        jobs={[
          {
            id: 58,
            work_key: "pilipilisbot/github-agent-bridge#58",
            repo: "pilipilisbot/github-agent-bridge",
            thread: 58,
            status: "blocked",
            action: "reply_comment",
            decision: "allowed",
            intent: "work_allowed",
            subject: "Needs a guarded retry from the list",
            trigger_actor: "ecarreras",
            trigger_actor_avatar_url: null,
            attempts: 1,
            coalesced_count: 1,
            last_error: null,
            locked_by: null,
            created_at: "2026-05-31T19:11:06Z",
            updated_at: "2026-05-31T19:11:06Z",
            started_at: null,
            finished_at: null,
            queue_wait_seconds: null,
            runtime_seconds: null,
            github_urls: [],
          },
        ]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={onViewJob}
        onRetry={onRetry}
        user={{ login: "admin", avatar_url: "", html_url: "https://github.com/admin", is_admin: true }}
      />,
    );

    await user.click(screen.getAllByRole("button", { name: "Retry job #58" })[0]);

    expect(confirm).toHaveBeenCalledWith("Retry job #58?");
    expect(onRetry).toHaveBeenCalledWith(58);
    expect(onViewJob).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("lets admins dismiss recoverable jobs from the jobs list without opening the detail page", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn().mockResolvedValue(undefined);
    const onViewJob = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <JobsList
        jobs={[
          {
            id: 58,
            work_key: "pilipilisbot/github-agent-bridge#58",
            repo: "pilipilisbot/github-agent-bridge",
            thread: 58,
            status: "blocked",
            action: "reply_comment",
            decision: "allowed",
            intent: "work_allowed",
            subject: "Needs an acknowledgement from the list",
            trigger_actor: "ecarreras",
            trigger_actor_avatar_url: null,
            attempts: 1,
            coalesced_count: 1,
            last_error: null,
            locked_by: null,
            created_at: "2026-05-31T19:11:06Z",
            updated_at: "2026-05-31T19:11:06Z",
            started_at: null,
            finished_at: null,
            queue_wait_seconds: null,
            runtime_seconds: null,
            github_urls: [],
          },
        ]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={onViewJob}
        onDismiss={onDismiss}
        user={{ login: "admin", avatar_url: "", html_url: "https://github.com/admin", is_admin: true }}
      />,
    );

    await user.click(screen.getAllByRole("button", { name: "Dismiss job #58" })[0]);

    expect(confirm).toHaveBeenCalledWith("Dismiss job #58?");
    expect(onDismiss).toHaveBeenCalledWith(58);
    expect(onViewJob).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("hides list retry actions from read-only users and non-retryable jobs", () => {
    render(
      <JobsList
        jobs={[
          {
            id: 58,
            work_key: "pilipilisbot/github-agent-bridge#58",
            repo: "pilipilisbot/github-agent-bridge",
            thread: 58,
            status: "pending",
            action: "reply_comment",
            decision: "allowed",
            intent: "work_allowed",
            subject: "Pending jobs are not manually retried",
            trigger_actor: "ecarreras",
            trigger_actor_avatar_url: null,
            attempts: 1,
            coalesced_count: 1,
            last_error: null,
            locked_by: null,
            created_at: "2026-05-31T19:11:06Z",
            updated_at: "2026-05-31T19:11:06Z",
            started_at: null,
            finished_at: null,
            queue_wait_seconds: null,
            runtime_seconds: null,
            github_urls: [],
          },
        ]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
        onRetry={vi.fn()}
        onDismiss={vi.fn()}
        user={{ login: "reader", avatar_url: "", html_url: "https://github.com/reader", is_admin: false }}
      />,
    );

    expect(screen.queryByRole("button", { name: "Retry job #58" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss job #58" })).not.toBeInTheDocument();
  });
});

describe("system page", () => {
  const systemdUnit = {
    role: "executor",
    kind: "service",
    unit: "github-agent-bridge.service",
    load_state: "loaded",
    active_state: "active",
    sub_state: "running",
    result: "success",
    exec_main_status: "0",
    main_pid: 123,
    uptime_seconds: 90,
    active_enter_timestamp: "Sat 2026-06-06 09:00:00 UTC",
    inactive_enter_timestamp: "",
    next_elapse: "",
    last_trigger: "",
    unit_file_state: "enabled",
    ok: true,
  };

  it("renders systemd service and timer status cards", () => {
    render(
      <SystemdUnits
        loading={false}
        data={{
          available: true,
          errors: [],
          units: [
            systemdUnit,
            {
              role: "reader",
              kind: "timer",
              unit: "github-agent-bridge-reader.timer",
              load_state: "loaded",
              active_state: "active",
              sub_state: "waiting",
              result: "success",
              exec_main_status: null,
              main_pid: null,
              uptime_seconds: null,
              active_enter_timestamp: "",
              inactive_enter_timestamp: "",
              next_elapse: "Sat 2026-06-06 09:15:00 UTC",
              last_trigger: "Sat 2026-06-06 09:10:00 UTC",
              unit_file_state: "enabled",
              ok: true,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("github-agent-bridge.service")).toBeInTheDocument();
    expect(screen.getByText("github-agent-bridge-reader.timer")).toBeInTheDocument();
    expect(screen.getByText("1m 30s")).toBeInTheDocument();
    expect(screen.getByText(/next Sat 2026-06-06/)).toBeInTheDocument();
  });

  it("streams a unit journal when its process row is expanded", async () => {
    const listeners = new Map<string, (message: MessageEvent) => void>();
    const close = vi.fn();
    const EventSourceMock = vi.fn(function (this: EventSource) {
      this.addEventListener = ((event: string, callback: (message: MessageEvent) => void) => {
        listeners.set(event, callback);
      }) as EventSource["addEventListener"];
      this.close = close;
      this.onerror = null;
    });
    vi.stubGlobal("EventSource", EventSourceMock);

    render(
      <SystemdUnits
        loading={false}
        data={{
          available: true,
          errors: [],
          units: [systemdUnit],
        }}
      />,
    );

    expect(EventSourceMock).not.toHaveBeenCalled();

    const rowSummary = screen.getByText("github-agent-bridge.service").closest("summary");
    expect(rowSummary).not.toBeNull();
    fireEvent.click(rowSummary!);

    await waitFor(() => expect(EventSourceMock).toHaveBeenCalledWith("/api/systemd/journal/stream?unit=github-agent-bridge.service"));
    act(() => {
      listeners.get("journal_line")?.(new MessageEvent("journal_line", { data: JSON.stringify({ unit: "github-agent-bridge.service", line: "started worker" }) }));
    });

    expect(screen.getByText("started worker")).toBeInTheDocument();
    expect(screen.getByText("1 lines streamed")).toBeInTheDocument();

    fireEvent.click(rowSummary!);

    await waitFor(() => expect(close).toHaveBeenCalled());
    vi.unstubAllGlobals();
  });
});

describe("product metadata", () => {
  it("shows the bridge version and upstream repository link", () => {
    render(<ProductMeta about={{ service: "github-agent-bridge-dashboard", version: "0.18.7", repository_url: "https://github.com/pilipilisbot/github-agent-bridge" }} />);

    expect(screen.getByText("Operational dashboard")).toBeInTheDocument();
    expect(screen.getByText("v0.18.7")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /github/i })).toHaveAttribute("href", "https://github.com/pilipilisbot/github-agent-bridge");
  });
});

describe("autoupdate notice", () => {
  const updateState = {
    updated_at: "2026-06-04T16:30:00Z",
    installed_tag: "v0.27.0",
    target: {
      tag_name: "v0.28.0",
      url: "https://github.com/pilipilisbot/github-agent-bridge/releases/tag/v0.28.0",
      body: "## Changes\n- Add **safe** autoupdate planning\n- Improve [dashboard release visibility](https://github.com/pilipilisbot/github-agent-bridge/releases/tag/v0.28.0)",
    },
    decision: "stage_defer_executor_reload",
    executor_reload_pending: true,
    dashboard_applied_at: "2026-06-04T16:31:00Z",
    blocked_reason: "active_jobs_block_executor_reload",
    queue: { active_counts: { pending: 1 }, active_total: 1 },
    classification: { risk: "executor_or_queue", migration_files: [], risky_files: ["src/github_agent_bridge/queue.py"] },
    warnings: [],
  };

  it("shows release impact only to admins", () => {
    const { rerender } = render(<AutoupdateNotice state={updateState} isAdmin={false} />);
    expect(screen.queryByLabelText("Update available")).not.toBeInTheDocument();

    rerender(<AutoupdateNotice state={updateState} isAdmin={true} />);

    expect(screen.getByLabelText("Update available")).toBeInTheDocument();
    expect(screen.getByText("v0.28.0")).toBeInTheDocument();
    expect(screen.getByText("Dashboard reload can be staged; executor reload waits for the queue")).toBeInTheDocument();
    expect(screen.getByText("executor or queue")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Changes" })).toBeInTheDocument();
    expect(screen.getByText("safe")).toHaveClass("font-semibold");
    expect(screen.getByRole("link", { name: "dashboard release visibility" })).toHaveAttribute("href", "https://github.com/pilipilisbot/github-agent-bridge/releases/tag/v0.28.0");
    expect(screen.getByRole("link", { name: /^release$/i })).toHaveAttribute("href", "https://github.com/pilipilisbot/github-agent-bridge/releases/tag/v0.28.0");
  });

  it("offers manual admin actions for recorded autoupdate plans", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    const onApply = vi.fn();
    const onCompletePending = vi.fn();
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(<AutoupdateNotice state={updateState} isAdmin={true} onRefresh={onRefresh} onApply={onApply} onCompletePending={onCompletePending} />);

    await user.click(screen.getByRole("button", { name: /check now/i }));
    await user.click(screen.getByRole("button", { name: /apply update/i }));
    await user.click(screen.getByRole("button", { name: /complete reload/i }));

    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onCompletePending).toHaveBeenCalledTimes(1);
  });

  it("does not show apply update for migration-blocked plans", () => {
    render(
      <AutoupdateNotice
        state={{ ...updateState, classification: { ...updateState.classification, migration_files: ["src/github_agent_bridge/sql/2.sql"] } }}
        isAdmin={true}
        onApply={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /apply update/i })).not.toBeInTheDocument();
  });

  it("does not offer completion before the update has been applied", () => {
    render(<AutoupdateNotice state={{ ...updateState, dashboard_applied_at: undefined }} isAdmin={true} onCompletePending={vi.fn()} />);

    expect(screen.getByText("executor reload pending")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /complete reload/i })).not.toBeInTheDocument();
  });

  it("keeps full changelog markdown for rendering", () => {
    expect(changelogMarkdown("  # v1\n\n- First\n* Second\nplain\n- Fourth\n- Fifth  ")).toBe("# v1\n\n- First\n* Second\nplain\n- Fourth\n- Fifth");
  });
});

describe("user menu", () => {
  it("shows admin and read-only modes beside the signed-in user", () => {
    const { rerender } = render(<UserMenu user={{ login: "alice", avatar_url: "", html_url: "https://github.com/alice", is_admin: true }} loading={false} />);
    expect(screen.getByText("Signed in · admin")).toBeInTheDocument();

    rerender(<UserMenu user={{ login: "bob", avatar_url: "", html_url: "https://github.com/bob", is_admin: false }} loading={false} />);
    expect(screen.getByText("Signed in · read-only")).toBeInTheDocument();
  });
});

describe("actor filter", () => {
  it("filters actors, selects a suggestion, and clears the selection", async () => {
    const user = userEvent.setup();
    let value = "";
    const options = [
      { login: "ecarreras", avatar_url: "https://example.com/ecarreras.png", job_count: 7, last_seen: "2026-05-25T12:00:00Z" },
      { login: "octocat", avatar_url: null, job_count: 2, last_seen: null },
    ];
    const onChange = (actor: string) => {
      value = actor;
      rerender(<ActorFilter value={value} options={options} onChange={onChange} />);
    };
    const { rerender } = render(<ActorFilter value={value} options={options} onChange={onChange} />);

    await user.type(screen.getByPlaceholderText("@login"), "eca");
    expect(screen.getByText("@ecarreras")).toBeInTheDocument();
    expect(screen.queryByText("@octocat")).not.toBeInTheDocument();

    await user.click(screen.getByText("@ecarreras"));
    expect(screen.getByPlaceholderText("@login")).toHaveValue("ecarreras");

    fireEvent.click(screen.getByLabelText("Clear actor filter"));
    expect(screen.getByPlaceholderText("@login")).toHaveValue("");
  });
});

describe("job filters", () => {
  it("shows applied filters while collapsed and clears them without expanding", async () => {
    const user = userEvent.setup();
    let filters = {
      status: "pending",
      repo: "pilipilisbot/github-agent-bridge",
      thread: "164",
      action: "open_issue",
      intent: "work_allowed",
      actor: "ecarreras",
    };
    const onChange = vi.fn((nextFilters: typeof filters) => {
      filters = nextFilters;
      rerender(<Filters filters={filters} actorOptions={[]} onChange={onChange} />);
    });
    const { rerender } = render(<Filters filters={filters} actorOptions={[]} onChange={onChange} />);

    const appliedFilters = within(screen.getByLabelText("Applied filters"));
    expect(appliedFilters.getByText("Status")).toBeInTheDocument();
    expect(appliedFilters.getByText("pending")).toBeInTheDocument();
    expect(appliedFilters.getByText("Repo")).toBeInTheDocument();
    expect(appliedFilters.getByText("pilipilisbot/github-agent-bridge")).toBeInTheDocument();
    expect(appliedFilters.getByText("@ecarreras")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(onChange).toHaveBeenLastCalledWith({ status: "", repo: "", thread: "", action: "", intent: "", actor: "" });
    expect(screen.queryByLabelText("Applied filters")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("clears all applied filter fields at once", async () => {
    const user = userEvent.setup();
    let filters = {
      status: "pending",
      repo: "pilipilisbot/github-agent-bridge",
      thread: "82",
      action: "open_issue",
      intent: "work_allowed",
      actor: "ecarreras",
    };
    const onChange = vi.fn((nextFilters: typeof filters) => {
      filters = nextFilters;
      rerender(<Filters filters={filters} actorOptions={[]} onChange={onChange} />);
    });
    const { rerender } = render(<Filters filters={filters} actorOptions={[]} onChange={onChange} />);

    expect(screen.getByLabelText("Repository")).toHaveValue("pilipilisbot/github-agent-bridge");
    expect(screen.getByLabelText("Thread")).toHaveValue("82");
    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(onChange).toHaveBeenLastCalledWith({ status: "", repo: "", thread: "", action: "", intent: "", actor: "" });
    expect(screen.getByLabelText("Status")).toHaveValue("");
    expect(screen.getByLabelText("Repository")).toHaveValue("");
    expect(screen.getByLabelText("Thread")).toHaveValue("");
    expect(screen.getByLabelText("Action")).toHaveValue("");
    expect(screen.getByPlaceholderText("@login")).toHaveValue("");
    expect(screen.getByLabelText("Intent")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled();
  });
});

describe("knowledge proposals", () => {
  it("keeps knowledge records separated behind tabs", async () => {
    const user = userEvent.setup();
    render(
      <KnowledgePage
        data={{
          repositories: ["pilipilisbot/github-agent-bridge"],
          summary: { proposed: 1, approved: 0, rules: 1, events: 1 },
          proposals: [
            {
              id: "feedback-proposal-1",
              event_id: "event-1",
              created_at: "2026-06-04T10:00:00Z",
              updated_at: "2026-06-04T10:01:00Z",
              status: "proposed",
              scope: "repo:pilipilisbot/github-agent-bridge",
              type: "operating_rule",
              confidence: 0.72,
              rule: "Keep knowledge moderation auditable.",
              reason: "A reusable process correction.",
              model: "gpt-test",
              error: null,
              source_event: null,
            },
          ],
          rules: [
            {
              id: "rule-1",
              scope: "repo:pilipilisbot/github-agent-bridge",
              type: "style_preference",
              rule: "Keep rule rows compact.",
              confidence: 0.82,
              observations: 2,
              source_events: ["event-1"],
              created_at: "2026-06-04T10:00:00Z",
              last_seen: "2026-06-04T10:01:00Z",
              source_event_details: [
                {
                  id: "event-1",
                  occurred_at: "2026-06-04T10:00:00Z",
                  captured_at: "2026-06-04T10:01:00Z",
                  source: "github",
                  scope: "repo:pilipilisbot/github-agent-bridge",
                  actor: "ecarreras",
                  trigger_actor: "ecarreras",
                  trigger_actor_avatar_url: "https://avatars.githubusercontent.com/u/294235?v=4",
                  github_urls: ["https://github.com/pilipilisbot/github-agent-bridge/issues/73#issuecomment-1"],
                  source_url: "https://github.com/pilipilisbot/github-agent-bridge/issues/73#issuecomment-1",
                  source_job_id: 510,
                  source_table: "job",
                  github_context: { urls: ["https://github.com/pilipilisbot/github-agent-bridge/issues/73#issuecomment-1"] },
                  comment: "Prefer tabs for knowledge.",
                  context: { issue: 73 },
                  classification: "style_preference",
                  confidence: 0.84,
                  memorable: true,
                },
              ],
            },
          ],
          events: [
            {
              id: "event-1",
              occurred_at: "2026-06-04T10:00:00Z",
              captured_at: "2026-06-04T10:01:00Z",
              source: "github",
              scope: "repo:pilipilisbot/github-agent-bridge",
              actor: "ecarreras",
              trigger_actor: "ecarreras",
              trigger_actor_avatar_url: "https://avatars.githubusercontent.com/u/294235?v=4",
              github_urls: ["https://github.com/pilipilisbot/github-agent-bridge/issues/73#issuecomment-1"],
              source_url: "https://github.com/pilipilisbot/github-agent-bridge/issues/73#issuecomment-1",
              source_job_id: 510,
              source_table: "job",
              github_context: { urls: ["https://github.com/pilipilisbot/github-agent-bridge/issues/73#issuecomment-1"] },
              comment: "Prefer tabs for knowledge.",
              context: { issue: 73 },
              classification: "style_preference",
              confidence: 0.84,
              memorable: true,
            },
          ],
        }}
        loading={false}
        error={null}
        repo=""
        status="proposed"
        user={{ login: "admin", avatar_url: "", html_url: "https://github.com/admin", is_admin: true }}
        now={Date.parse("2026-06-04T10:02:00Z")}
        onRepoChange={vi.fn()}
        onStatusChange={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onUpdateRuleScope={vi.fn()}
        onDeleteRule={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.queryByRole("link", { name: /^Dashboard$/i })).not.toBeInTheDocument();
    expect(screen.getByText("Keep knowledge moderation auditable.")).toBeInTheDocument();
    expect(screen.queryByText("Keep rule rows compact.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /rules \(1\)/i }));
    expect(screen.getByText("Keep rule rows compact.")).toBeInTheDocument();
    expect(screen.queryByText("Keep knowledge moderation auditable.")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Proposal status")).not.toBeInTheDocument();
    expect(screen.getByText("@ecarreras")).toBeInTheDocument();
    expect(screen.getByText("Job #510")).toBeInTheDocument();
    expect(screen.getByText("pilipilisbot/github-agent-bridge/issues/73#issuecomment-1")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /events \(1\)/i }));
    expect(screen.getByText("Prefer tabs for knowledge.")).toBeInTheDocument();
    expect(screen.getByText("@ecarreras")).toBeInTheDocument();
    expect(screen.getByText("Job #510")).toBeInTheDocument();
    expect(screen.getByText("pilipilisbot/github-agent-bridge/issues/73#issuecomment-1")).toBeInTheDocument();
  });

  it("lets manageable curated rules edit scope only after entering edit mode", async () => {
    const user = userEvent.setup();
    const onUpdateRuleScope = vi.fn().mockResolvedValue(undefined);
    const rules = [
      {
        id: "rule-1",
        scope: "repo:pilipilisbot/github-agent-bridge",
        type: "style_preference",
        rule: "Keep rule rows compact.",
        confidence: 0.82,
        observations: 2,
        source_events: [],
        created_at: "2026-06-04T10:00:00Z",
        last_seen: "2026-06-04T10:01:00Z",
        source_event_details: [],
        can_manage: false,
      },
    ];

    const { rerender } = render(
      <KnowledgeRules
        rules={rules}
        loading={false}
        now={Date.parse("2026-06-04T10:02:00Z")}
        onUpdateRuleScope={onUpdateRuleScope}
        onDeleteRule={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText("Scope")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();

    rerender(
      <KnowledgeRules
        rules={[{ ...rules[0], can_manage: true }]}
        loading={false}
        now={Date.parse("2026-06-04T10:02:00Z")}
        onUpdateRuleScope={onUpdateRuleScope}
        onDeleteRule={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText("Scope")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.selectOptions(screen.getByLabelText("Scope"), "global");
    await user.click(screen.getByRole("button", { name: /save/i }));

    expect(onUpdateRuleScope).toHaveBeenCalledWith("rule-1", "global");
  });

  it("cancels curated rule scope edits without saving", async () => {
    const user = userEvent.setup();
    const onUpdateRuleScope = vi.fn().mockResolvedValue(undefined);
    render(
      <KnowledgeRules
        rules={[
          {
            id: "rule-1",
            scope: "repo:pilipilisbot/github-agent-bridge",
            type: "style_preference",
            rule: "Keep rule rows compact.",
            confidence: 0.82,
            observations: 2,
            source_events: [],
            created_at: "2026-06-04T10:00:00Z",
            last_seen: "2026-06-04T10:01:00Z",
            source_event_details: [],
            can_manage: true,
          },
        ]}
        loading={false}
        now={Date.parse("2026-06-04T10:02:00Z")}
        onUpdateRuleScope={onUpdateRuleScope}
        onDeleteRule={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.selectOptions(screen.getByLabelText("Scope"), "global");
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByLabelText("Scope")).not.toBeInTheDocument();
    expect(onUpdateRuleScope).not.toHaveBeenCalled();
  });

  it("shows moderation actions only to admins for proposed rules", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    const onReject = vi.fn().mockResolvedValue(undefined);
    const proposals = [
      {
        id: "feedback-proposal-1",
        event_id: "event-1",
        created_at: "2026-06-04T10:00:00Z",
        updated_at: "2026-06-04T10:01:00Z",
        status: "proposed",
        scope: "repo:pilipilisbot/github-agent-bridge",
        type: "operating_rule",
        confidence: 0.72,
        rule: "Keep knowledge moderation auditable.",
        reason: "A reusable process correction.",
        model: "gpt-test",
        error: null,
        source_event: null,
      },
    ];

    const { rerender } = render(<KnowledgeProposals proposals={proposals} loading={false} isAdmin={false} now={Date.parse("2026-06-04T10:02:00Z")} onApprove={onApprove} onReject={onReject} />);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    rerender(<KnowledgeProposals proposals={proposals} loading={false} isAdmin={true} now={Date.parse("2026-06-04T10:02:00Z")} onApprove={onApprove} onReject={onReject} />);
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(onApprove).toHaveBeenCalledWith("feedback-proposal-1");
    expect(onReject).not.toHaveBeenCalled();
  });

  it("shows proposal source actor and links", () => {
    const proposals = [
      {
        id: "feedback-proposal-1",
        event_id: "event-1",
        created_at: "2026-06-04T10:00:00Z",
        updated_at: "2026-06-04T10:01:00Z",
        status: "proposed",
        scope: "repo:pilipilisbot/github-agent-bridge",
        type: "operating_rule",
        confidence: 0.72,
        rule: "Keep knowledge moderation auditable.",
        reason: "A reusable process correction.",
        model: "gpt-test",
        error: null,
        source_event: {
          id: "event-1",
          occurred_at: "2026-06-04T10:00:00Z",
          captured_at: "2026-06-04T10:01:00Z",
          source: "github",
          scope: "repo:pilipilisbot/github-agent-bridge",
          actor: "copilot-pull-request-reviewer[bot]",
          trigger_actor: "copilot-pull-request-reviewer[bot]",
          trigger_actor_avatar_url: "",
          github_urls: ["https://github.com/pilipilisbot/github-agent-bridge/pull/117#pullrequestreview-1"],
          source_url: "https://github.com/pilipilisbot/github-agent-bridge/pull/117#pullrequestreview-1",
          source_job_id: 510,
          source_table: "job",
          github_context: { urls: ["https://github.com/pilipilisbot/github-agent-bridge/pull/117#pullrequestreview-1"] },
          comment: "Preserve backward compatibility.",
          context: {},
          classification: "technical_criterion",
          confidence: 0.74,
          memorable: false,
        },
      },
    ];

    render(<KnowledgeProposals proposals={proposals} loading={false} isAdmin={false} now={Date.parse("2026-06-04T10:02:00Z")} onApprove={vi.fn()} onReject={vi.fn()} />);

    expect(screen.getByText("@copilot-pull-request-reviewer[bot]")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Job #510/i })).toHaveAttribute("href", "/jobs/510");
    expect(screen.getByRole("link", { name: /github.com\/pilipilisbot\/github-agent-bridge/i })).toHaveAttribute("href", "https://github.com/pilipilisbot/github-agent-bridge/pull/117#pullrequestreview-1");
  });
});

describe("log grouping", () => {
  it("collapses consecutive OpenClaw CLI events while preserving boundaries", () => {
    const grouped = groupSessionEvents([
      { id: 1, ts: "2026-05-25T12:00:00Z", job_id: 45, work_key: "repo#45", session_id: "s1", event_type: "openclaw_stdout", summary: "stdout", detail: "first line" },
      { id: 2, ts: "2026-05-25T12:00:01Z", job_id: 45, work_key: "repo#45", session_id: "s1", event_type: "openclaw_stdout", summary: "stdout", detail: "second line" },
      { id: 3, ts: "2026-05-25T12:00:02Z", job_id: 45, work_key: "repo#45", session_id: "s1", event_type: "agent_message", summary: "done", detail: null },
    ]);

    expect(grouped).toHaveLength(2);
    expect(grouped[0]).toMatchObject({ count: 2, summary: "stdout (2): first line" });
    expect(grouped[0].detail).toBe("first line\nsecond line");
    expect(grouped[1]).toMatchObject({ count: 1, summary: "done" });
  });

  it("collapses consecutive transcript CLI entries", () => {
    const grouped = groupTranscriptEntries([
      { timestamp: "2026-05-25T12:00:00Z", role: "assistant", kind: "openclaw_stderr", title: "stderr", text: "warning" },
      { timestamp: "2026-05-25T12:00:01Z", role: "assistant", kind: "openclaw_stderr", title: "stderr", text: "details" },
      { timestamp: "2026-05-25T12:00:02Z", role: "assistant", kind: "message", title: "message", text: "finished" },
    ]);

    expect(grouped).toHaveLength(2);
    expect(grouped[0]).toMatchObject({ count: 2, summary: "assistant · openclaw_stderr (2): warning" });
    expect(grouped[0].text).toBe("warning\ndetails");
  });
});
