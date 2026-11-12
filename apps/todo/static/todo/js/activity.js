/*
 * The task page's History (issue #274). The activity log is not part of the page: the muted
 * "History" button fetches the first page (`GET /todo/<n>/activity/`) into #task-activity-body the
 * first time it is pressed, and presses after that only show or hide what is there. "Show more"
 * is a plain htmx button inside the fetched HTML; this file moves keyboard focus to what just
 * arrived (the heading after the first load, the first new row after "Show more") and re-fetches the
 * first page after an action on the task, so an open history doesn't go stale.
 */
(function () {
    'use strict';

    const toggle = document.getElementById('task-activity-toggle');
    const body = document.getElementById('task-activity-body');
    if (!toggle || !body) return;

    let loaded = false;

    function load(focusHeading) {
        const request = htmx.ajax('GET', toggle.dataset.url, { target: body, swap: 'innerHTML' });
        if (focusHeading && request && request.then) {
            request.then(function () {
                const heading = document.getElementById('task-activity-heading');
                if (heading) heading.focus();
            });
        }
    }

    toggle.addEventListener('click', function () {
        const opening = body.hidden;
        body.hidden = !opening;
        toggle.setAttribute('aria-expanded', opening ? 'true' : 'false');
        toggle.textContent = opening ? 'Hide history' : 'History';
        if (opening && !loaded) {
            loaded = true;
            load(true);
        }
    });

    // Rows added by "Show more": focus the first new one, so the keyboard continues from there.
    document.addEventListener('htmx:afterSettle', function (event) {
        const target = event.detail && event.detail.target;
        if (!target) return;
        if (target.id === 'task-activity-list') {
            const fresh = target.querySelector('[data-activity-new]');
            if (fresh) {
                fresh.removeAttribute('data-activity-new');
                fresh.focus();
            }
        } else if (loaded && !body.hidden && (target.id === 'task-panel' || target.id === 'attachment-list')) {
            load(false);  // something changed on the task: show it
        }
    });
})();
