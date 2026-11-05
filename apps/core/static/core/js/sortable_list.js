// Shared reordering for any list of items with a drag handle: SortableJS for the mouse and touch, the
// arrow keys on a focused handle for the keyboard, a polite live-region announcement for each move and
// one save per burst of key presses. Used by the To-do lists and the account page's "Apps" form.
//
//   initSortableList(container, {
//     handle: '.drag-handle',         // selector of the drag handle inside an item (a focusable
//                                     //   role="button" element, not a <button>: Firefox won't drag from one)
//     item: '[data-task-id]',         // selector of the items, direct children of `container`
//     onReorder(container, items, {viaKeyboard}) {},  // save the new order; `items` are the elements in order
//     label(handle, item) { return 'Item'; },          // the name announced ("<name> moved to position ...")
//     liveRegion: element | (container) => element,    // the polite live region (default: one in <body>)
//     horizontalKeys: false,          // also let Left/Right (earlier/later) move an item (a grid)
//     debounceMs: 400,                // quiet time after the last key press before onReorder runs
//   });
//
// Idempotent per container (call it again after htmx swaps in the list: a container that is already
// set up is left alone). At either end an arrow key announces "<name> is already first/last.".
// Loaded, deferred, after SortableJS by core/partials/sortable_script.html.
(function () {
  'use strict';

  const DEFAULT_DEBOUNCE_MS = 400;
  const configs = new WeakMap();
  const timers = new WeakMap();
  let sharedLive = null;

  function itemsOf(container, config) {
    return Array.from(container.children).filter(function (child) { return child.matches(config.item); });
  }

  function liveRegionFor(container, config) {
    const live = typeof config.liveRegion === 'function' ? config.liveRegion(container) : config.liveRegion;
    if (live) return live;
    if (!sharedLive) {
      sharedLive = document.createElement('div');
      sharedLive.className = 'sr-only';
      sharedLive.setAttribute('aria-live', 'polite');
      sharedLive.setAttribute('data-sortable-live', '');
      document.body.appendChild(sharedLive);
    }
    return sharedLive;
  }

  function announce(container, config, text) {
    const live = liveRegionFor(container, config);
    live.textContent = '';
    window.setTimeout(function () { live.textContent = text; }, 30);
  }

  function reorder(container, config, viaKeyboard) {
    config.onReorder(container, itemsOf(container, config), { viaKeyboard: viaKeyboard });
  }

  function initSortableList(container, options) {
    if (!container) return;
    let config = configs.get(container);
    if (!config) {
      config = Object.assign({
        handle: '.drag-handle',
        horizontalKeys: false,
        debounceMs: DEFAULT_DEBOUNCE_MS,
        label: function () { return 'Item'; },
      }, options);
      configs.set(container, config);
    }
    // SortableJS may load after this call (both are deferred); a later call finishes the job.
    if (typeof Sortable === 'undefined' || Sortable.get(container)) return;
    Sortable.create(container, {
      handle: config.handle,
      draggable: config.item,
      animation: 150,
      onEnd: function (event) {
        if (event.oldIndex !== event.newIndex) reorder(container, config, false);
      },
    });
  }

  document.addEventListener('keydown', function (event) {
    const earlier = event.key === 'ArrowUp' || event.key === 'ArrowLeft';
    const later = event.key === 'ArrowDown' || event.key === 'ArrowRight';
    if (!earlier && !later) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    // The container is the parent of the item that holds the handle; try each ancestor with a config.
    let container = null;
    let config = null;
    for (let node = target.parentElement; node; node = node.parentElement) {
      const found = configs.get(node);
      if (found) { container = node; config = found; break; }
    }
    if (!container || !config) return;
    const handle = target.closest(config.handle);
    if (!handle || !container.contains(handle)) return;
    let item = handle.closest(config.item);
    while (item && item.parentElement !== container) {
      item = item.parentElement && item.parentElement.closest(config.item);
    }
    if (!item) return;
    if (event.key.indexOf('Left') > -1 || event.key.indexOf('Right') > -1) {
      if (!config.horizontalKeys) return;
    }
    event.preventDefault();
    const items = itemsOf(container, config);
    const from = items.indexOf(item);
    const to = from + (earlier ? -1 : 1);
    const name = config.label(handle, item);
    if (to < 0 || to >= items.length) {
      announce(container, config, name + ' is already ' + (earlier ? 'first' : 'last') + '.');
      return;
    }
    if (earlier) container.insertBefore(item, items[to]);
    else container.insertBefore(item, items[to].nextElementSibling);
    handle.focus();
    announce(container, config, name + ' moved to position ' + (to + 1) + ' of ' + items.length + '.');
    window.clearTimeout(timers.get(container));
    timers.set(container, window.setTimeout(function () { reorder(container, config, true); }, config.debounceMs));
  });

  window.initSortableList = initSortableList;
})();
