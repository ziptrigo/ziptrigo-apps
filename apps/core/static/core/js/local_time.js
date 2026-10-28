// Times in the visitor's own time zone. The server keeps every time in UTC (TIME_ZONE = 'UTC',
// no per-user time zone), so a time of day rendered or read on the server is UTC; the browser
// knows its own zone and converts both ways. Loaded on every page by core/base.html, and run again
// on whatever htmx swaps in.
//
// - `<time datetime="<ISO with offset>" data-local-time="long|short|iso">` gets its text rewritten
//   in local time; with `data-local-time-title` it is the `title` (tooltip) that is rewritten
//   instead. The server renders the UTC fallback, marked "UTC", for anyone without JavaScript:
//   use the `{% local_time %}` tag (core/templatetags/local_time.py), which does both.
//   long = "Oct 27, 2026, 8:51 AM", short = "Oct 27, 8:51 AM", iso = "2026-10-27 08:51".
// - `<input type="datetime-local" data-utc-target="<id>">` (no `name`) shows the hidden input
//   `<id>`, the one the form submits, in local time and writes the UTC value back
//   (`2026-10-27T13:51:00.000Z`) whenever the visible one changes. Prefill the hidden input in UTC
//   with an offset (`2026-10-27T13:51Z`). Without JavaScript nothing is sent.
(function () {
    function pad(n) {
        return String(n).padStart(2, '0');
    }

    function parse(value) {
        const d = new Date(value);
        return Number.isNaN(d.getTime()) ? null : d;
    }

    function toLocalInputValue(iso) {
        const d = parse(iso);
        if (!d) return '';
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    const FORMATS = {
        long: { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' },
        short: { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' },
    };

    function format(d, style) {
        if (style === 'iso') {
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
        }
        return d.toLocaleString(undefined, FORMATS[style] || FORMATS.long);
    }

    function localizeTime(el) {
        const d = parse(el.getAttribute('datetime'));
        if (!d) return;
        const text = format(d, el.dataset.localTime);
        if (el.hasAttribute('data-local-time-title')) el.title = text;
        else el.textContent = text;
    }

    function bindInput(input) {
        if (input.dataset.utcBound) return;
        const hidden = document.getElementById(input.dataset.utcTarget);
        if (!hidden) return;
        input.dataset.utcBound = '1';
        if (hidden.value) input.value = toLocalInputValue(hidden.value);
        function sync() {
            if (!input.value) {
                hidden.value = '';
                return;
            }
            // A `datetime-local` string without an offset is parsed as local time.
            const d = parse(input.value);
            hidden.value = d ? d.toISOString() : input.value;
        }
        input.addEventListener('input', sync);
        input.addEventListener('change', sync);
    }

    function init(root) {
        root.querySelectorAll('time[data-local-time]').forEach(localizeTime);
        root.querySelectorAll('input[data-utc-target]').forEach(bindInput);
    }

    // `htmx:load` fires for every element htmx swaps in, out-of-band swaps included.
    document.addEventListener('htmx:load', (event) => init(event.detail.elt));
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => init(document));
    } else {
        init(document);
    }
})();
