// Web Push in the browser (issue #239). Loaded, deferred, by core/base.html for signed-in users
// while push is configured (`{% push_script %}`); its `data-*` attributes carry the URLs, the
// VAPID public key, whether this session already saved a subscription (`data-synced`) and an
// opaque id of the signed-in account (`data-owner`).
//
// 1. Every page: when this browser already has a push subscription and permission, *and the
//    signed-in account is the one that turned push on here* (`data-owner` equals the owner kept in
//    localStorage), but the session hasn't saved it (a new sign-in: signing out deleted it on the
//    server) or the browser renewed it since (the endpoint kept in localStorage differs), it posts
//    the subscription again. No prompt: that account already allowed it. Another account signing
//    in on the same browser gets nothing until it turns push on itself, and without localStorage
//    nothing is re-posted at all.
// 2. The account page's "Notifications on this device" section ([data-push-settings]): shows
//    whether push is on, off, blocked or unsupported, and turns it on (register the service
//    worker, ask for permission, subscribe, save) or off (delete on the server, unsubscribe). The
//    permission prompt only ever follows a click.
(function () {
  const script = document.currentScript;
  if (!script) return;
  const config = script.dataset;
  // `{"endpoint": ..., "owner": ...}`: which subscription was saved, and for which account.
  const STORAGE_KEY = 'zt-push';

  function supported() {
    return (
      window.isSecureContext &&
      'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window
    );
  }

  // Safari on iPhone and iPad only offers push to a site added to the Home Screen (iOS 16.4+).
  function isIosBrowserTab() {
    const ios =
      /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const standalone =
      navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
    return ios && !standalone;
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    const input = document.querySelector('input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  // localStorage can be missing or throw (private windows, blocked site data): then nothing is
  // remembered, and nothing is re-posted on its own.
  function remembered() {
    try {
      const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || 'null');
      return value && typeof value === 'object' ? value : null;
    } catch (_) {
      return null;
    }
  }

  function remember(value) {
    try {
      if (value) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch (_) {
      /* not remembered */
    }
  }

  function ownedHere() {
    const record = remembered();
    return Boolean(record && config.owner && record.owner === config.owner);
  }

  function keyBytes(base64url) {
    const padding = '='.repeat((4 - (base64url.length % 4)) % 4);
    const raw = atob((base64url + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  function sameKey(buffer, bytes) {
    if (!buffer) return false;
    const current = new Uint8Array(buffer);
    return current.length === bytes.length && current.every((b, i) => b === bytes[i]);
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
  }

  async function errorText(response) {
    try {
      const data = await response.json();
      return data.detail || data.error || '';
    } catch (_) {
      return '';
    }
  }

  async function saveSubscription(subscription) {
    const response = await post(config.subscribeUrl, subscription.toJSON());
    if (!response.ok) {
      throw new Error((await errorText(response)) || 'The subscription could not be saved.');
    }
    remember({ endpoint: subscription.endpoint, owner: config.owner });
    config.synced = '1';
  }

  async function currentSubscription() {
    const registration = await navigator.serviceWorker.getRegistration('/');
    if (!registration) return null;
    return registration.pushManager.getSubscription();
  }

  // 1. Every page.
  async function resync() {
    if (!supported() || Notification.permission !== 'granted') return;
    if (!ownedHere()) return;
    const subscription = await currentSubscription();
    if (!subscription) return;
    if (config.synced === '1' && remembered().endpoint === subscription.endpoint) return;
    await saveSubscription(subscription);
  }

  // 2. The account page.
  function setupSettings(section) {
    const message = section.querySelector('[data-push-message]');
    const buttons = section.querySelectorAll('button[data-push-action]');

    function show(state, text) {
      section.querySelectorAll('[data-push-state]').forEach((el) => {
        el.hidden = el.dataset.pushState !== state;
      });
      section.dataset.pushCurrent = state;
      if (message) message.textContent = text || '';
    }

    function busy(on) {
      buttons.forEach((b) => {
        b.disabled = on;
        b.setAttribute('aria-busy', on ? 'true' : 'false');
      });
    }

    async function refresh() {
      if (!supported()) return show(isIosBrowserTab() ? 'ios' : 'unsupported');
      if (Notification.permission === 'denied') return show('blocked');
      const subscription = await currentSubscription().catch(() => null);
      // On only for the account that turned it on here (or that this session saved): a browser
      // subscription another account left behind is "off" for this one, which can take it over
      // with the button.
      const ours = config.synced === '1' || ownedHere();
      show(subscription && ours && Notification.permission === 'granted' ? 'on' : 'off');
    }

    async function turnOn() {
      // The prompt must come straight from the click, before anything else is awaited.
      const permission = await Notification.requestPermission();
      if (permission === 'denied') return show('blocked');
      if (permission !== 'granted') return show('off', 'Notifications were not allowed.');
      const registration = await navigator.serviceWorker.register(config.swUrl, { scope: '/' });
      await navigator.serviceWorker.ready;
      const key = keyBytes(config.publicKey);
      let subscription = await registration.pushManager.getSubscription();
      if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
        // Subscribed with an earlier key pair: that subscription can't receive our pushes.
        await subscription.unsubscribe();
        subscription = null;
      }
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: key,
        });
      }
      try {
        await saveSubscription(subscription);
      } catch (error) {
        // Not saved, so nothing would reach it: don't leave the browser subscribed.
        await subscription.unsubscribe().catch(() => {});
        throw error;
      }
      show('on', 'Notifications are on for this device.');
    }

    async function turnOff() {
      const subscription = await currentSubscription();
      if (subscription) {
        const response = await post(config.unsubscribeUrl, { endpoint: subscription.endpoint });
        if (!response.ok) throw new Error((await errorText(response)) || 'Could not turn notifications off.');
        await subscription.unsubscribe();
      }
      remember(null);
      config.synced = '0';
      show('off', 'Notifications are off for this device.');
    }

    section.addEventListener('click', async (event) => {
      const button = event.target instanceof Element ? event.target.closest('button[data-push-action]') : null;
      if (!button || button.disabled) return;
      busy(true);
      try {
        if (button.dataset.pushAction === 'on') await turnOn();
        else await turnOff();
      } catch (error) {
        await refresh();
        // Our own errors carry the server's message; the browser's (a DOMException from the push
        // service, say) are technical, so they get a plain one.
        const text = error instanceof DOMException || !(error && error.message)
          ? 'Something went wrong. Try again.'
          : error.message;
        if (message) message.textContent = text;
      } finally {
        busy(false);
      }
    });

    refresh();
  }

  function start() {
    resync().catch(() => {});
    const section = document.querySelector('[data-push-settings]');
    if (section) setupSettings(section);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
