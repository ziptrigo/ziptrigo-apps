/*
 * The sliding underline of the filter tabs (`<nav data-tabs>` with the current tab marked
 * `aria-current="page"`, e.g. the Receipts, Vendors and Short URLs status tabs).
 *
 * The tabs are re-sent with every list refresh (htmx swaps the whole bar), so the indicator can't
 * live inside the swapped markup and animate by itself. Instead: before a swap, remember where each
 * bar's current tab sat; after it settles, put a fresh indicator at the old spot and let it slide to
 * the new one. Without this script the current tab keeps its static underline (`border-brand-primary`),
 * which this script hides only once its own indicator is in place (`has-indicator`, core/css/site.css).
 */
(function () {
    var remembered = {};

    function geometry(tab) {
        return { left: tab.offsetLeft, top: tab.offsetTop + tab.offsetHeight - 2, width: tab.offsetWidth };
    }

    function apply(indicator, g, animate) {
        indicator.style.transition = animate ? '' : 'none';
        indicator.style.left = g.left + 'px';
        indicator.style.top = g.top + 'px';
        indicator.style.width = g.width + 'px';
    }

    // Idempotent: htmx settles a swap (and its out-of-band parts) more than once, and a second call
    // must not cancel the slide the first one started.
    function place(nav, from) {
        var tab = nav.querySelector('[aria-current="page"]');
        var indicator = nav.querySelector('.tab-indicator');
        if (!tab) {
            if (indicator) indicator.remove();
            nav.classList.remove('has-indicator');
            return;
        }
        var to = geometry(tab);
        var key = to.left + ':' + to.top + ':' + to.width;
        if (indicator && indicator.dataset.at === key) return;
        var fresh = !indicator;
        if (fresh) {
            indicator = document.createElement('span');
            indicator.className = 'tab-indicator';
            indicator.setAttribute('aria-hidden', 'true');
            nav.appendChild(indicator);
        }
        nav.classList.add('has-indicator');
        if (fresh && from) {
            apply(indicator, from, false);
            void indicator.offsetWidth; // commit the starting spot before moving
            apply(indicator, to, true);
        } else {
            apply(indicator, to, false);
        }
        indicator.dataset.at = key;
    }

    function placeAll() {
        document.querySelectorAll('[data-tabs]').forEach(function (nav) {
            place(nav, remembered[nav.getAttribute('aria-label')]);
        });
    }

    document.addEventListener('htmx:beforeSwap', function () {
        remembered = {};
        document.querySelectorAll('[data-tabs]').forEach(function (nav) {
            var tab = nav.querySelector('[aria-current="page"]');
            if (tab) remembered[nav.getAttribute('aria-label')] = geometry(tab);
        });
    });
    document.addEventListener('htmx:afterSettle', placeAll);
    window.addEventListener('resize', function () { remembered = {}; placeAll(); });
    document.addEventListener('DOMContentLoaded', function () {
        placeAll();
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeAll);
    });
})();
