/*
 * The shared event page (world_time/public/event.html): keeps the line under the title current
 * ("Starts in 3 h 20 min", "Happening now: ends in 40 min", "Ended") and names the visitor's time
 * zone. The server renders the same sentence for the moment the page was made, so a visitor
 * without JavaScript still reads it. The wording is `services.events.status_text`'s.
 */
(function () {
    'use strict';

    function span(ms) {
        const minutes = Math.max(1, Math.floor(ms / 60000));
        const days = Math.floor(minutes / 1440);
        const hours = Math.floor((minutes % 1440) / 60);
        const mins = minutes % 60;
        if (days) return days + (days === 1 ? ' day' : ' days') + (hours ? ' ' + hours + ' h' : '');
        if (hours) return hours + ' h' + (mins ? ' ' + mins + ' min' : '');
        return mins + ' min';
    }

    function update(element) {
        const start = Date.parse(element.dataset.start);
        const end = Date.parse(element.dataset.end);
        if (Number.isNaN(start) || Number.isNaN(end)) return;
        const now = Date.now();
        let text;
        if (now >= end) text = 'Ended';
        else if (now >= start) text = 'Happening now: ends in ' + span(end - now);
        else text = 'Starts in ' + span(start - now);
        if (element.textContent !== text) element.textContent = text;
    }

    function run() {
        document.querySelectorAll('[data-wt-countdown]').forEach(update);
    }

    document.addEventListener('DOMContentLoaded', function () {
        run();
        setInterval(run, 15000);
        const note = document.querySelector('[data-wt-zone-note]');
        try {
            const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            if (note && zone) note.textContent = 'Shown in the time zone of this device (' + zone.replace(/_/g, ' ') + ').';
        } catch (_) { /* the plain sentence stays */ }
    });
})();
