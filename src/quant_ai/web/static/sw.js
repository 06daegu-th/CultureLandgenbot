/* 서비스 워커: 앱처럼 설치 · 화면 틀 오프라인 캐시 · 웹 푸시 알림 */
"use strict";
const SHELL = "qa-shell-v32-1";
const FILES = ["/", "/index.html", "/style.css", "/calm.css", "/toss.css", "/icons.js", "/words.js", "/pro.js", "/live.js", "/verify.js", "/desk.js", "/truth.js", "/allin.js", "/os.js", "/hub.js", "/board.js", "/easy.js", "/goal.js", "/toss.js", "/toss2.js", "/app.js", "/vendor/lightweight-charts.standalone.production.js", "/icon-192.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
// 데이터(API)는 항상 네트워크 (오래된 시세·판단을 보여주면 안 된다). 화면 틀은 네트워크 우선, 실패하면 캐시.
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/reports/")) return;
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    if (r.ok) caches.open(SHELL).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match("/index.html"))));
});
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Quant AI", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Quant AI", {
    body: d.body || "", icon: "/icon-192.png", badge: "/icon-192.png", tag: d.tag || undefined,
    data: { link: d.link || "#control" }, renotify: false,
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = "/" + (e.notification.data?.link || "#control");
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if ("focus" in c) { c.navigate(url).catch(() => {}); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
