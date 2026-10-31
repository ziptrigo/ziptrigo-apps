/*
 * Skeleton rows for a list that takes a moment to refresh (a search, a tab, a page; issue #214).
 *
 * A list container marked `data-list` that htmx refreshes by a person's action shows three shimmering
 * rows if the answer has not arrived after 250 ms, so a slow answer is a visible wait instead of a frozen
 * page. A fast answer never shows them. Polling (a request with no triggering event) never does, and
 * if the response isn't swapped in (an error), the list's previous content comes back.
 * The rows come from `<template id="skeleton-rows">` in core/base.html.
 */
(function () {
    var DELAY = 250;
    var timers = new WeakMap();

    function rows() {
        var template = document.getElementById('skeleton-rows');
        return template ? template.innerHTML : '';
    }

    document.addEventListener('htmx:beforeRequest', function (event) {
        var target = event.detail.target;
        var config = event.detail.requestConfig || {};
        if (!target || !target.hasAttribute || !target.hasAttribute('data-list') || !config.triggeringEvent) return;
        clearTimeout(timers.get(target));
        timers.set(target, setTimeout(function () {
            if (target.hasAttribute('data-skeleton')) return;
            target._beforeSkeleton = target.innerHTML;
            target.setAttribute('data-skeleton', '');
            target.setAttribute('aria-busy', 'true');
            target.innerHTML = rows();
        }, DELAY));
    });

    function clear(target) {
        clearTimeout(timers.get(target));
        target.removeAttribute('aria-busy');
        target.removeAttribute('data-skeleton');
        delete target._beforeSkeleton;
    }

    // The answer was swapped in: nothing to restore.
    document.addEventListener('htmx:afterSwap', function (event) {
        var target = event.detail.target;
        if (target && target.hasAttribute && target.hasAttribute('data-list')) clear(target);
    });

    // The request ended without a swap (an error, an empty 204): put back what the list showed.
    document.addEventListener('htmx:afterRequest', function (event) {
        var target = event.detail.target;
        if (!target || !target.hasAttribute || !target.hasAttribute('data-list')) return;
        setTimeout(function () {
            if (target.hasAttribute('data-skeleton') && target._beforeSkeleton !== undefined) {
                target.innerHTML = target._beforeSkeleton;
                clear(target);
            } else {
                clearTimeout(timers.get(target));
            }
        }, 0);
    });
})();
