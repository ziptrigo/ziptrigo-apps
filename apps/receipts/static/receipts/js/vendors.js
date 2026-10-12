/*
 * The Vendors page (`receipts/vendors.html`): which rows are ticked, for the bulk bar. The checkboxes
 * belong to a hidden form (`#vendor-bulk`, through their `form` attribute) that the bar's buttons
 * include in their requests, so the filter form around the list never sends them.
 *
 * Loaded as a classic script (not deferred), so the `alpine:init` listener exists before Alpine
 * starts.
 */
document.addEventListener('alpine:init', function () {
    Alpine.data('vendorSelection', function () {
        return {
            count: 0,
            boxes() {
                return Array.from(this.$root.querySelectorAll('input[type="checkbox"][name="ids"]'));
            },
            update() {
                this.count = this.boxes().filter((box) => box.checked).length;
            },
            clear() {
                this.boxes().forEach((box) => { box.checked = false; });
                this.update();
            },
        };
    });
});
