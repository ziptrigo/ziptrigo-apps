// The account page's "Apps" form (issue #246): putting the apps in order.
//
// Loaded, deferred, by accounts/account.html after SortableJS and the shared helper. Each tile of `[data-apps-grid]` has a
// drag handle (`.drag-handle`, hidden until this script shows it, since ordering needs JavaScript)
// and a hidden `order` input, so moving a tile is all it takes: after a move the form is submitted
// the way a checkbox change does (htmx `change`), and the server saves the DOM order.
//
// The dragging (SortableJS) and the arrow keys (up/left earlier, down/right later; announcements, one
// request per burst) are core's `initSortableList` (core/js/sortable_list.js); the live region is
// `[data-apps-live]`. This file only reveals the handles, saves and handles a failed save.
//
// Run again on whatever htmx swaps in (a refused save re-renders the form).
(function () {
  'use strict';

  function save(grid) {
    const form = grid.closest('form');
    if (form && window.htmx) window.htmx.trigger(form, 'change');
  }

  function wire(root) {
    const scope = root && root.querySelectorAll ? root : document;
    const grids = Array.from(scope.querySelectorAll('[data-apps-grid]'));
    if (scope.matches && scope.matches('[data-apps-grid]')) grids.push(scope);
    grids.forEach(function (grid) {
      grid.querySelectorAll('.drag-handle').forEach(function (handle) { handle.classList.remove('hidden'); });
      if (typeof window.initSortableList !== 'function') return;
      window.initSortableList(grid, {
        handle: '.drag-handle',
        item: '[data-app]',
        horizontalKeys: true,
        label: function (handle) { return handle.dataset.name || 'App'; },
        liveRegion: function (el) {
          const form = el.closest('form');
          return form && form.querySelector('[data-apps-live]');
        },
        onReorder: save,
      });
    });
  }

  // A save that never got an answer (network error) or got a server error (htmx doesn't swap a 5xx):
  // say so, and fetch the stored state so the tiles match what the server has.
  function saveFailed(event) {
    const form = event.target;
    const config = event.detail && event.detail.requestConfig;
    if (!form || form.id !== 'apps-form' || !config || String(config.verb).toLowerCase() !== 'post') return;
    const old = form.querySelector('[data-apps-error]');
    if (old) old.remove();
    const message = document.createElement('div');
    message.setAttribute('data-apps-error', '');
    message.setAttribute('role', 'alert');
    message.className = 'mb-4 text-sm p-3 rounded-xl bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200 border border-red-300 dark:border-red-700';
    message.textContent = "Couldn't save your apps. Try again.";
    form.insertBefore(message, form.firstElementChild && form.firstElementChild.nextSibling);
    const url = form.getAttribute('data-form-url');
    if (url && window.htmx) {
      window.htmx.ajax('GET', url + '?failed=1', { target: '#apps-form', swap: 'outerHTML' });
    }
  }
  document.addEventListener('htmx:responseError', saveFailed);
  document.addEventListener('htmx:sendError', saveFailed);

  wire(document);
  if (window.htmx) window.htmx.onLoad(wire);
})();
