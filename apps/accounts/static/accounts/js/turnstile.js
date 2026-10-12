// Cloudflare Turnstile on the register form (issue #117).
//
// Explicit rendering: htmx replaces the whole form, widget included, on every 422, and the new copy
// needs its own widget (a token is single-use anyway). `htmx.onLoad` covers swapped-in content and
// `zipTurnstileLoaded` (api.js's `onload`) covers the first page load, whichever happens last.
// The form's submit button is disabled from the moment a widget renders until it hands over a
// token, and again when that token expires or the widget errors. If Cloudflare's script never
// loads, nothing here runs and the button stays enabled; the server then rejects the submission
// with a captcha error rather than leaving a button that can never be clicked.
//
// A 429 is retargeted to the page's message element, so the form (and its widget) is *not*
// replaced. The token may already be spent by then (`SIGNUP_IP` is checked after the captcha), so
// the widget is reset and the button disabled until it hands over a new one.
(function () {
  function theme() {
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  }

  function renderIn(root) {
    if (!window.turnstile || !root || !root.querySelectorAll) return;
    root.querySelectorAll('.cf-turnstile').forEach(function (el) {
      if (el.dataset.turnstileRendered) return;
      el.dataset.turnstileRendered = '1';

      var form = el.closest('form');
      var submit = form && form.querySelector('button[type="submit"]');
      function setReady(ready) {
        if (submit) submit.disabled = !ready;
      }

      setReady(false);
      el.dataset.widgetId = window.turnstile.render(el, {
        sitekey: el.dataset.sitekey,
        action: el.dataset.action,
        size: 'flexible',
        theme: theme(),
        callback: function () { setReady(true); },
        'expired-callback': function () { setReady(false); },
        'error-callback': function () { setReady(false); },
      });
    });
  }

  function resetAfterRateLimit(event) {
    if (!window.turnstile || !event.detail.xhr || event.detail.xhr.status !== 429) return;
    var form = event.detail.elt && event.detail.elt.closest && event.detail.elt.closest('form');
    if (!form) return;
    form.querySelectorAll('.cf-turnstile[data-widget-id]').forEach(function (el) {
      var submit = form.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      window.turnstile.reset(el.dataset.widgetId);
    });
  }

  window.zipTurnstileLoaded = function () { renderIn(document); };
  htmx.onLoad(renderIn);
  document.addEventListener('htmx:afterRequest', resetAfterRateLimit);
})();
