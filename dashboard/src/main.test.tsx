import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  ActorFilter,
  AutoupdateNotice,
  Filters,
  JobDetail,
  JobsList,
  KnowledgePage,
  KnowledgeProposals,
  ProductMeta,
  SectionNav,
  StatusBadge,
  SystemdUnits,
  UserMenu,
  buildJobQuery,
  buildKnowledgeQuery,
  changelogMarkdown,
  formatRuntimeUsageSeconds,
  groupSessionEvents,
  groupTranscriptEntries,
  isKnowledgePath,
  isRetryableStatus,
  isSystemPath,
  metricsSummaryPath,
  runtimeBucketLabel,
  selectedJobIdFromPath,
  shouldRefreshJobForSessionEvent,
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
    const { rerender } = render(<SectionNav isDashboardRoute={true} isSystemRoute={false} isKnowledgeRoute={false} knowledgeBadgeCount={2} />);

    expect(screen.getByRole("link", { name: /Knowledge/i })).toContainElement(screen.getByLabelText("2 proposed knowledge items"));
    expect(screen.getByRole("link", { name: /Jobs/i })).toHaveClass("bg-primary");
    expect(screen.getByRole("link", { name: /System/i })).not.toHaveClass("bg-primary");

    rerender(<SectionNav isDashboardRoute={false} isSystemRoute={true} isKnowledgeRoute={false} knowledgeBadgeCount={0} />);
    expect(screen.getByRole("link", { name: /System/i })).toHaveClass("bg-primary");

    rerender(<SectionNav isDashboardRoute={false} isSystemRoute={false} isKnowledgeRoute={true} knowledgeBadgeCount={0} />);
    expect(screen.queryByLabelText(/proposed knowledge/i)).not.toBeInTheDocument();
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
  };

  it("pulses pending and running jobs, but leaves waiting approval static", () => {
    const { rerender } = render(<StatusBadge status="pending" />);
    expect(screen.getByText("pending").querySelector("span")).toHaveClass("animate-live-pulse");

    rerender(<StatusBadge status="running" />);
    expect(screen.getByText("running").querySelector("span")).toHaveClass("animate-live-pulse");

    rerender(<StatusBadge status="waiting_approval" />);
    expect(screen.getByText("waiting_approval").querySelector("span")).not.toHaveClass("animate-live-pulse");
  });

  it("keeps the jobs table header above animated status dots while scrolling", () => {
    render(
      <JobsList
        jobs={[job]}
        loading={false}
        now={Date.parse("2026-05-31T19:12:00Z")}
        onViewJob={() => undefined}
      />,
    );

    expect(screen.getByRole("columnheader", { name: "Status" }).parentElement).toHaveClass("sticky", "top-0", "z-10");
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
      },
    ];

    const { rerender } = render(<KnowledgeProposals proposals={proposals} loading={false} isAdmin={false} now={Date.parse("2026-06-04T10:02:00Z")} onApprove={onApprove} onReject={onReject} />);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    rerender(<KnowledgeProposals proposals={proposals} loading={false} isAdmin={true} now={Date.parse("2026-06-04T10:02:00Z")} onApprove={onApprove} onReject={onReject} />);
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(onApprove).toHaveBeenCalledWith("feedback-proposal-1");
    expect(onReject).not.toHaveBeenCalled();
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
