// Times in the visitor's own time zone, the browser's, written in the site's wording. The server
// keeps every time in UTC (TIME_ZONE = 'UTC'); a signed-in user's saved zone is what the server's
// own `|date` follows, but what this script reads and writes is always UTC, whatever zone the
// server had active, and the browser converts both ways with its own zone. The *wording* is the
// site's, not the browser's locale (issue #245): the date order and the clock come from the
// element's data-date-format / data-time-format (else <html>'s), through core/js/datetime_format.js.
// Loaded on every page by core/base.html, and run again on whatever htmx swaps in.
//
// - `<time datetime="<ISO with offset>" data-local-time="long|short|iso">` gets its text rewritten
//   in local time; with `data-local-time-title` it is the `title` (tooltip) that is rewritten
//   instead. The server renders the UTC fallback, marked "UTC", for anyone without JavaScript:
//   use the `{% local_time %}` tag (core/templatetags/local_time.py), which does both.
//   long = "11/05/2026, 9:05 PM" (public: "Nov 5, 2026, 9:05 PM"), short = the same without the
//   year when it is this year's, iso = "2026-11-05 9:05 PM".
// - A date field and a time field (core/components/date_picker.html, time_field.html), wrapped in
//   `data-pair-date` and `data-pair-time` elements inside one with `data-utc-pair` and
//   `data-utc-target="<id>"`, show the hidden input `<id>`, the
//   one the form submits, in local time and write the UTC value back
//   (`2026-10-27T13:51:00.000Z`) whenever either changes; a date without a time means the end of
//   that day. Prefill the hidden input in UTC with an offset (`2026-10-27T13:51Z`). Without
//   JavaScript nothing is sent.
(function () {
    const Z = window.ZTDateTime;

    function pad(n) {
        return String(n).padStart(2, '0');
    }

    function parse(value) {
        const d = new Date(value);
        return Number.isNaN(d.getTime()) ? null : d;
    }

    function format(d, style, formats) {
        const time = Z.formatTime(d.getHours(), d.getMinutes(), formats);
        if (style === 'iso') {
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${time}`;
        }
        const day = Z.formatDate(d.getFullYear(), d.getMonth() + 1, d.getDate(),
            style === 'short' ? 'compact' : 'full', formats);
        return `${day}, ${time}`;
    }

    function localizeTime(el) {
        const d = parse(el.getAttribute('datetime'));
        if (!d) return;
        const text = format(d, el.dataset.localTime, Z.formatsFor(el));
        if (el.hasAttribute('data-local-time-title')) el.title = text;
        else el.textContent = text;
    }

    function bindPair(box) {
        if (box.dataset.utcBound) return;
        const utc = document.getElementById(box.dataset.utcTarget);
        const dateInput = box.querySelector('[data-pair-date] input[type=hidden]');
        const timeInput = box.querySelector('[data-pair-time] input[type=hidden]');
        if (!utc || !dateInput || !timeInput) return;
        box.dataset.utcBound = '1';

        function show(iso) {
            const d = iso ? parse(iso) : null;
            const date = d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : '';
            const time = d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
            // The fields may not be initialised yet (Alpine starts after this script); they read
            // the hidden value when they do, and listen for this event when they already have.
            for (const [input, value] of [[dateInput, date], [timeInput, time]]) {
                input.value = value;
                input.dispatchEvent(new CustomEvent('field-set', { detail: { value } }));
            }
        }

        function sync() {
            if (!dateInput.value) {
                utc.value = '';
                return;
            }
            const [y, m, d] = dateInput.value.split('-').map(Number);
            const [hh, mm] = (timeInput.value || '23:59').split(':').map(Number);
            const local = new Date(y, m - 1, d, hh, mm);
            utc.value = Number.isNaN(local.getTime()) ? '' : local.toISOString();
        }

        if (utc.value) show(utc.value);
        box.addEventListener('change', (event) => {
            if (event.target === dateInput || event.target === timeInput) sync();
        });
    }

    function init(root) {
        root.querySelectorAll('time[data-local-time]').forEach(localizeTime);
        root.querySelectorAll('[data-utc-pair]').forEach(bindPair);
    }

    // `htmx:load` fires for every element htmx swaps in, out-of-band swaps included.
    document.addEventListener('htmx:load', (event) => init(event.detail.elt));
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => init(document));
    } else {
        init(document);
    }
})();
