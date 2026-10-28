// The signed-in user's time zone (issue #177). Loaded, deferred, by core/base.html for signed-in
// users only. Not related to core/js/local_time.js, which only shows times in the browser's zone.
//
// 1. Detection: reads the browser's IANA zone. If the account has none yet (<html
//    data-user-timezone> is empty), it posts it once per browser session to the URL in this
//    script's `data-url` (form-encoded, with the CSRF token). The server only stores it while the
//    account still has no zone, and this never overrides a saved one.
// 2. Account page: next to the Time zone select, shows "Detected from your browser: <zone>" with a
//    "Use it" link that picks it in the select, whenever it differs from the selected zone (and is
//    one of the select's options). Run again on whatever htmx swaps in, since saving the profile
//    replaces the form. When detection saves the zone while the page shows "Not set", it also
//    selects the zone there.
(function () {
  const url = document.currentScript && document.currentScript.dataset.url;

  function detected() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (_) {
      return '';
    }
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    const input = document.querySelector('input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function storage(action, key, value) {
    // sessionStorage can be missing or throw (private windows, blocked site data).
    try {
      return action === 'get' ? window.sessionStorage.getItem(key) : window.sessionStorage.setItem(key, value);
    } catch (_) {
      return null;
    }
  }

  function sendDetected(zone) {
    const html = document.documentElement;
    if (!url || !zone || html.dataset.userTimezone || storage('get', 'tzSent')) return;
    // Marked before the request: a refusal (or a network error) isn't retried on every page.
    // `keepalive` lets the request finish when the visitor navigates away at once (the page they
    // land on after signing in is often left within a second), which would otherwise lose it for
    // the rest of the session.
    storage('set', 'tzSent', '1');
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken() },
      body: new URLSearchParams({ timezone: zone }),
      credentials: 'same-origin',
      keepalive: true,
    }).then((response) => {
      if (!response.ok) return;
      html.dataset.userTimezone = zone;
      adoptDetected(zone);
    }).catch(() => {});
  }

  // The account page was rendered with "Not set" before the zone was saved. Show the saved zone in
  // its select, unless the visitor has already picked something, so the page doesn't say "Not
  // set" for a zone that is now saved. (The form also posts the zone it was rendered with and the
  // server ignores an untouched select, so saving the name can't clear it either way.)
  function adoptDetected(zone) {
    const select = document.querySelector('select[name=timezone]');
    if (!select || select.value !== '') return;
    if (!Array.from(select.options).some((option) => option.value === zone)) return;
    select.value = zone;
    const hint = document.getElementById('timezone-detected');
    if (hint) hint.hidden = true;
  }

  function showHint(root, zone) {
    const select = root.querySelector('select[name=timezone]');
    const hint = root.querySelector('#timezone-detected');
    if (!select || !hint || !zone) return;
    const offered = Array.from(select.options).some((option) => option.value === zone);
    hint.hidden = !offered || select.value === zone;
    if (hint.dataset.bound) return;
    hint.dataset.bound = '1';
    hint.querySelector('[data-timezone-detected]').textContent = zone;
    hint.querySelector('[data-timezone-use]').addEventListener('click', (event) => {
      event.preventDefault();
      select.value = zone;
      hint.hidden = true;
    });
    select.addEventListener('change', () => {
      hint.hidden = select.value === zone;
    });
  }

  const zone = detected();
  sendDetected(zone);
  showHint(document, zone);
  // `htmx:load` fires for every element htmx swaps in, so the form saved over htmx gets its hint.
  document.addEventListener('htmx:load', (event) => showHint(event.detail.elt, zone));
})();
