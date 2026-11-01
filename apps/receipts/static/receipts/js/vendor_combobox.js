/*
 * The vendor field of a receipt's page (`receipts/partials/vendor_field.html`): an ARIA combobox over
 * the user's own vendors.
 *
 * The input's own htmx attributes fetch the options (`GET /receipts/vendors/suggest/`) into the
 * listbox; this component only handles the state around them: the text, the id of a picked vendor
 * (sent as `vendor_id`, and cleared as soon as the text is edited, so the form never submits an id
 * for a name that was typed over), keyboard navigation, and what depends on the change (the hint
 * that the name the AI read will be taught to the chosen vendor).
 *
 * Loaded as a classic script (not deferred), so the `alpine:init` listener exists before Alpine
 * starts.
 */
document.addEventListener('alpine:init', function () {
    Alpine.data('vendorCombobox', function () {
        return {
            name: '',
            vendorId: '',
            original: '',
            originalId: '',
            extracted: '',
            open: false,
            // Escape closed the list: suggestions still in flight must not reopen it until the
            // text changes or the arrows ask for it.
            dismissed: false,
            active: -1,
            activeId: '',

            init() {
                const data = this.$el.dataset;
                this.name = data.name || '';
                this.original = this.name.trim();
                this.vendorId = data.vendorId || '';
                this.originalId = this.vendorId;
                this.extracted = data.extracted || '';
            },

            // The user moved this receipt to another vendor (not saved yet).
            get changed() {
                return this.name.trim() !== this.original
                    || (this.vendorId !== '' && this.vendorId !== this.originalId);
            },

            // Saving will teach the name the AI read to the chosen vendor. (The hint's markup is
            // left out by the server when a vetted vendor is called that, which keeps the name.)
            get showHint() {
                return this.changed && this.extracted !== '' && this.name.trim() !== '';
            },

            options() {
                return Array.from(this.$refs.list.querySelectorAll('[role="option"]'));
            },

            // Typing turns a picked vendor back into a plain name.
            edited() {
                this.vendorId = '';
                this.dismissed = false;
            },

            // The listbox was just filled by htmx.
            onSwap() {
                this.setActive(-1);
                this.open = !this.dismissed
                    && this.options().length > 0
                    && document.activeElement === this.$refs.input;
            },

            close() {
                this.open = false;
                this.setActive(-1);
            },

            pick(option) {
                this.name = option.dataset.vendorName || '';
                this.vendorId = option.dataset.vendorId || '';
                this.close();
                this.$refs.input.focus();
            },

            activate(option) {
                this.setActive(this.options().indexOf(option));
            },

            setActive(index) {
                const options = this.options();
                this.active = index;
                this.activeId = '';
                options.forEach((option, position) => {
                    const on = position === index;
                    option.setAttribute('aria-selected', on ? 'true' : 'false');
                    option.classList.toggle('bg-sage-50', on);
                    option.classList.toggle('dark:bg-gray-700', on);
                    if (on) {
                        this.activeId = option.id;
                        option.scrollIntoView({ block: 'nearest' });
                    }
                });
            },

            onKeydown(event) {
                const options = this.options();
                switch (event.key) {
                    case 'ArrowDown':
                    case 'ArrowUp': {
                        if (options.length === 0) return;
                        event.preventDefault();
                        this.dismissed = false;
                        this.open = true;
                        const step = event.key === 'ArrowDown' ? 1 : -1;
                        this.setActive((this.active + step + options.length) % options.length);
                        break;
                    }
                    case 'Enter':
                        // Picks the highlighted option; otherwise Enter saves the form as usual.
                        if (this.open && this.active >= 0) {
                            event.preventDefault();
                            this.pick(options[this.active]);
                        }
                        break;
                    case 'Escape':
                        // Also when the list hasn't opened yet: a reply still on its way stays closed.
                        this.dismissed = true;
                        if (this.open) {
                            event.preventDefault();
                            event.stopPropagation();
                            this.close();
                        }
                        break;
                    case 'Tab':
                        this.close();
                        break;
                }
            },
        };
    });
});
