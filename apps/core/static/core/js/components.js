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
                // A small pop on whatever element is marked `data-copy-icon` (the check that replaces the copy glyph).
                const icon = this.$root.querySelector('[data-copy-icon]');
                if (icon) {
                    icon.classList.remove('animate-pop');
                    void icon.offsetWidth; // restart the animation on a second copy
                    icon.classList.add('animate-pop');
                }
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

    // Where focus goes back to when the confirmation modal closes: the trigger, unless it is a menu item (its
    // menu closes when it is clicked) or is no longer shown. Then the control that opened the hidden part: the
    // nearest focusable element before it (a row menu's button), or nothing.
    const FOCUSABLE = 'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';
    function shown(el) {
        return el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null;
    }
    function openerBefore(el) {
        for (let sibling = el.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
            const control = sibling.matches(FOCUSABLE) ? sibling : sibling.querySelector(FOCUSABLE);
            if (control && shown(control)) return control;
        }
        return null;
    }
    function returnFocusTo(trigger) {
        if (!trigger || !trigger.isConnected) return null;
        // Decided by the markup, not by visibility: the menu may still be fading out.
        const menu = trigger.closest('[role="menu"]');
        if (menu) return openerBefore(menu) || null;
        if (shown(trigger)) return trigger;
        let hidden = trigger;
        while (hidden.parentElement && !shown(hidden.parentElement)) hidden = hidden.parentElement;
        return openerBefore(hidden);
    }

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
                // Not once it has closed again: a quick Escape can come before this runs.
                this.$nextTick(() => { if (this.open && this.$refs.cancel) this.$refs.cancel.focus(); });
            },
            close() {
                this.open = false;
                const target = returnFocusTo(this.trigger);
                if (target) target.focus();
            },
        };
    });
});

// The bell's badge pops when the number goes up. The badge replaces itself every 60 s (and is swapped out
// of band by other responses), so compare the count it carries with the last one seen.
(function () {
    let last = null;
    function badgeCount() {
        const badge = document.getElementById('notification-badge');
        return badge ? parseInt(badge.dataset.count || '0', 10) : null;
    }
    function check() {
        const now = badgeCount();
        if (now === null) return;
        if (last !== null && now > last) {
            const badge = document.getElementById('notification-badge');
            badge.classList.remove('animate-pop');
            void badge.offsetWidth;
            badge.classList.add('animate-pop');
        }
        last = now;
    }
    document.addEventListener('DOMContentLoaded', function () { last = badgeCount(); });
    document.addEventListener('htmx:afterSwap', check);
    document.addEventListener('htmx:oobAfterSwap', check);
})();

// Keeps keyboard focus when htmx replaces the content of a container marked `data-keep-focus` (the
// notification dropdown and list): the focused control is swapped out (a dismissed item, a "Mark all as
// read" that is no longer needed), which would drop focus to <body>. After the swap focus goes to the
// control now at the same place in the container, or the last one, or the container itself.
(function () {
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const origin = new WeakMap(); // container -> the control the request came from and its place among the container's controls
    const pending = new WeakMap(); // container -> its place in the container's controls
    // The request is read before hx-disabled-elt disables the button (a disabled control loses focus).
    // The control is the focused one inside the container, else the submit button of the form that sent it.
    document.addEventListener('htmx:beforeRequest', function (event) {
        const elt = event.detail.elt;
        const container = elt && elt.closest && elt.closest('[data-keep-focus]');
        if (!container) return;
        const active = document.activeElement;
        const from = active && container.contains(active) && active !== container
            ? active : (elt.matches('button, a') ? elt : elt.querySelector('button, [type=submit]'));
        if (from) origin.set(container, { from, index: Array.from(container.querySelectorAll(FOCUSABLE)).indexOf(from) });
    });
    document.addEventListener('htmx:beforeSwap', function (event) {
        const target = event.detail.target;
        if (!target || !target.hasAttribute('data-keep-focus') || !origin.has(target)) return;
        const { from, index } = origin.get(target);
        origin.delete(target);
        if (!target.contains(from)) return;
        // Only when the person was on that control (not when they have moved on while it loaded).
        const active = document.activeElement;
        if (active && active !== document.body && active !== from) return;
        pending.set(target, index);
    });
    document.addEventListener('htmx:afterSettle', function (event) {
        const target = event.detail.target;
        if (!target || !pending.has(target)) return;
        const index = pending.get(target);
        pending.delete(target);
        const active = document.activeElement;
        if (active && active !== document.body) return;
        const controls = target.querySelectorAll(FOCUSABLE);
        const next = index >= 0 && controls.length ? controls[Math.min(index, controls.length - 1)] : null;
        if (next) {
            next.focus();
        } else {
            if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
            target.focus();
        }
    });
})();
