// The site's service worker (issue #239): shows Web Push notifications and handles taps on them.
// Served from the site root by `apps.core.views.push.service_worker` (`/sw.js`), so its scope is
// the whole site; registered by core/js/push.js when the user turns notifications on.
//
// The payload is the JSON `apps.core.services.push.build_payload` makes: `title`, `body`, `icon`,
// `badge`, an optional `tag` and `actions`, and `data` (`url`, `push_id`, and for a notification
// with buttons `action_url` and `action_token`).
//
// - push: always shows a notification. Browsers require one for every push (`userVisibleOnly`),
//   so even an unreadable payload shows the site's name rather than nothing.
// - notificationclick: a tap on a button posts the answer to `data.action_url` with the signed
//   token and does not open the site; a tap on the notification itself focuses a tab already on
//   `data.url`, or opens one.
'use strict';

self.addEventListener('install', () => {
  // A new version takes over at once: it only shows notifications, so there is no page state to
  // keep consistent with the old one.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = {};
  }
  const title = typeof payload.title === 'string' && payload.title ? payload.title : 'ZipTrigo';
  const options = {
    body: typeof payload.body === 'string' ? payload.body : '',
    data: payload.data && typeof payload.data === 'object' ? payload.data : {},
  };
  if (payload.icon) options.icon = payload.icon;
  if (payload.badge) options.badge = payload.badge;
  if (payload.tag) {
    options.tag = payload.tag;
    // A notification that replaces an earlier one with the same tag still alerts the user.
    options.renotify = true;
  }
  if (Array.isArray(payload.actions) && payload.actions.length) {
    options.actions = payload.actions.map((a) => ({ action: a.action, title: a.title }));
  }
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  const notification = event.notification;
  const data = notification.data || {};
  notification.close();
  if (event.action) {
    event.waitUntil(sendAnswer(data, event.action));
    return;
  }
  event.waitUntil(openUrl(data.url || '/'));
});

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Posts the answer, trying again twice on a network error (a phone waking up may have no
// connection for a moment). The server keeps the first answer to a push, so a retry of one that
// did arrive changes nothing. No cookies: the token is what authenticates it.
async function sendAnswer(data, action) {
  if (!data.action_url || !data.action_token) return;
  const url = new URL(data.action_url, self.location.origin);
  if (url.origin !== self.location.origin) return;
  const body = JSON.stringify({ token: data.action_token, action: action });
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await fetch(url.href, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'omit',
        body: body,
      });
      return;
    } catch (_) {
      await wait(2000 * (attempt + 1));
    }
  }
}

async function openUrl(path) {
  const url = new URL(path, self.location.origin);
  // Only this site's pages: the URL comes from the server, but a payload is never trusted further.
  if (url.origin !== self.location.origin) return;
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of windows) {
    if (client.url === url.href && 'focus' in client) return client.focus();
  }
  if (self.clients.openWindow) return self.clients.openWindow(url.href);
}
