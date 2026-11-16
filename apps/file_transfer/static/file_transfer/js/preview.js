// The file preview dialog of the public download page (issue #311).
//
// A classic script (not a module, not deferred), loaded before Alpine so the `alpine:init` listener
// below exists in time. The page puts the previewable files into a `json_script` element
// (`#ft-previews`: id, name, size, kind, icon, the preview URL, the download URL) and wraps the file
// list and the dialog in `x-data="ftPreview"`.
//
// Nothing here decides what a file is: `kind` comes from the server, which read it from the file's
// bytes. An image, PDF, video or audio file is shown by pointing an <img>, <iframe>, <video> or
// <audio> at the preview URL (a redirect to a short-lived presigned URL). Text is fetched from the
// preview URL and put in the page with `x-text` (textContent), never as HTML.
document.addEventListener('alpine:init', function () {
    Alpine.data('ftPreview', function () {
        return {
            files: [],
            isOpen: false,
            index: 0,
            // 'loading', 'ready' or 'error' for the file being shown.
            status: 'loading',
            text: '',
            // Markdown: the server-rendered, sanitised HTML, which view is showing, and whether the
            // formatted view covers only the start of the file.
            html: '',
            htmlTruncated: false,
            view: 'formatted',
            trigger: null,
            controller: null,

            init() {
                const data = document.getElementById('ft-previews');
                this.files = data ? JSON.parse(data.textContent) : [];
            },

            get current() { return this.files[this.index] || {}; },
            get position() { return (this.index + 1) + ' of ' + this.files.length; },
            get many() { return this.files.length > 1; },

            open(id) {
                const index = this.files.findIndex((file) => file.id === id);
                if (index < 0) return;
                this.trigger = document.activeElement;
                this.index = index;
                this.isOpen = true;
                document.body.classList.add('overflow-hidden');
                this.show();
                this.$nextTick(() => { if (this.$refs.close) this.$refs.close.focus(); });
            },

            close() {
                if (!this.isOpen) return;
                this.isOpen = false;
                if (this.controller) this.controller.abort();
                this.text = '';
                this.html = '';
                document.body.classList.remove('overflow-hidden');
                const trigger = this.trigger;
                this.trigger = null;
                if (trigger && trigger.focus) trigger.focus();
            },

            step(delta) {
                if (!this.isOpen || !this.many) return;
                this.index = (this.index + delta + this.files.length) % this.files.length;
                this.show();
            },
            prev() { this.step(-1); },
            next() { this.step(1); },

            // Start showing the current file: media report back through their own load events,
            // text is fetched here.
            show() {
                if (this.controller) this.controller.abort();
                this.controller = null;
                this.text = '';
                this.html = '';
                this.htmlTruncated = false;
                this.view = 'formatted';
                this.status = 'loading';
                if (this.current.kind === 'text' || this.current.kind === 'markdown') this.loadText();
            },

            async loadText() {
                const file = this.current;
                this.controller = new AbortController();
                try {
                    const response = await fetch(file.url, {
                        credentials: 'same-origin',
                        signal: this.controller.signal,
                    });
                    if (!response.ok) throw new Error('status ' + response.status);
                    let text;
                    let html = '';
                    let truncated = false;
                    if (file.kind === 'markdown') {
                        // The raw text and its rendering, which the server sanitised (raw HTML is
                        // escaped, images are never rendered).
                        const data = await response.json();
                        text = data.text;
                        html = data.html || '';
                        truncated = !!data.html_truncated;
                    } else {
                        text = await response.text();
                    }
                    if (this.current.id !== file.id) return;
                    this.text = text;
                    this.html = html;
                    this.htmlTruncated = truncated;
                    // Without a rendering there is only the raw view.
                    this.view = file.kind === 'markdown' && html ? 'formatted' : 'raw';
                    this.status = 'ready';
                } catch (error) {
                    if (error && error.name === 'AbortError') return;
                    if (this.current.id === file.id) this.status = 'error';
                }
            },

            get showFormatted() { return this.current.kind === 'markdown' && this.view === 'formatted' && !!this.html; },
            get showRaw() { return this.current.kind === 'text' || (this.current.kind === 'markdown' && !this.showFormatted); },
            get canToggle() { return this.current.kind === 'markdown' && !!this.html; },
            setView(view) { this.view = view; },

            ready() { this.status = 'ready'; },
            failed() { this.status = 'error'; },

            // Arrow keys move between the previewable files, except where they mean something
            // else (a video's seek bar, a field).
            onKey(event) {
                if (!this.isOpen) return;
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.close();
                    return;
                }
                if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
                const tag = event.target && event.target.tagName;
                if (['VIDEO', 'AUDIO', 'INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
                event.preventDefault();
                this.step(event.key === 'ArrowLeft' ? -1 : 1);
            },

            // Keep Tab inside the dialog.
            trap(event) {
                if (event.key !== 'Tab') return;
                const dialog = this.$refs.dialog;
                const focusable = Array.from(dialog.querySelectorAll(
                    'button:not([disabled]), a[href], video[controls], audio[controls], [tabindex]:not([tabindex="-1"])'
                )).filter((el) => el.offsetParent !== null);
                if (!focusable.length) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            },
        };
    });
});
