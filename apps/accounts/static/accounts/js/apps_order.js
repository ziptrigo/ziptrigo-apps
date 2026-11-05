// The account page's "Apps" form (issue #246): putting the apps in order.
//
// Loaded, deferred, by accounts/account.html after SortableJS. Each tile of `[data-apps-grid]` has a
// drag handle (`.drag-handle`, hidden until this script shows it, since ordering needs JavaScript)
// and a hidden `order` input, so moving a tile is all it takes: after a move the form is submitted
// the way a checkbox change does (htmx `change`), and the server saves the DOM order.
//
//  - Mouse and touch: SortableJS, by the handle.
//  - Keyboard: the arrow keys (up/left earlier, down/right later) on a focused handle move its tile one
//    place; at either end the handle announces that it is already first or last. Focus stays on the handle, the move is announced in the polite live region, and one
//    request is sent for a burst of key presses.
//
// Run again on whatever htmx swaps in (a refused save re-renders the form).
(function () {
  'use strict';

  const SAVE_DELAY_MS = 400;
  const timers = new WeakMap();

  function tilesOf(grid) {
    return Array.from(grid.children).filter(function (child) { return child.hasAttribute('data-app'); });
  }

  function save(grid) {
    const form = grid.closest('form');
    if (form && window.htmx) window.htmx.trigger(form, 'change');
  }

  function announce(grid, text) {
    const form = grid.closest('form');
    const live = form && form.querySelector('[data-apps-live]');
    if (!live) return;
    live.textContent = '';
    window.setTimeout(function () { live.textContent = text; }, 30);
  }

  function wire(root) {
    const scope = root && root.querySelectorAll ? root : document;
    const grids = Array.from(scope.querySelectorAll('[data-apps-grid]'));
    if (scope.matches && scope.matches('[data-apps-grid]')) grids.push(scope);
    grids.forEach(function (grid) {
      grid.querySelectorAll('.drag-handle').forEach(function (handle) { handle.classList.remove('hidden'); });
      if (typeof Sortable === 'undefined' || Sortable.get(grid)) return;
      Sortable.create(grid, {
        handle: '.drag-handle',
        draggable: '[data-app]',
        animation: 150,
        onEnd: function (event) {
          if (event.oldIndex !== event.newIndex) save(grid);
        },
      });
    });
  }

  document.addEventListener('keydown', function (event) {
    const earlier = event.key === 'ArrowUp' || event.key === 'ArrowLeft';
    const later = event.key === 'ArrowDown' || event.key === 'ArrowRight';
    if (!earlier && !later) return;
    const handle = event.target instanceof Element ? event.target.closest('.drag-handle') : null;
    const tile = handle && handle.closest('[data-app]');
    const grid = tile && tile.parentElement;
    if (!grid || !grid.hasAttribute('data-apps-grid')) return;
    event.preventDefault();
    const tiles = tilesOf(grid);
    const from = tiles.indexOf(tile);
    const to = from + (earlier ? -1 : 1);
    const name = handle.dataset.name || 'App';
    if (to < 0 || to >= tiles.length) {
      announce(grid, name + ' is already ' + (earlier ? 'first' : 'last') + '.');
      return;
    }
    if (earlier) grid.insertBefore(tile, tiles[to]);
    else grid.insertBefore(tile, tiles[to].nextElementSibling);
    handle.focus();
    announce(grid, name + ' moved to position ' + (to + 1) + ' of ' + tiles.length + '.');
    window.clearTimeout(timers.get(grid));
    timers.set(grid, window.setTimeout(function () { save(grid); }, SAVE_DELAY_MS));
  });

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
