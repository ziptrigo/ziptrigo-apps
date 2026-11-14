/*
 * World Time (apps/world_time). Loaded by world_time/layout.html as a classic script, so the
 * `alpine:init` listener below exists before Alpine (deferred) starts.
 *
 *  - `worldTimeGrid` (Alpine): one per rendered grid (world_time/partials/grid.html; htmx swaps a new
 *    one in after every change to the rows). It reads the JSON the server put in the grid -- the
 *    first instant of the day shown, the number of hour tiles, and per row the UTC offset (minutes)
 *    at each tile's start -- and does only arithmetic with it: the server computed every zone, so
 *    the page can't disagree with it. It draws the selected range and the "now" line, keeps each
 *    row's clock current, and writes the selected range in every location's local time.
 *
 *    A selection is two numbers, minutes from the first instant (`a` < `b`). Clicking a tile
 *    selects the hour from the half hour clicked; dragging (mouse or pen) selects a range, snapped
 *    to 30 minutes; the Start and End fields set an exact minute on the home row's clock; the arrow
 *    keys move it. It survives a re-render of the grid (kept on `window` as two instants).
 *
 *  - The "Add a location" search: Enter picks the first suggestion, the arrow keys walk them,
 *    Escape closes them, and they are cleared once a place was added.
 *  - The day picker: choosing a date opens that day.
 *  - Reordering rows: core's `initSortableList`, posting the new order.
 *
 * Dates and times are written with `ZTDateTime` (core/js/datetime_format.js): the account's formats.
 */
(function () {
    'use strict';

    const MINUTE = 60000;
    const SNAP = 30;

    function pad(n) { return String(n).padStart(2, '0'); }

    document.addEventListener('alpine:init', function () {
        Alpine.data('worldTimeGrid', function () {
            return {
                start: 0,
                hours: 24,
                rows: [],
                a: null,
                b: null,
                anchor: null,
                dragging: false,
                startTime: '',
                endTime: '',
                syncing: false,
                now: Date.now(),
                tileWidth: 40,
                tilesLeft: 0,
                announcement: '',
                timer: null,

                init() {
                    const script = this.$root.querySelector('script[type="application/json"]');
                    const data = script ? JSON.parse(script.textContent) : {};
                    this.start = data.start || 0;
                    this.hours = data.hours || 24;
                    this.rows = data.rows || [];
                    if (!this.rows.length) return;
                    this.$nextTick(() => {
                        this.measure();
                        this.restore();
                        this.scrollToStart();
                        this.tick();
                    });
                    this.timer = setInterval(() => this.tick(), 20000);
                    this._onResize = () => this.measure();
                    window.addEventListener('resize', this._onResize);
                    this.$watch('startTime', (value) => this.fromField('start', value));
                    this.$watch('endTime', (value) => this.fromField('end', value));
                },

                destroy() {
                    clearInterval(this.timer);
                    window.removeEventListener('resize', this._onResize);
                },

                // -- geometry --------------------------------------------------------------------

                get total() { return this.hours * 60; },

                measure() {
                    const strip = this.$root.querySelector('[data-wt-tiles]');
                    if (!strip || !strip.firstElementChild) return;
                    this.tileWidth = strip.firstElementChild.getBoundingClientRect().width || 40;
                    this.tilesLeft = strip.offsetLeft;
                },

                x(minutes) { return this.tilesLeft + (minutes / 60) * this.tileWidth; },

                minutesAt(event) {
                    const strip = this.$root.querySelector('[data-wt-tiles]');
                    const left = strip.getBoundingClientRect().left;
                    const raw = ((event.clientX - left) / this.tileWidth) * 60;
                    return Math.max(0, Math.min(this.total, raw));
                },

                scrollToStart() {
                    const scroller = this.$refs.scroller;
                    if (!scroller || scroller.scrollWidth <= scroller.clientWidth) return;
                    // Bring "now" (or, on another day, the morning) into view on a narrow screen.
                    const minutes = this.nowVisible ? (this.now - this.start) / MINUTE : 8 * 60;
                    const visible = scroller.clientWidth - this.tilesLeft;
                    scroller.scrollLeft = Math.max(0, (minutes / 60) * this.tileWidth - visible / 3);
                },

                // -- time arithmetic -------------------------------------------------------------

                // The wall clock of row `row` at `minutes` from the start: {y, m, d, hour, minute}.
                wall(row, minutes) {
                    const index = Math.min(this.hours, Math.max(0, Math.floor(minutes / 60)));
                    const offset = row.offsets[index];
                    const shifted = new Date(this.start + minutes * MINUTE + offset * MINUTE);
                    return {
                        y: shifted.getUTCFullYear(),
                        m: shifted.getUTCMonth() + 1,
                        d: shifted.getUTCDate(),
                        hour: shifted.getUTCHours(),
                        minute: shifted.getUTCMinutes(),
                    };
                },

                get home() {
                    return this.rows.find(function (row) { return row.home; }) || this.rows[0];
                },

                get homeTitle() { return this.home ? this.home.title : ''; },

                timeText(wall) { return window.ZTDateTime.formatTime(wall.hour, wall.minute); },

                dateText(wall) { return window.ZTDateTime.formatDate(wall.y, wall.m, wall.d, 'weekday'); },

                rangeText(row) {
                    const from = this.wall(row, this.a);
                    const to = this.wall(row, this.b);
                    const sameDay = from.y === to.y && from.m === to.m && from.d === to.d;
                    return this.dateText(from) + ', ' + this.timeText(from) + ' \u2013 ' +
                        (sameDay ? '' : this.dateText(to) + ', ') + this.timeText(to);
                },

                // -- the selection ---------------------------------------------------------------

                get hasSelection() { return this.a !== null && this.b !== null && this.b > this.a; },

                get selectionStyle() {
                    if (!this.hasSelection) return {};
                    return { left: this.x(this.a) + 'px', width: (this.x(this.b) - this.x(this.a)) + 'px' };
                },

                get lines() {
                    if (!this.hasSelection) return [];
                    return this.rows.map((row) => ({ id: row.id, title: row.title, range: this.rangeText(row) }));
                },

                get durationText() {
                    if (!this.hasSelection) return '';
                    const minutes = Math.round(this.b - this.a);
                    const hours = Math.floor(minutes / 60);
                    const rest = minutes % 60;
                    return [hours ? hours + ' h' : '', rest ? rest + ' min' : ''].filter(Boolean).join(' ');
                },

                get summaryText() {
                    return this.lines.map(function (line) { return line.title + ': ' + line.range; }).join('\n');
                },

                select(a, b, announce) {
                    a = Math.max(0, Math.min(this.total, a));
                    b = Math.max(0, Math.min(this.total, b));
                    if (b <= a) return;
                    this.a = a;
                    this.b = b;
                    this.syncFields();
                    window.__worldTimeSelection = { from: this.start + a * MINUTE, to: this.start + b * MINUTE };
                    if (announce !== false) {
                        this.announcement = 'Selected ' + this.rangeText(this.home) + ', ' + this.homeTitle + ' time.';
                    }
                },

                clear() {
                    this.a = this.b = null;
                    window.__worldTimeSelection = null;
                    this.syncFields();
                    this.announcement = 'Selection cleared.';
                },

                // A grid swapped in after a change (or the same day opened again) keeps the range.
                restore() {
                    const kept = window.__worldTimeSelection;
                    if (!kept) return;
                    const a = (kept.from - this.start) / MINUTE;
                    const b = (kept.to - this.start) / MINUTE;
                    if (a >= 0 && b <= this.total && b > a) this.select(a, b, false);
                },

                syncFields() {
                    this.syncing = true;
                    if (this.hasSelection) {
                        const from = this.wall(this.home, this.a);
                        const to = this.wall(this.home, this.b);
                        this.startTime = pad(from.hour) + ':' + pad(from.minute);
                        this.endTime = pad(to.hour) + ':' + pad(to.minute);
                    } else {
                        this.startTime = this.endTime = '';
                    }
                    this.$nextTick(() => { this.syncing = false; });
                },

                // The minute of the day shown at which the home clock reads `value` (HH:MM), the
                // first one after `after`; null when the day has none.
                minuteOf(value, after) {
                    for (let minute = Math.floor(after) + 1; minute <= this.total; minute += 1) {
                        const wall = this.wall(this.home, minute);
                        if (pad(wall.hour) + ':' + pad(wall.minute) === value) return minute;
                    }
                    return null;
                },

                fromField(which, value) {
                    if (this.syncing || !this.hasSelection || !/^\d\d:\d\d$/.test(value || '')) return;
                    if (which === 'start') {
                        const a = this.minuteOf(value, -1);
                        if (a === null) return this.syncFields();
                        const length = this.b - this.a;
                        this.select(a, a < this.b ? this.b : Math.min(this.total, a + length));
                    } else {
                        const b = this.minuteOf(value, this.a);
                        if (b === null) return this.syncFields();
                        this.select(this.a, b);
                    }
                },

                // -- pointer and keyboard --------------------------------------------------------

                snap(minutes) { return Math.round(minutes / SNAP) * SNAP; },

                down(event) {
                    if (event.button !== 0 || !event.target.closest('[data-wt-tiles]')) return;
                    this.measure();
                    this.anchor = Math.floor(this.minutesAt(event) / SNAP) * SNAP;
                    // Touch scrolls the grid; only a mouse or a pen drags a range. A tap still selects.
                    this.dragging = event.pointerType !== 'touch';
                    this.moved = false;
                    if (this.dragging) {
                        try { this.$refs.canvas.setPointerCapture(event.pointerId); } catch (_) { /* not capturable */ }
                    }
                },

                move(event) {
                    if (!this.dragging || this.anchor === null) return;
                    const at = this.snap(this.minutesAt(event));
                    if (at === this.anchor) return;
                    this.moved = true;
                    this.select(Math.min(this.anchor, at), Math.max(this.anchor, at), false);
                },

                up(event) {
                    if (this.anchor === null) return;
                    if (event.type === 'pointercancel') { this.anchor = null; this.dragging = false; return; }
                    if (!this.moved) this.select(this.anchor, this.anchor + 60);
                    else if (this.hasSelection) this.select(this.a, this.b);
                    this.anchor = null;
                    this.dragging = false;
                },

                onKey(event) {
                    if (event.target !== this.$refs.scroller) return;
                    const step = event.key === 'ArrowLeft' ? -SNAP : event.key === 'ArrowRight' ? SNAP : 0;
                    if (event.key === 'Escape' && this.hasSelection) {
                        this.clear();
                    } else if ((event.key === 'Enter' || event.key === ' ' || step) && !this.hasSelection) {
                        const from = this.nowVisible
                            ? Math.floor((this.now - this.start) / MINUTE / 60) * 60
                            : Math.min(9 * 60, this.total - 60);
                        this.select(from, from + 60);
                    } else if (step && event.shiftKey) {
                        if (this.b + step > this.a) this.select(this.a, this.b + step);
                    } else if (step) {
                        if (this.a + step >= 0 && this.b + step <= this.total) this.select(this.a + step, this.b + step);
                    } else {
                        return;
                    }
                    event.preventDefault();
                    this.reveal();
                },

                // Keep the selection in view when the keyboard moves it.
                reveal() {
                    const scroller = this.$refs.scroller;
                    if (!scroller || !this.hasSelection) return;
                    const left = this.x(this.a);
                    const right = this.x(this.b);
                    if (left - this.tilesLeft < scroller.scrollLeft) scroller.scrollLeft = left - this.tilesLeft;
                    else if (right > scroller.scrollLeft + scroller.clientWidth) scroller.scrollLeft = right - scroller.clientWidth;
                },

                // The rows were dragged into a new order: the lines of a selected range follow it.
                reordered(ids) {
                    const place = new Map(ids.map(function (id, index) { return [id, index]; }));
                    this.rows = this.rows.slice().sort(function (one, other) {
                        return (place.get(one.id) || 0) - (place.get(other.id) || 0);
                    });
                },

                // -- now -------------------------------------------------------------------------

                get nowVisible() {
                    return this.rows.length > 0 && this.now >= this.start && this.now < this.start + this.total * MINUTE;
                },

                get nowStyle() {
                    return this.nowVisible ? { left: this.x((this.now - this.start) / MINUTE) + 'px' } : {};
                },

                tick() {
                    this.now = Date.now();
                    this.rows.forEach((row) => {
                        const shifted = new Date(this.now + row.nowOffset * MINUTE);
                        const clock = this.$root.querySelector('[data-wt-clock="' + row.id + '"]');
                        const day = this.$root.querySelector('[data-wt-clock-date="' + row.id + '"]');
                        if (clock) clock.textContent = window.ZTDateTime.formatTime(shifted.getUTCHours(), shifted.getUTCMinutes());
                        if (day) {
                            day.textContent = window.ZTDateTime.formatDate(
                                shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, shifted.getUTCDate(), 'weekday');
                        }
                    });
                },
            };
        });
    });

    // -- the "Add a location" search -----------------------------------------------------------

    function suggestions(form) {
        return Array.from(form.querySelectorAll('[data-wt-suggestion]'));
    }

    document.addEventListener('keydown', function (event) {
        const form = event.target.closest ? event.target.closest('[data-wt-search]') : null;
        if (!form) return;
        const input = form.querySelector('input[type="search"]');
        const items = suggestions(form);
        const index = items.indexOf(event.target);
        if (event.key === 'Escape') {
            const results = form.querySelector('#wt-search-results');
            if (results && results.childElementCount) {
                results.replaceChildren();
                input.focus();
                event.preventDefault();
            }
        } else if (event.target === input && event.key === 'Enter') {
            // Enter in the box picks the first suggestion; with none there is nothing to add.
            event.preventDefault();
            if (items.length) items[0].click();
        } else if (event.key === 'ArrowDown' && items.length) {
            event.preventDefault();
            items[Math.min(items.length - 1, index + 1)].focus();
        } else if (event.key === 'ArrowUp' && index !== -1) {
            event.preventDefault();
            (index === 0 ? input : items[index - 1]).focus();
        }
    });

    // Suggestions close when the focus leaves the search.
    document.addEventListener('focusin', function (event) {
        document.querySelectorAll('[data-wt-search]').forEach(function (form) {
            if (form.contains(event.target)) return;
            const results = form.querySelector('#wt-search-results');
            if (results) results.replaceChildren();
        });
    });

    document.addEventListener('htmx:afterRequest', function (event) {
        const form = event.target.closest ? event.target.closest('[data-wt-search]') : null;
        if (!form || event.target !== form || !event.detail.successful) return;
        const input = form.querySelector('input[type="search"]');
        input.value = '';
        input.focus();
    });

    // -- the day picker ------------------------------------------------------------------------

    document.addEventListener('change', function (event) {
        const holder = event.target.closest ? event.target.closest('[data-wt-date]') : null;
        if (!holder || event.target.type !== 'hidden' || !/^\d{4}-\d\d-\d\d$/.test(event.target.value)) return;
        window.location.assign(holder.dataset.url + '?date=' + encodeURIComponent(event.target.value));
    });

    // -- reordering rows -----------------------------------------------------------------------

    function showProblem(list, html) {
        const slot = document.querySelector(list.dataset.msg || '#wt-msg');
        if (slot) slot.innerHTML = html;
    }

    function postOrder(list) {
        const body = new URLSearchParams();
        const ids = [];
        list.querySelectorAll(':scope > [data-sort-id]').forEach(function (row) {
            body.append('ids[]', row.dataset.sortId);
            ids.push(row.dataset.sortId);
        });
        // `HX-Request` makes the view answer a refusal with its message partial.
        fetch(list.dataset.reorderUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken') || '',
                'HX-Request': 'true',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: body,
            credentials: 'same-origin',
        }).then(function (response) {
            if (!response.ok) return response.text().then(function (html) { showProblem(list, html); });
            showProblem(list, '');
            // The grid's own data (the lines of a selected range) follows the new order.
            list.dispatchEvent(new CustomEvent('wt-reordered', { bubbles: true, detail: { ids: ids } }));
        }).catch(function () {
            showProblem(list, '<p class="text-red-600 dark:text-red-400" role="alert">Could not save the new order.</p>');
        });
    }

    function makeSortable(list) {
        if (typeof window.initSortableList !== 'function') return;
        window.initSortableList(list, {
            handle: '.drag-handle',
            item: '[data-sort-id]',
            label: function (handle) { return handle.dataset.name || 'Location'; },
            onReorder: postOrder,
        });
        // For the browser tests: the rows can be moved from here on (a swapped-in grid is wired a
        // moment after it appears).
        list.dataset.sortableReady = '1';
    }

    // htmx is loaded before this script; SortableJS and the helper are deferred, so wait for them.
    document.addEventListener('DOMContentLoaded', function () {
        htmx.onLoad(function (root) {
            const scope = root.querySelectorAll ? root : document;
            scope.querySelectorAll('.wt-grid [data-sortable]').forEach(makeSortable);
        });
        document.querySelectorAll('.wt-grid [data-sortable]').forEach(makeSortable);
    });
})();
