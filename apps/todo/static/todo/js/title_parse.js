/*
 * Natural-language dates in the To-do forms (issue #183), loaded deferred by todo/layout.html.
 *
 * A container with `data-title-parse` holds a text input and a chip (`data-parse-chip`). While the
 * user types, after a 600 ms pause and only when the text changed and has at least 3 characters,
 * the text is posted to `data-parse-url` (`POST /todo/parse/`, JSON). When the server finds a date
 * the chip shows its label with Accept and Dismiss buttons:
 *
 *  - mode "title" (the quick-add form, the default): Accept writes the date, time, repeat and
 *    mode inputs of the form and replaces the title with the server's `clean_title`;
 *  - mode "date" (`data-mode="date"`, the task page's "Type a date..." box): the same, but the
 *    text is not a title, so `clean_title` is ignored and the box is emptied.
 *
 * Dismiss hides the chip and remembers the text, which is not parsed again. Nothing is applied
 * unless the user accepts: submitting the form with the chip showing sends the fields as they are,
 * and the server never re-parses.
 *
 * What Accept changes is exactly what the chip says. The date is always written; the time and the
 * repeat only when the text contains them. A time or repeat the text does not contain leaves the
 * form's own alone (a deliberate choice: typing "tomorrow" into a repeating task moves its date,
 * it does not stop the repeat), so when the form has one the chip says so: "Tomorrow (repeat
 * kept)", "Tomorrow (time and repeat kept)".
 *
 * Failures: `available: false` (no AI configured) turns the feature off for the rest of the page
 * load. A transient failure (`retry: true`: a timeout, a busy server, a non-2xx answer) only backs
 * off: nothing is asked for 60 s, then the current text is. A 429 is waited out silently when it
 * is short, and over a long wait (the daily limit) the script stops for the page. A text counts
 * as handled only once its answer was applied, so one that never got an answer (the user kept
 * typing, the call failed) is asked again if the user comes back to it.
 * Tab reaches the buttons, Enter on the focused chip accepts, Escape dismisses. Everything is
 * wired from `htmx.onLoad`, so the form htmx swaps in is covered too. A box sets
 * `data-parse-ready="1"` once it is wired, which the browser tests wait for before typing.
 */
(function () {
    'use strict';

    var PAUSE_MS = 600;
    var MIN_LENGTH = 3;
    var MAX_RETRY_WAIT_S = 60;
    var BACKOFF_MS = 60000;
    var stopped = false;       // the AI is not configured, or the daily limit is spent
    var backoffUntil = 0;      // a transient failure: nothing is asked before this time

    function csrfToken(form) {
        var field = form && form.querySelector('input[name="csrfmiddlewaretoken"]');
        if (field) return field.value;
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function setValue(input, value) {
        if (!input) return;
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function setup(box) {
        if (box.dataset.parseWired) return;
        var input = box.querySelector('[data-parse-input]');
        var chip = box.querySelector('[data-parse-chip]');
        if (!input || !chip) return;
        box.dataset.parseWired = '1';

        var form = box.closest('form');
        var dateMode = box.dataset.mode === 'date';
        var label = chip.querySelector('[data-parse-label]');
        var status = box.querySelector('[data-parse-status]');
        var timer = null;
        var controller = null;
        var handled = '';   // the text last asked about or accepted: not asked again
        var dismissed = Object.create(null);  // the texts the user dismissed: not asked again either
        var result = null;

        // What Accept leaves alone, in words: the form's own time or repeat the text lacks.
        function labelFor(data) {
            var kept = [];
            var time = form && form.querySelector('[name="due_time"]');
            var rule = form && form.querySelector('input[name="rrule"]');
            if (!data.due_time && time && time.value) kept.push('time');
            if (!data.rrule && rule && rule.value) kept.push('repeat');
            return data.label + (kept.length ? ' (' + kept.join(' and ') + ' kept)' : '');
        }

        function hideChip() {
            chip.hidden = true;
            result = null;
        }

        function say(text) {
            if (status) status.textContent = text;
        }

        function schedule(delay) {
            clearTimeout(timer);
            timer = setTimeout(ask, delay);
        }

        function ask() {
            var text = input.value.replace(/\s+/g, ' ').trim();
            if (stopped || text.length < MIN_LENGTH || text === handled || dismissed[text]) return;
            var wait = backoffUntil - Date.now();
            if (wait > 0) {
                // Backing off after a failure: ask again, for whatever is typed by then, after it.
                schedule(wait + 50);
                return;
            }
            if (controller) controller.abort();
            controller = new AbortController();
            var mine = controller;
            fetch(box.dataset.parseUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(form) },
                body: JSON.stringify({ title: text }),
                credentials: 'same-origin',
                signal: mine.signal,
            }).then(function (response) {
                if (response.status === 429) {
                    // A short wait is waited out silently; a long one (the daily limit) stops the
                    // chip for this page.
                    var retryAfter = parseInt(response.headers.get('Retry-After'), 10) || 1;
                    if (retryAfter > MAX_RETRY_WAIT_S) stopped = true;
                    else schedule(retryAfter * 1000);
                    return null;
                }
                if (!response.ok) {
                    backoffUntil = Date.now() + BACKOFF_MS;
                    return null;
                }
                return response.json();
            }).then(function (data) {
                if (!data) return;
                if (data.available === false) {
                    stopped = true;
                    return;
                }
                if (data.retry) {
                    backoffUntil = Date.now() + BACKOFF_MS;
                    return;
                }
                // The user kept typing: this answer is for text that is gone, and that text is
                // not handled (coming back to it asks again).
                if (input.value.replace(/\s+/g, ' ').trim() !== text) return;
                handled = text;
                if (!data.has_date) {
                    hideChip();
                    if (dateMode) say('No date found.');
                    return;
                }
                result = data;
                label.textContent = labelFor(data);
                chip.title = data.matched_text || '';
                chip.hidden = false;
                say('');
            }).catch(function (error) {
                // Aborted by newer input: nothing. The network failed: back off, no chip.
                if (!error || error.name !== 'AbortError') backoffUntil = Date.now() + BACKOFF_MS;
            });
        }

        function accept() {
            if (!result) return;
            var data = result;
            setValue(form.querySelector('[name="due_date"]'), data.due_date || '');
            if (data.due_time) setValue(form.querySelector('[name="due_time"]'), data.due_time);
            if (data.rrule) {
                var repeat = form.querySelector('[x-data="repeatControl"]');
                var component = repeat && window.Alpine ? window.Alpine.$data(repeat) : null;
                if (component && component.setRule) {
                    component.setRule(data.rrule);
                } else {
                    // No Alpine component: write the inputs the form submits. The mode stays.
                    setValue(form.querySelector('input[name="rrule"]'), data.rrule);
                }
            }
            hideChip();
            if (dateMode) {
                handled = '';
                input.value = '';
            } else {
                setValue(input, data.clean_title || input.value);
                handled = input.value.replace(/\s+/g, ' ').trim();
            }
            input.focus();
        }

        function dismiss() {
            dismissed[input.value.replace(/\s+/g, ' ').trim()] = true;
            hideChip();
            input.focus();
        }

        input.addEventListener('input', function () {
            // The chip describes text that has changed: take it away until the new text is read.
            hideChip();
            say('');
            if (controller) controller.abort();
            schedule(PAUSE_MS);
        });
        // In the date box Enter is "read this now", not "submit the task form".
        if (dateMode) {
            input.addEventListener('keydown', function (event) {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                handled = '';
                clearTimeout(timer);
                ask();
            });
        }
        // The label says what is kept from the form: keep it true if the form changes meanwhile.
        if (form) {
            form.addEventListener('change', function () {
                if (result && !chip.hidden) label.textContent = labelFor(result);
            });
        }
        chip.querySelector('[data-parse-accept]').addEventListener('click', accept);
        chip.querySelector('[data-parse-dismiss]').addEventListener('click', dismiss);
        chip.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                dismiss();
            } else if (event.key === 'Enter' && event.target === chip) {
                event.preventDefault();
                accept();
            }
        });
        box.dataset.parseReady = '1';  // wired: every listener above is in place
    }

    htmx.onLoad(function (root) {
        var scope = root.querySelectorAll ? root : document;
        scope.querySelectorAll('[data-title-parse]').forEach(setup);
        if (root.matches && root.matches('[data-title-parse]')) setup(root);
    });
})();
