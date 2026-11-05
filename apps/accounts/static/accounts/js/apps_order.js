// The account page's "Apps" form (issue #246): putting the apps in order.
//
// Loaded, deferred, by accounts/account.html after SortableJS. Each tile of `[data-apps-grid]` has a
// drag handle (`.drag-handle`, hidden until this script shows it, since ordering needs JavaScript)
// and a hidden `order` input, so moving a tile is all it takes: after a move the form is submitted
// the way a checkbox change does (htmx `change`), and the server saves the DOM order.
//
//  - Mouse and touch: SortableJS, by the handle.
//  - Keyboard: the up and down (and left and right) arrow keys on a focused handle move its tile one
//    place. Focus stays on the handle, the move is announced in the polite live region, and one
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
    const live = grid.closest('form').querySelector('[data-apps-live]');
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
    if (to < 0 || to >= tiles.length) return;
    if (earlier) grid.insertBefore(tile, tiles[to]);
    else grid.insertBefore(tile, tiles[to].nextElementSibling);
    handle.focus();
    announce(grid, (handle.dataset.name || 'App') + ' moved to position ' + (to + 1) + ' of ' + tiles.length + '.');
    window.clearTimeout(timers.get(grid));
    timers.set(grid, window.setTimeout(function () { save(grid); }, SAVE_DELAY_MS));
  });

  wire(document);
  if (window.htmx) window.htmx.onLoad(wire);
})();
