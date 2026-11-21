/*
 * The note editor (`form[data-note-editor]`), loaded deferred by notes/layout.html:
 *
 *  - Ctrl+S / Cmd+S saves (submits the form the way the Save button does).
 *  - A `beforeunload` warning while the title or the content differs from what the editor opened
 *    with (or while the conflict panel is showing: the typed text is not saved). It is dropped when
 *    a save succeeds (the response redirects: `HX-Redirect`) and when "Discard my changes" is used.
 *    There is no autosave.
 *  - The view switch, the `noteEditor` Alpine component on the form: Content only (the default every
 *    time the editor opens), Content + preview, or Preview only. The choice is kept in
 *    `window.ztNoteEditorView` for the life of the page, so the form re-rendered by a conflict keeps
 *    it, except that a conflict never opens on Preview only (the person's own text, which the
 *    conflict panel asks them to compare, would be hidden): it opens on Content + preview instead.
 *    The preview is only asked for while it shows: the textarea's `input` trigger is filtered on its
 *    `data-preview`, and switching to a view with the preview fetches it once (`htmx.ajax` from the
 *    textarea, so its `hx-sync` still applies). Hiding the textarea while it has the focus moves the
 *    focus to the preview, and a refused save whose field errors name the content switches back to
 *    a view that shows the textarea, so the message under it can be seen.
 *
 * Loaded as a classic script (not `defer`) by both layouts, so its `alpine:init` listener exists
 * before Alpine starts.
 */
(function () {
    'use strict';

    let skipWarning = false;

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('noteEditor', function () {
            return {
                view: 'content',

                init() {
                    let view = window.ztNoteEditorView || 'content';
                    if (view === 'preview' && this.$el.querySelector('#editor-conflict')) view = 'split';
                    this.view = window.ztNoteEditorView = view;
                    if (view !== 'content') this.$nextTick(() => this.refreshPreview());
                },

                setView(view) {
                    const showed = this.view !== 'content';
                    const pane = this.$refs.writePane;
                    const hidesFocus = view === 'preview' && pane && pane.contains(document.activeElement);
                    this.view = window.ztNoteEditorView = view;
                    if (view !== 'content' && !showed) this.$nextTick(() => this.refreshPreview());
                    if (hidesFocus) this.$nextTick(() => this.$refs.preview.focus());
                },

                refreshPreview() {
                    const area = this.$refs.content;
                    htmx.ajax('POST', area.getAttribute('hx-post'), {
                        source: area, target: '#note-preview', swap: 'innerHTML',
                    });
                },

                // A refused save swaps its messages into #editor-msg; when one of them is about the
                // content while the textarea is hidden, show it again (field_errors.js puts the message
                // under it).
                showContentErrors(event) {
                    if (this.view !== 'preview') return;
                    const target = event.target;
                    if (!target || !target.querySelector) return;
                    if (!target.querySelector('[data-field-error="content"]')) return;
                    this.setView('split');
                    this.$nextTick(() => this.$refs.content.focus());
                },
            };
        });
    });

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
