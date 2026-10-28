/*
 * The "Repeat" control of the To-do task form (issue #179): a popover that builds an RFC 5545
 * RRULE value (`FREQ=MONTHLY;BYMONTHDAY=1`) and the repeat mode into the hidden inputs `rrule` and
 * `recurrence_mode`. The server validates and canonicalises them (`services.recurrence.parse_rule`);
 * this script only has to produce a rule the server accepts, in the same canonical order:
 * FREQ, INTERVAL, BYDAY, BYMONTHDAY, BYMONTH, COUNT or UNTIL.
 *
 * Loaded as a classic script (not deferred) so the `alpine:init` listener exists before Alpine
 * starts. The pure functions (`build`, `parse`, `summary`) are also exported for the Node check
 * in tests (`module.exports`), which is not loaded by the site.
 */
(function () {
    'use strict';

    var WEEKDAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
    var WEEKDAY_NAMES = {
        MO: 'Monday', TU: 'Tuesday', WE: 'Wednesday', TH: 'Thursday', FR: 'Friday',
        SA: 'Saturday', SU: 'Sunday',
    };
    var UNITS = { DAILY: 'day', WEEKLY: 'week', MONTHLY: 'month', YEARLY: 'year' };
    var FREQS = { day: 'DAILY', week: 'WEEKLY', month: 'MONTHLY', year: 'YEARLY' };
    var MONTHS = [
        'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
        'October', 'November', 'December',
    ];

    function defaults() {
        return {
            freq: '',            // '', 'day', 'week', 'month', 'year'
            interval: 1,
            days: [],            // weekday codes, for week
            monthKind: 'day',    // 'same' (the date's own day), 'day', 'last' or 'nth', for month
            monthDay: 1,
            nth: '1',            // '1'..'4' or '-1'
            nthDay: 'MO',
            yearKind: 'date',    // 'same' (the date's own day) or 'date', for year
            yearMonth: 1,
            yearDay: 1,
            ends: 'never',       // 'never', 'count' or 'date'
            count: 5,
            until: '',           // YYYY-MM-DD
            mode: 'fixed',
        };
    }

    function whole(value, low, high, fallback) {
        var number = parseInt(value, 10);
        if (isNaN(number)) return fallback;
        return Math.min(high, Math.max(low, number));
    }

    // The RRULE value for a state, '' for "does not repeat".
    function build(state) {
        if (!state.freq) return '';
        var parts = ['FREQ=' + FREQS[state.freq]];
        var interval = whole(state.interval, 1, 365, 1);
        if (interval > 1) parts.push('INTERVAL=' + interval);
        if (state.freq === 'week' && state.days.length) {
            var days = WEEKDAYS.filter(function (code) { return state.days.indexOf(code) !== -1; });
            parts.push('BYDAY=' + days.join(','));
        }
        if (state.freq === 'month') {
            if (state.monthKind === 'last') parts.push('BYMONTHDAY=-1');
            else if (state.monthKind === 'nth') parts.push('BYDAY=' + state.nth + state.nthDay);
            else if (state.monthKind === 'day') parts.push('BYMONTHDAY=' + whole(state.monthDay, 1, 31, 1));
        }
        if (state.freq === 'year' && state.yearKind === 'date') {
            parts.push('BYMONTHDAY=' + whole(state.yearDay, 1, 31, 1));
            parts.push('BYMONTH=' + whole(state.yearMonth, 1, 12, 1));
        }
        if (state.ends === 'count') parts.push('COUNT=' + whole(state.count, 1, 1000, 1));
        else if (state.ends === 'date' && state.until) parts.push('UNTIL=' + state.until.replace(/-/g, ''));
        return parts.join(';');
    }

    // The state for a canonical rule, or null when the controls cannot express it exactly (the
    // popover then keeps the rule as it is until the user changes something).
    function parse(rule) {
        var state = defaults();
        if (!rule) return state;
        var parts = {};
        rule.split(';').forEach(function (segment) {
            var at = segment.indexOf('=');
            if (at > 0) parts[segment.slice(0, at)] = segment.slice(at + 1);
        });
        var freq = Object.keys(FREQS).filter(function (key) { return FREQS[key] === parts.FREQ; })[0];
        if (!freq) return null;
        state.freq = freq;
        if (parts.INTERVAL) state.interval = whole(parts.INTERVAL, 1, 365, 1);
        if (parts.BYDAY) {
            if (freq === 'week') {
                state.days = parts.BYDAY.split(',');
            } else if (freq === 'month') {
                var match = /^(-1|[1-4])(MO|TU|WE|TH|FR|SA|SU)$/.exec(parts.BYDAY);
                if (!match) return null;
                state.monthKind = 'nth';
                state.nth = match[1];
                state.nthDay = match[2];
            }
        }
        if (freq === 'month' && parts.BYMONTHDAY) {
            if (parts.BYMONTHDAY === '-1') state.monthKind = 'last';
            else state.monthDay = whole(parts.BYMONTHDAY, 1, 31, 1);
        } else if (freq === 'month' && !parts.BYDAY) {
            state.monthKind = 'same';
        }
        if (freq === 'year') {
            if (parts.BYMONTHDAY) state.yearDay = whole(parts.BYMONTHDAY, 1, 31, 1);
            if (parts.BYMONTH) state.yearMonth = whole(parts.BYMONTH, 1, 12, 1);
            if (!parts.BYMONTHDAY && !parts.BYMONTH) state.yearKind = 'same';
        }
        if (parts.COUNT) {
            state.ends = 'count';
            state.count = whole(parts.COUNT, 1, 1000, 1);
        } else if (parts.UNTIL && /^\d{8}$/.test(parts.UNTIL)) {
            state.ends = 'date';
            state.until = parts.UNTIL.slice(0, 4) + '-' + parts.UNTIL.slice(4, 6) + '-' + parts.UNTIL.slice(6);
        }
        return build(state) === rule ? state : null;
    }

    function ordinalDay(number) {
        var last = number % 100;
        var suffix = last >= 10 && last <= 20 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[number % 10] || 'th';
        return number + suffix;
    }

    // A short sentence for the button, in step with the server's `describe` for these rules.
    function summary(state) {
        if (!state.freq) return 'Repeat';
        var interval = whole(state.interval, 1, 365, 1);
        var text = 'Every ' + (interval > 1 ? interval + ' ' + UNITS[FREQS[state.freq]] + 's' : UNITS[FREQS[state.freq]]);
        if (state.freq === 'week' && state.days.length) {
            text += ' on ' + WEEKDAYS.filter(function (code) { return state.days.indexOf(code) !== -1; })
                .map(function (code) { return WEEKDAY_NAMES[code]; }).join(', ');
        }
        if (state.freq === 'month') {
            if (state.monthKind === 'last') text += ' on the last day';
            else if (state.monthKind === 'nth') {
                var words = { '1': 'first', '2': 'second', '3': 'third', '4': 'fourth', '-1': 'last' };
                text += ' on the ' + words[state.nth] + ' ' + WEEKDAY_NAMES[state.nthDay];
            } else if (state.monthKind === 'day') text += ' on the ' + ordinalDay(whole(state.monthDay, 1, 31, 1));
        }
        if (state.freq === 'year' && state.yearKind === 'date') {
            text += ' on ' + whole(state.yearDay, 1, 31, 1) + ' ' + MONTHS[whole(state.yearMonth, 1, 12, 1) - 1];
        }
        if (state.ends === 'count') text += ', ' + whole(state.count, 1, 1000, 1) + ' times';
        else if (state.ends === 'date' && state.until) text += ', until ' + state.until;
        return text;
    }

    var api = { build: build, parse: parse, summary: summary, defaults: defaults };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (typeof document === 'undefined') return;

    document.addEventListener('alpine:init', function () {
        // One per task form, on the element carrying `data-rrule` (the rule the task has, '' for
        // none) and `data-mode`. The controls have no `name`: the form submits only the two
        // hidden inputs, bound to `rrule` and `mode` below.
        Alpine.data('repeatControl', function () {
            var component = {
                open: false,
                customRule: '',   // a rule the controls cannot express, kept until they are used
                init() {
                    var rule = this.$el.dataset.rrule || '';
                    var state = parse(rule);
                    if (state === null) {
                        this.customRule = rule;
                        state = defaults();
                    }
                    Object.assign(this, state);
                    this.mode = this.$el.dataset.mode === 'from_completion' ? 'from_completion' : 'fixed';
                },
                touch() { this.customRule = ''; },
                // Used by the title chip (title_parse.js): show `rule` (a canonical RRULE value)
                // in the controls and submit it. The mode is left as it is.
                setRule(rule) {
                    var state = parse(rule);
                    if (state === null) {
                        this.customRule = rule;
                        return;
                    }
                    this.customRule = '';
                    var self = this;
                    Object.keys(state).forEach(function (key) {
                        if (key !== 'mode') self[key] = state[key];
                    });
                },
                get rrule() { return this.customRule || build(this); },
                get label() { return this.customRule ? 'Custom repeat' : summary(this); },
                toggleDay(code) {
                    this.touch();
                    var at = this.days.indexOf(code);
                    if (at === -1) this.days.push(code); else this.days.splice(at, 1);
                },
                clear() {
                    this.touch();
                    this.freq = '';
                    this.open = false;
                },
            };
            // Not Object.assign/spread onto the object above: they would call the getters.
            var state = defaults();
            Object.keys(state).forEach(function (key) { component[key] = state[key]; });
            return component;
        });
    });
})();
