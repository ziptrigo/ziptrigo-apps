/*
 * Alpine components shared by every product's pages. Loaded deferred from `core/base.html`, ahead
 * of Alpine itself, so the `alpine:init` listener is registered before Alpine starts.
 */
document.addEventListener('alpine:init', function () {
    // A "Copy" button for the text in `x-ref="text"`.
    Alpine.data('copyText', function () {
        return {
            copied: false,
            timer: null,
            async copy() {
                const text = this.$refs.text.textContent.trim();
                try {
                    await navigator.clipboard.writeText(text);
                } catch (_) {
                    // Insecure origin or a refused permission: fall back to the old way.
                    const area = document.createElement('textarea');
                    area.value = text;
                    area.setAttribute('readonly', '');
                    area.style.position = 'fixed';
                    area.style.opacity = '0';
                    document.body.appendChild(area);
                    area.select();
                    try { document.execCommand('copy'); } catch (__) { /* nothing more to try */ }
                    document.body.removeChild(area);
                }
                this.copied = true;
                clearTimeout(this.timer);
                this.timer = setTimeout(() => { this.copied = false; }, 2000);
            },
        };
    });

    // The notification bell of `core/partials/notification_bell.html`. Opening the dropdown asks
    // the server for its content (an htmx POST on the panel, triggered by this event), which also
    // marks what it lists as read; closing it asks for nothing.
    Alpine.data('notificationBell', function () {
        return {
            open: false,
            toggle() {
                this.open = !this.open;
                if (this.open) htmx.trigger(this.$refs.panel, 'notifications-open');
            },
            close() {
                this.open = false;
            },
        };
    });

    // The confirmation modal of `core/partials/confirm_modal.html`. A trigger button carries
    // `data-url` (where the form posts), `data-title`, `data-body` and `data-label`, and optionally
    // `data-tone="primary"` (a non-destructive action; the default is the red "danger" button) and
    // `data-cost` (credits the action will charge: posted as `confirm_cost`, which the view
    // compares with what it is about to charge before it spends anything).
    Alpine.data('confirmModal', function () {
        return {
            open: false,
            url: '',
            title: '',
            body: '',
            label: 'Delete',
            tone: 'danger',
            cost: '',
            checkboxName: '',
            checkboxLabel: '',
            trigger: null,
            show(el) {
                const data = el.dataset;
                this.url = data.url;
                this.title = data.title;
                this.body = data.body;
                this.label = data.label || 'Delete';
                this.tone = data.tone || 'danger';
                this.cost = data.cost || '';
                // An optional checkbox (`data-checkbox-name` / `data-checkbox-label`), posted as
                // `<name>=1` when ticked (To-do: "Also stop repeating").
                this.checkboxName = data.checkboxName || '';
                this.checkboxLabel = data.checkboxLabel || '';
                this.trigger = el;
                this.open = true;
                this.$nextTick(() => { if (this.$refs.cancel) this.$refs.cancel.focus(); });
            },
            close() {
                this.open = false;
                if (this.trigger) this.trigger.focus();
            },
        };
    });
});
