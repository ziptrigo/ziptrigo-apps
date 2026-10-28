// The server keeps times in UTC (TIME_ZONE = 'UTC', no per-user time zone), so a bare
// `datetime-local` value would be read as UTC and a user in Chicago picking 08:48 would get 08:48
// UTC. The browser knows its own zone, so it converts both ways:
//
// - `<input type="datetime-local" data-utc-target="<id>">` shows the hidden input `<id>` (which
//   holds the UTC value the form submits, e.g. `2026-10-27T13:48Z`) in local time, and writes the
//   UTC value back whenever the visible one changes;
// - `<time datetime="<ISO with offset>" data-local-time>` is rewritten in local time (the server
//   renders it in UTC, marked "UTC", for anyone without JavaScript).
(function () {
    function pad(n) {
        return String(n).padStart(2, '0');
    }

    function toLocalInputValue(iso) {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '';
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function bindInput(input) {
        const hidden = document.getElementById(input.dataset.utcTarget);
        if (!hidden) return;
        if (hidden.value) input.value = toLocalInputValue(hidden.value);
        function sync() {
            if (!input.value) {
                hidden.value = '';
                return;
            }
            // A `datetime-local` string without an offset is parsed as local time.
            const d = new Date(input.value);
            hidden.value = Number.isNaN(d.getTime()) ? input.value : d.toISOString();
        }
        input.addEventListener('input', sync);
        input.addEventListener('change', sync);
    }

    function localizeTime(el) {
        const d = new Date(el.getAttribute('datetime'));
        if (Number.isNaN(d.getTime())) return;
        el.textContent = d.toLocaleString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function init() {
        document.querySelectorAll('input[data-utc-target]').forEach(bindInput);
        document.querySelectorAll('time[data-local-time]').forEach(localizeTime);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
