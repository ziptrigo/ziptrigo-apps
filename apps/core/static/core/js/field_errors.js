/*
 * Field errors: the message of a refused form shown under the field that failed (issue #219).
 *
 * A 422 errors partial (`{% field_errors source %}`, core/partials/field_errors.html) carries one hidden
 * `<li data-field-error="<input name>">` per message inside a `<ul data-field-errors>`. The message slot
 * above the form still says everything; this script adds, for each name that matches a control of the form
 * (the form the data sits in, else the form that sent the request, else the element `data-scope` names):
 *
 *   - a `<p id="...-error" class="mt-1.5 text-xs text-red-600 dark:text-red-400">` with the icon, directly
 *     under the field (after its wrapping label, or the `.relative` box around it, or the flex/grid row it
 *     sits in, so the label's accessible name stays the label's, an absolutely positioned button isn't
 *     pushed and the message is never one more cell of a row);
 *   - `aria-invalid="true"` on the control (site.css draws the red border and halo from it), and the message's
 *     id in front of its `aria-describedby`, so help text that was already there is kept and read after it.
 *
 * The message is not a live region: the slot above is what is announced, once. Focus: when the page
 * has no focus of its own on a field of the form (the swap dropped it, or it sits on the submit button), the
 * first invalid control gets it, so a person using a keyboard or a screen reader lands where the fix is,
 * and hears the label and the message. A person who is typing in a field of the form is never moved.
 *
 * A field's message goes away, with its attributes, the moment the person edits it; all of a form's go
 * when a new request from it starts (the answer brings the ones that still apply). A name with no control,
 * and every non-field error, stay only in the slot. Nothing here needs the answer to come from htmx: the
 * same markup in a full page (a plain POST re-rendered) is applied on DOMContentLoaded.
 *
 * A hidden input (a date picker's value, a UTC mirror of a `datetime-local`) is not what the person sees:
 * the first visible control in the same box takes its place. `data-error-for="<name>"` on any element picks
 * the control or widget by hand.
 */
(function () {
    var MARK = 'data-field-error-id'; // on a control: the id of the message under it
    var originals = new WeakMap(); // control -> its aria-describedby before ours
    var requestForm = null; // the form the latest htmx request came from

    function formOf(element) {
        if (!element) return null;
        if (element.tagName === 'FORM') return element;
        return element.form || (element.closest && element.closest('form'));
    }

    function isField(element) {
        return !!element && /^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName) &&
            !/^(hidden|button|submit|reset|image)$/.test(element.type);
    }

    function visible(element) {
        return !!element && element.offsetParent !== null;
    }

    // The controls the person fills in for an input `name`: the named controls, a hidden one swapped for
    // the visible control next to it.
    function controlsFor(form, name) {
        var explicit = form.querySelector('[data-error-for="' + name.replace(/"/g, '\\"') + '"]');
        if (explicit) return [explicit];
        var found = [];
        form.querySelectorAll('input, select, textarea').forEach(function (element) {
            if (element.name !== name) return;
            if (element.type !== 'hidden') {
                found.push(element);
                return;
            }
            var partner = element.id && form.querySelector('[data-utc-target="' + element.id + '"]');
            if (!partner && element.parentElement) {
                partner = element.parentElement.querySelector('input:not([type=hidden]), select, textarea, button');
            }
            if (partner && found.indexOf(partner) < 0) found.push(partner);
        });
        return found;
    }

    // Where the message goes: after the label that wraps the control, else after the `.relative` box
    // around it (an input with a button laid over it), else after the control itself; and never into a
    // row of siblings (an amount next to its currency, a group of radios): then after the row.
    function anchorOf(control, form) {
        var anchor = control;
        var label = control.closest('label');
        var parent = control.parentElement;
        if (label && form.contains(label)) {
            anchor = label;
        } else if (parent && parent !== form && parent.matches('.relative, [data-field-wrap]')) {
            anchor = parent;
        }
        while (anchor.parentElement && anchor.parentElement !== form && inRow(anchor.parentElement)) {
            anchor = anchor.parentElement;
        }
        return anchor;
    }

    // A container that lays its children side by side (a flex row, a grid), where a new child would
    // become one more cell instead of a line under the field.
    function inRow(element) {
        var style = getComputedStyle(element);
        if (/grid/.test(style.display)) return true;
        return /flex/.test(style.display) && /^row/.test(style.flexDirection);
    }

    function uniqueId(base) {
        var id = base + '-error';
        for (var n = 2; document.getElementById(id); n++) id = base + '-error-' + n;
        return id;
    }

    function build(id, messages) {
        var p = document.createElement('p');
        p.id = id;
        p.className = 'mt-1.5 text-xs text-red-600 dark:text-red-400 animate-flash-in';
        p.setAttribute('data-field-error-message', '');
        var icon = document.createElement('i');
        icon.className = 'fas fa-circle-exclamation mr-1';
        icon.setAttribute('aria-hidden', 'true');
        p.appendChild(icon);
        messages.forEach(function (message, index) {
            if (index) p.appendChild(document.createElement('br'));
            p.appendChild(document.createTextNode(message));
        });
        return p;
    }

    function show(form, name, messages) {
        var controls = controlsFor(form, name);
        if (!controls.length) return;
        var last = controls[controls.length - 1];
        var anchor = anchorOf(last, form);
        var base = last.id || (form.id ? form.id + '-' : '') + name;
        var message = build(uniqueId(base), messages);

        // The message sits inside the bottom margin of its anchor (a label with `mb-4`), not below it:
        // adjacent vertical margins collapse, so the gap would otherwise stay the anchor's whole margin.
        var gap = parseFloat(getComputedStyle(anchor).marginBottom) || 0;
        if (gap > 6) {
            message.style.marginTop = 6 - gap + 'px';
            message.style.marginBottom = gap + 'px';
        }
        anchor.insertAdjacentElement('afterend', message);

        controls.forEach(function (control) {
            if (!originals.has(control)) originals.set(control, control.getAttribute('aria-describedby'));
            var before = originals.get(control);
            control.setAttribute('aria-invalid', 'true');
            control.setAttribute('aria-describedby', message.id + (before ? ' ' + before : ''));
            control.setAttribute(MARK, message.id);
        });
    }

    function clearControl(control) {
        var id = control.getAttribute(MARK);
        if (!id) return;
        var message = document.getElementById(id);
        if (message) message.remove();
        // Every control that shared this message (a radio group) lets go of it together.
        document.querySelectorAll('[' + MARK + '="' + id + '"]').forEach(function (other) {
            other.removeAttribute(MARK);
            other.removeAttribute('aria-invalid');
            var before = originals.get(other);
            if (before) other.setAttribute('aria-describedby', before);
            else other.removeAttribute('aria-describedby');
            originals.delete(other);
        });
    }

    function clearForm(form) {
        form.querySelectorAll('[' + MARK + ']').forEach(clearControl);
        form.querySelectorAll('[data-field-error-message]').forEach(function (message) { message.remove(); });
    }

    function shouldTakeFocus(form) {
        var active = document.activeElement;
        if (!active || active === document.body || active === document.documentElement) return true;
        if (!form.contains(active)) return false;
        return !isField(active);
    }

    // Show what the unprocessed `<ul data-field-errors>` in `root` carry. Idempotent: htmx fires its
    // events more than once for one swap, and a list is applied once.
    function apply(root) {
        root.querySelectorAll('ul[data-field-errors]:not([data-applied])').forEach(function (list) {
            list.setAttribute('data-applied', '');
            var form = (list.dataset.scope && document.getElementById(list.dataset.scope)) ||
                list.closest('form') || requestForm;
            if (!form || !document.contains(form)) return;

            clearForm(form);
            var byName = {};
            var order = [];
            list.querySelectorAll('li[data-field-error]').forEach(function (item) {
                var name = item.getAttribute('data-field-error');
                if (!byName[name]) { byName[name] = []; order.push(name); }
                byName[name].push(item.textContent.trim());
            });
            order.forEach(function (name) { show(form, name, byName[name]); });
            // The first invalid control in the page's order, not the order the server listed them in.
            var invalid = Array.prototype.filter.call(form.querySelectorAll('[aria-invalid="true"]'), function (c) {
                return visible(c) && !c.disabled;
            })[0];
            if (invalid && shouldTakeFocus(form)) invalid.focus();
        });
    }

    document.addEventListener('htmx:beforeRequest', function (event) {
        var form = formOf(event.detail.elt);
        if (!form) return;
        requestForm = form;
        // A request a field makes by itself (a live check) isn't a new attempt at the whole form.
        if (!isField(event.detail.elt)) clearForm(form);
    });
    // After the swap has settled: the old form is gone by then, so ids stay unique.
    document.addEventListener('htmx:afterSettle', function () { apply(document); });
    document.addEventListener('DOMContentLoaded', function () { apply(document); });

    // The person is fixing it: the message and the attributes go as soon as the field is edited.
    function edited(event) {
        var target = event.target;
        if (!(target instanceof Element)) return;
        var control = target.closest('[' + MARK + ']');
        if (control) clearControl(control);
    }
    document.addEventListener('input', edited);
    document.addEventListener('change', edited);
    // A widget made of buttons (the date picker) changes its value without an input event.
    document.addEventListener('click', function (event) {
        var target = event.target;
        if (!(target instanceof Element)) return;
        var box = target.closest('.relative, [data-field-wrap]');
        if (!box) return;
        var control = box.querySelector('button[' + MARK + ']');
        if (control && control !== target.closest('button')) clearControl(control);
    });
})();
