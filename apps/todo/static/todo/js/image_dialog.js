/*
 * The image popup of the To-do pages (issue #344 follow-up): clicking an attachment's thumbnail shows the full
 * image in a dialog over the same page. **Open** in the file's row still opens it in a new tab, as before.
 *
 * The markup is `todo/partials/image_dialog.html`, once per page, in the layout. The thumbnails
 * (`[data-attachment-thumbnail]`, `todo/partials/attachment_thumbnail.html`) are swapped in by htmx, so one
 * delegated click listener on the document handles them all. Its `<img src>` is the thumbnail endpoint, which
 * redirects to the full image, so the dialog loads the same address. A click with Ctrl, Cmd, Shift or Alt, or a
 * middle click, is left to the browser (a new tab or window), like any link.
 *
 * Loaded as a classic script (not deferred) so its `alpine:init` listener exists before Alpine starts.
 */
(function () {
    'use strict';

    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0) {
            return;
        }
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }
        var link = event.target.closest && event.target.closest('a[data-attachment-thumbnail]');
        if (!link) {
            return;
        }
        var image = link.querySelector('img');
        if (!image) {
            return;
        }
        event.preventDefault();
        window.dispatchEvent(new CustomEvent('open-image-dialog', {
            detail: {src: image.getAttribute('src'), href: link.getAttribute('href'), trigger: link},
        }));
    });

    document.addEventListener('alpine:init', function () {
        Alpine.data('imageDialog', function () {
            return {
                open: false,
                src: '',
                href: '',
                loaded: false,
                failed: false,
                trigger: null,
                show: function (detail) {
                    this.src = detail.src;
                    this.href = detail.href;
                    this.trigger = detail.trigger || null;
                    this.loaded = false;
                    this.failed = false;
                    this.open = true;
                    var self = this;
                    this.$nextTick(function () {
                        if (self.$refs.close) {
                            self.$refs.close.focus();
                        }
                    });
                },
                close: function () {
                    this.open = false;
                    var trigger = this.trigger;
                    this.trigger = null;
                    if (trigger && document.body.contains(trigger)) {
                        trigger.focus({preventScroll: true});
                    }
                    // Drop the image once the fade-out is over, so a closed dialog holds no large bitmap.
                    var self = this;
                    setTimeout(function () {
                        if (!self.open) {
                            self.src = '';
                        }
                    }, 300);
                },
            };
        });
    });
})();
