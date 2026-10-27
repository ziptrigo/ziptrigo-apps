/*
 * Alpine components specific to the Receipts pages (`copyText` and `confirmModal` are shared by
 * every product and live in `core/js/components.js`). Loaded as a classic script (not deferred)
 * so the `alpine:init` listener is registered before Alpine starts.
 */
document.addEventListener('alpine:init', function () {
    // A dialog that was fetched into a host element (the vendor rename and merge dialogs): the
    // server sends the whole overlay, which has this component on its root. Closing removes it,
    // and focus goes back to what opened it. A refused form is re-rendered inside it by htmx, so
    // the dialog stays open; a successful one redirects the page.
    Alpine.data('fragmentModal', function () {
        return {
            opener: null,
            init() {
                this.opener = document.activeElement;
                this.$nextTick(() => {
                    const target = this.$root.querySelector('[data-autofocus]')
                        || this.$root.querySelector('input:not([type="hidden"]), select, button');
                    if (target) target.focus();
                });
            },
            close() {
                const opener = this.opener;
                this.$root.remove();
                if (opener && document.contains(opener)) opener.focus();
            },
        };
    });
});
