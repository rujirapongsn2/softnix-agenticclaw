"use strict";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { body: event.data ? event.data.text() : "New message" };
  }
  const title = payload.title || "Softnix Agent";
  const options = {
    body: payload.body || "You have a new reply.",
    icon: payload.icon || "/static/Logo_Softnix.png",
    badge: payload.badge || "/static/Logo_Softnix.png",
    data: {
      url: payload.url || "/mobile",
      session_id: payload.session_id || "",
      message_id: payload.message_id || "",
      instance_id: payload.instance_id || "",
    },
    tag: payload.tag || `softnix-${payload.session_id || "reply"}`,
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification?.data?.url || "/mobile";
  event.waitUntil((async () => {
    const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clientsList) {
      if ("focus" in client) {
        await client.focus();
        if ("navigate" in client) {
          await client.navigate(targetUrl);
        }
        return;
      }
    }
    if (self.clients.openWindow) {
      await self.clients.openWindow(targetUrl);
    }
  })());
});
