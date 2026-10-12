/*
 * Small Alpine components shared by the Receipts pages. Loaded as a classic script (not deferred)
 * so the `alpine:init` listener is registered before Alpine starts.
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

    // The confirmation modal of `receipts/partials/confirm_modal.html`. A trigger button carries
    // `data-url` (where the form posts), `data-title`, `data-body` and `data-label`, and optionally
    // `data-tone="primary"` (a non-destructive action; the default is the red "danger" button).
    Alpine.data('confirmModal', function () {
        return {
            open: false,
            url: '',
            title: '',
            body: '',
            label: 'Delete',
            tone: 'danger',
            trigger: null,
            show(el) {
                const data = el.dataset;
                this.url = data.url;
                this.title = data.title;
                this.body = data.body;
                this.label = data.label || 'Delete';
                this.tone = data.tone || 'danger';
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
