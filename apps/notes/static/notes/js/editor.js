/*
 * The note editor (`form[data-note-editor]`), loaded deferred by notes/layout.html:
 *
 *  - Ctrl+S / Cmd+S saves (submits the form the way the Save button does).
 *  - A `beforeunload` warning while the title or the content differs from what the editor opened
 *    with (or while the conflict panel is showing: the typed text is not saved). It is dropped when
 *    a save succeeds (the response redirects: `HX-Redirect`) and when "Discard my changes" is used.
 *    There is no autosave.
 */
(function () {
    'use strict';

    let skipWarning = false;

    function editorForm() {
        return document.querySelector('form[data-note-editor]');
    }

    function fieldsOf(form) {
        const title = form.querySelector('[name="title"]');
        const content = form.querySelector('[name="content"]');
        return (title ? title.value : '') + '\u0000' + (content ? content.value : '');
    }

    function isDirty(form) {
        if (form.querySelector('#editor-conflict')) return true;
        return form.dataset.initial !== undefined && fieldsOf(form) !== form.dataset.initial;
    }

    function wire(form) {
        if (form.dataset.editorReady) return;
        form.dataset.editorReady = '1';
        form.dataset.initial = fieldsOf(form);
        const discard = form.querySelector('[data-discard]');
        if (discard) discard.addEventListener('click', function () { skipWarning = true; });
    }

    window.addEventListener('beforeunload', function (event) {
        const form = editorForm();
        if (skipWarning || !form || !isDirty(form)) return;
        event.preventDefault();
        event.returnValue = '';
    });

    // A successful save answers with an HX-Redirect: the page is about to be left on purpose.
    document.addEventListener('htmx:beforeOnLoad', function (event) {
        const xhr = event.detail && event.detail.xhr;
        if (xhr && xhr.getResponseHeader && xhr.getResponseHeader('HX-Redirect')) skipWarning = true;
    });

    document.addEventListener('keydown', function (event) {
        if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
        const form = editorForm();
        if (!form) return;
        event.preventDefault();
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
    });

    htmx.onLoad(function (root) {
        const scope = root.querySelectorAll ? root : document;
        scope.querySelectorAll('form[data-note-editor]').forEach(wire);
        if (root.matches && root.matches('form[data-note-editor]')) wire(root);
    });
})();
