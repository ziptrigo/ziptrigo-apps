/*
 * The Usage tab's filter form (issue #347): one Alpine component, `usageFilters`.
 *
 * The form refreshes the report with htmx on every change (a GET, `hx-trigger="change delay:50ms"`),
 * so what it sends must mean what the person did:
 *  - Changing the period or the dates forgets the chosen grouping (the radios are unticked, so no
 *    `group` is sent) and the report suggests one for the new period.
 *  - The app chips are a positive list (`?app=receipts`). With every chip ticked the person has not
 *    filtered, but sending them all would leave out an app that only the new period has, so a change
 *    of period unticks them all (nothing sent = every app). A partial selection is kept.
 * The custom date fields show only for "Custom". Loaded as a classic script (not deferred), so its
 * `alpine:init` listener exists before Alpine starts.
 */
document.addEventListener('alpine:init', function () {
    Alpine.data('usageFilters', function (range) {
        return {
            range: range,
            onChange(event) {
                const name = event.target.name;
                if (name === 'range') this.range = event.target.value;
                if (!['range', 'date_from', 'date_to'].includes(name)) return;
                this.$el.querySelectorAll('input[name="group"]').forEach(function (radio) {
                    radio.checked = false;
                });
                const chips = Array.from(this.$el.querySelectorAll('input[name="app"]'));
                if (chips.every(function (chip) { return chip.checked; })) {
                    chips.forEach(function (chip) { chip.checked = false; });
                }
            },
        };
    });
});
