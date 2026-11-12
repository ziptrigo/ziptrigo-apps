/*
 * The close dialog of the To-do pages (issue #272): what "Complete" asks for a parent with open
 * subtasks (close them as Done, as Won't do, or leave them open) and what "Close as won't do" asks
 * (an optional note, which becomes a comment, and the same question about the subtasks).
 *
 * The markup is `todo/partials/close_dialog.html`, once per page, in the layout. A button opens it with
 * `todoOpenCloseDialog(button)`, reading `data-url`, `data-mode` (`done` or `wont_do`), `data-ctx`
 * (`row` or `page`), `data-title`, `data-count` (the open subtasks), `data-blockers` (the open blockers,
 * issue #273) and `data-blockers-url` (JSON with their names) from it. With open blockers the form lists
 * them and posts `confirm_blockers=1`: closing is allowed after that confirmation, the links are kept. The form is a plain POST
 * (like the shared confirmation modal): the views answer it with a redirect back.
 *
 * Loaded as a classic script (not deferred) so its `alpine:init` listener exists before Alpine starts.
 */
(function () {
    'use strict';

    document.addEventListener('alpine:init', function () {
        Alpine.data('closeDialog', function () {
            return {
                open: false,
                url: '',
                mode: 'done',
                ctx: 'row',
                title: '',
                count: 0,
                blockers: 0,
                blockerList: [],
                subtasks: 'done',
                trigger: null,
                get heading() {
                    return this.mode === 'wont_do' ? "Close as won't do" : 'Complete task';
                },
                get intro() {
                    return this.mode === 'wont_do'
                        ? this.title + " will be closed as won't do."
                        : this.title + ' will be marked as done.';
                },
                show: function (detail) {
                    this.url = detail.url;
                    this.mode = detail.mode === 'wont_do' ? 'wont_do' : 'done';
                    this.ctx = detail.ctx === 'page' ? 'page' : 'row';
                    this.title = detail.title || '';
                    this.count = parseInt(detail.count || '0', 10) || 0;
                    this.blockers = parseInt(detail.blockers || '0', 10) || 0;
                    this.blockerList = [];
                    this.subtasks = 'done';
                    this.trigger = detail.trigger || null;
                    this.open = true;
                    var self = this;
                    if (this.blockers > 0 && detail.blockersUrl) {
                        fetch(detail.blockersUrl, {credentials: 'same-origin', headers: {Accept: 'application/json'}})
                            .then(function (response) { return response.ok ? response.json() : {blockers: []}; })
                            .then(function (data) { if (self.url === detail.url) self.blockerList = data.blockers || []; })
                            .catch(function () {});
                    }
                    // Not once it has closed again: a quick Escape can come before this runs.
                    this.$nextTick(function () {
                        if (!self.open) return;
                        var first = self.$refs.note || self.$root.querySelector('input[name="subtasks"]') || self.$refs.cancel;
                        if (first) first.focus();
                    });
                },
                close: function () {
                    this.open = false;
                    var trigger = this.trigger;
                    if (trigger && trigger.isConnected) trigger.focus();
                },
            };
        });
    });

    window.todoOpenCloseDialog = function (button) {
        window.dispatchEvent(new CustomEvent('open-close-dialog', {
            detail: {
                url: button.dataset.url,
                mode: button.dataset.mode,
                ctx: button.dataset.ctx,
                title: button.dataset.title,
                count: button.dataset.count,
                blockers: button.dataset.blockers,
                blockersUrl: button.dataset.blockersUrl,
                trigger: button,
            },
        }));
    };
})();
