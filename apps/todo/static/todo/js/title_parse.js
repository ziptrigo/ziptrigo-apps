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
 * and the server never re-parses. A time or a repeat the text does not contain leaves the form's
 * own time and repeat alone. When the server answers `available: false` (no AI configured, or the
 * call failed) the script stops asking for the rest of the page load; a 429 is waited out
 * silently. Tab reaches the buttons, Enter on the focused chip accepts, Escape dismisses.
 * Everything is wired from `htmx.onLoad`, so the form htmx swaps in is covered too.
 */
(function () {
    'use strict';

    var PAUSE_MS = 600;
    var MIN_LENGTH = 3;
    var MAX_RETRY_WAIT_S = 60;
    var stopped = false;

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
        if (box.dataset.parseReady) return;
        var input = box.querySelector('[data-parse-input]');
        var chip = box.querySelector('[data-parse-chip]');
        if (!input || !chip) return;
        box.dataset.parseReady = '1';

        var form = box.closest('form');
        var dateMode = box.dataset.mode === 'date';
        var label = chip.querySelector('[data-parse-label]');
        var status = box.querySelector('[data-parse-status]');
        var timer = null;
        var controller = null;
        var handled = '';   // the text last asked about or accepted: not asked again
        var dismissed = Object.create(null);  // the texts the user dismissed: not asked again either
        var result = null;

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
                    // Waited out silently: ask again when the budget is back.
                    var wait = parseInt(response.headers.get('Retry-After'), 10) || 1;
                    schedule(Math.min(wait, MAX_RETRY_WAIT_S) * 1000);
                    return null;
                }
                if (!response.ok) return null;
                return response.json();
            }).then(function (data) {
                if (!data) return;
                if (data.available === false) {
                    stopped = true;
                    return;
                }
                handled = text;
                // The user kept typing: this answer is for text that is gone.
                if (input.value.replace(/\s+/g, ' ').trim() !== text) return;
                if (!data.has_date) {
                    hideChip();
                    if (dateMode) say('No date found.');
                    return;
                }
                result = data;
                label.textContent = data.label;
                chip.title = data.matched_text || '';
                chip.hidden = false;
                say('');
            }).catch(function () {
                // Aborted by newer input, or the network failed: no chip, no message.
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
    }

    htmx.onLoad(function (root) {
        var scope = root.querySelectorAll ? root : document;
        scope.querySelectorAll('[data-title-parse]').forEach(setup);
        if (root.matches && root.matches('[data-title-parse]')) setup(root);
    });
})();
