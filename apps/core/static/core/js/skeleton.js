/*
 * Skeleton rows for a list that takes a moment to refresh (a search, a tab, a page; issue #214).
 *
 * A list container marked `data-list` that htmx refreshes by a person's action shows three shimmering
 * rows if the answer has not arrived after 250 ms, so a slow answer is a visible wait instead of a frozen
 * page. A fast answer never shows them, and polling (a request with no triggering event) never does.
 *
 * The rows are put *beside* the list's own content, which is only hidden (core/css/site.css,
 * `[data-skeleton]`): the list holds hidden inputs (status, sort, vendor) that the next request reads
 * from the form around it, and htmx has bound its links and buttons, so nothing may be removed or
 * rebuilt. When the answer is swapped in the container's content is replaced as usual; if the request
 * ends without a swap (an error), the rows are removed and the list shows again, untouched.
 * The rows come from `<template id="skeleton-rows">` in core/base.html.
 */
(function () {
    var DELAY = 250;
    var timers = new WeakMap();
    var inflight = new Map(); // xhr -> the list it was sent for (the answer may be retargeted elsewhere)

    function isList(target) {
        return !!target && !!target.hasAttribute && target.hasAttribute('data-list');
    }

    function show(target) {
        if (target.hasAttribute('data-skeleton')) return;
        var template = document.getElementById('skeleton-rows');
        if (!template) return;
        var holder = document.createElement('div');
        holder.setAttribute('data-skeleton-rows', '');
        holder.innerHTML = template.innerHTML;
        target.insertBefore(holder, target.firstChild);
        target.setAttribute('data-skeleton', '');
        target.setAttribute('aria-busy', 'true');
    }

    function clear(target) {
        clearTimeout(timers.get(target));
        var holder = target.querySelector(':scope > [data-skeleton-rows]');
        if (holder) holder.remove();
        target.removeAttribute('data-skeleton');
        target.removeAttribute('aria-busy');
    }

    document.addEventListener('htmx:beforeRequest', function (event) {
        var target = event.detail.target;
        var config = event.detail.requestConfig || {};
        if (!isList(target) || !config.triggeringEvent) return;
        clearTimeout(timers.get(target));
        timers.set(target, setTimeout(function () { show(target); }, DELAY));
        inflight.set(event.detail.xhr, target);
    });

    // The answer was swapped in (the rows went with the old content), or the request ended: either way
    // there is nothing left to wait for. Removing is harmless when they are already gone.
    document.addEventListener('htmx:afterSwap', function (event) {
        if (isList(event.detail.target)) clear(event.detail.target);
    });
    // The request ended. An answer retargeted away from the list (a refused form's message goes to its own
    // slot) never swaps into it, so `afterSwap` above never fires for the list: find it by the request, not by
    // the target the answer ended up with. A request replaced by a newer one (hx-sync) must not clear the
    // newer one's rows, so only clear when no other request for the list is still in flight.
    document.addEventListener('htmx:afterRequest', function (event) {
        var target = inflight.get(event.detail.xhr);
        if (!target) return;
        inflight.delete(event.detail.xhr);
        var others = Array.from(inflight.values()).some(function (other) { return other === target; });
        if (!others) clear(target);
    });
})();
