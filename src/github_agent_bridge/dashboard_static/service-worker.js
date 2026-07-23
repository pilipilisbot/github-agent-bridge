self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  const title = payload.title || "GitHub Agent Bridge";
  const status = String(payload.status || "");
  const isBlocked = status === "blocked";
  const isFailed = status === "failed";
  const jobUrl = payload.job_url || (payload.job_id ? `/jobs/${payload.job_id}` : "/");
  const githubUrl = payload.github_url || payload.followup_url || "";
  const timestamp = payload.timestamp ? Date.parse(payload.timestamp) : Date.now();
  const actions = [{ action: "open-job", title: "Open job" }];
  if (githubUrl) actions.push({ action: "open-github", title: "Open GitHub" });
  const options = {
    body: payload.body || "Bridge job finished",
    tag: payload.tag || "github-agent-bridge",
    icon: payload.icon || "/bridge-icon.svg",
    badge: "/bridge-badge.svg",
    timestamp: Number.isNaN(timestamp) ? Date.now() : timestamp,
    requireInteraction: isBlocked || isFailed,
    renotify: isBlocked || isFailed,
    vibrate: isBlocked || isFailed ? [120, 80, 120] : undefined,
    actions,
    data: {
      url: payload.url || jobUrl,
      job_url: jobUrl,
      github_url: githubUrl,
    },
  };
  event.waitUntil(
    Promise.all([
      clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
        for (const client of clientList) {
          client.postMessage({
            type: "github-agent-bridge:push",
            payload: { ...payload, title, body: options.body, url: options.data.url },
          });
        }
      }),
      self.registration.showNotification(title, options),
    ]),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const targetUrl = event.action === "open-github" ? data.github_url : event.action === "open-job" ? data.job_url : data.url || data.job_url || data.github_url || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      const target = new URL(targetUrl, self.location.origin).href;
      const sameOrigin = new URL(target).origin === self.location.origin;
      if (sameOrigin) {
        for (const client of clientList) {
          if ("focus" in client) {
            client.navigate(target);
            return client.focus();
          }
        }
      }
      return clients.openWindow(target);
    }),
  );
});
