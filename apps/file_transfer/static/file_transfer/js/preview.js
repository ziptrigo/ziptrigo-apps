// The file preview dialog of the public download page (issue #311).
//
// A classic script (not a module, not deferred), loaded before Alpine so the `alpine:init` listener
// below exists in time. The page puts the previewable files into a `json_script` element
// (`#ft-previews`: id, name, size, kind, icon, the preview URL, the "view full" URL and label, the
// download URL, whether a text sample was cut) and wraps the file list and the dialog in
// `x-data="ftPreview"`.
//
// A preview is a SAMPLE, never the file:
//   * image / pdf: `url` is the build's status endpoint. The first call (`?start=1`) starts the
//     lazy build of downsized WebP pages; the script polls until they exist (`preparing`), then
//     shows them with <img> (a PDF's pages with Previous / Next page).
//   * text / markdown: `url` is the first 64 KB, fetched and put in with `x-text` (textContent).
// "View full image / document" (`full_url`) COUNTS AS A DOWNLOAD (the server records it). It
// answers a presigned URL which is fetched into a Blob and shown from there; the Download buttons
// then save that Blob with no request at all, so the whole thing is one download. If the browser
// may not read the bucket (CORS), the URL is shown directly and the server lets the Download that
// follows through without counting it again (once, in this session).
//
// Nothing here decides what a file is: `kind` comes from the server, which read it from the
// file's bytes.
document.addEventListener('alpine:init', function () {
    // How many full files are kept in memory for the Download buttons (a file evicted just
    // downloads from the server, which does not count it a second time).
    const MAX_BLOBS = 3;
    // How often, and how many times, the build is polled before giving up for now.
    const POLL_MS = 1500;
    const MAX_POLLS = 40;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    Alpine.data('ftPreview', function () {
        return {
            files: [],
            isOpen: false,
            index: 0,
            // 'loading' (spinner), 'preparing' (the pages are being built), 'ready', 'unavailable'
            // (no downsized preview of this file) or 'error' (it could not be loaded).
            status: 'loading',
            // Only for 'unavailable': a failed build can be tried again, a verdict cannot.
            canRetry: false,
            // Image and PDF: the downsized pages' URLs, the one showing, the PDF's real page count.
            pages: [],
            pageIndex: 0,
            totalPages: 0,
            // Text and Markdown: the sample (or, after View full, the whole file), the server-
            // rendered and sanitised HTML, and which view shows.
            text: '',
            html: '',
            htmlTruncated: false,
            view: 'formatted',
            // View full: 'idle', 'loading', 'ready' or 'error'; what it shows (an object URL of
            // the Blob, or the bucket's URL when the Blob could not be fetched).
            fullState: 'idle',
            fullSrc: '',
            fullFailedMessage: '',
            // Files this page has counted as a download through View full, and the Blobs kept.
            counted: new Set(),
            blobs: new Map(),
            objectUrl: '',
            trigger: null,
            controller: null,

            init() {
                const data = document.getElementById('ft-previews');
                this.files = data ? JSON.parse(data.textContent) : [];
            },

            get current() { return this.files[this.index] || {}; },
            get position() { return (this.index + 1) + ' of ' + this.files.length; },
            get many() { return this.files.length > 1; },
            get isText() { return this.current.kind === 'text' || this.current.kind === 'markdown'; },
            get isPdf() { return this.current.kind === 'pdf'; },
            get pageUrl() { return this.pages[this.pageIndex] || ''; },
            get pageLabel() { return 'Page ' + (this.pageIndex + 1) + ' of ' + this.pages.length; },
            get hasPageNav() { return this.isPdf && this.pages.length > 1 && this.fullState !== 'ready'; },
            get cutPages() { return this.isPdf && this.totalPages > this.pages.length; },
            get fullShowing() { return this.fullState === 'ready'; },
            get wasCounted() { return !!this.current.id && this.counted.has(this.current.id); },
            // The button: offered while the whole file is not already showing.
            get canViewFull() { return !!this.current.full_url && this.fullState !== 'ready'; },
            get sampleOnly() { return this.isText && this.current.sample_cut && this.fullState !== 'ready'; },
            get errorText() {
                if (this.status === 'unavailable') return 'Preview not available for this file.';
                return "This file can't be previewed here. You can still download it.";
            },

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
                this.reset();
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

            // PDF pages (a document's, not the files').
            pageStep(delta) {
                const target = this.pageIndex + delta;
                if (target < 0 || target >= this.pages.length) return;
                this.pageIndex = target;
                this.status = 'loading';
            },
            prevPage() { this.pageStep(-1); },
            nextPage() { this.pageStep(1); },

            reset() {
                if (this.controller) this.controller.abort();
                this.controller = null;
                this.releaseObjectUrl();
                this.text = '';
                this.html = '';
                this.htmlTruncated = false;
                this.view = 'formatted';
                this.pages = [];
                this.pageIndex = 0;
                this.totalPages = 0;
                this.canRetry = false;
                this.fullState = 'idle';
                this.fullSrc = '';
                this.fullFailedMessage = '';
            },

            releaseObjectUrl() {
                if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
                this.objectUrl = '';
            },

            // Start showing the current file.
            show() {
                this.reset();
                this.status = 'loading';
                this.controller = new AbortController();
                if (this.isText) this.loadText();
                else this.loadPages(true);
            },

            // An image or a PDF: ask for the build (first call), poll until the pages exist.
            async loadPages(start) {
                const file = this.current;
                const signal = this.controller.signal;
                let polls = 0;
                try {
                    for (;;) {
                        const response = await fetch(file.url + (start ? '?start=1' : ''), {
                            credentials: 'same-origin',
                            signal,
                        });
                        if (!response.ok) throw new Error('status ' + response.status);
                        const data = await response.json();
                        if (this.current.id !== file.id) return;
                        start = false;
                        if (data.status === 'ready' && data.page_urls.length) {
                            this.pages = data.page_urls;
                            this.totalPages = data.total_pages || data.page_urls.length;
                            this.pageIndex = 0;
                            // The first page's <img> reports `load`, which makes it 'ready'.
                            this.status = 'loading';
                            return;
                        }
                        if (data.status === 'unavailable' || data.status === 'failed') {
                            this.canRetry = data.status === 'failed';
                            this.status = 'unavailable';
                            return;
                        }
                        // 'preparing' (or 'none': a purge reset it meanwhile, so ask again).
                        if (data.status === 'none') start = true;
                        this.status = 'preparing';
                        if (++polls > MAX_POLLS) {
                            this.canRetry = true;
                            this.status = 'unavailable';
                            return;
                        }
                        await sleep(POLL_MS);
                        if (this.current.id !== file.id || signal.aborted) return;
                    }
                } catch (error) {
                    if (error && error.name === 'AbortError') return;
                    if (this.current.id === file.id) this.status = 'error';
                }
            },

            retry() {
                if (!this.isOpen) return;
                this.status = 'loading';
                this.canRetry = false;
                this.loadPages(true);
            },

            async loadText() {
                const file = this.current;
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

            // The page images report back through their own events (any HTTP or decoding failure
            // of one is the same "can't be previewed" message, never a blank dialog).
            ready() { if (this.status === 'loading') this.status = 'ready'; },
            failed() { this.status = 'error'; },

            // "View full image / document": counts as a download (the server records it), then
            // the whole file is shown from a Blob.
            async viewFull() {
                const file = this.current;
                if (!file.full_url || this.fullState === 'loading') return;
                this.fullState = 'loading';
                this.fullFailedMessage = '';
                const signal = this.controller ? this.controller.signal : undefined;
                try {
                    let entry = this.blobs.get(file.id);
                    let meta = null;
                    if (!entry) {
                        const response = await fetch(file.full_url, { credentials: 'same-origin', signal });
                        if (!response.ok) throw new Error('status ' + response.status);
                        meta = await response.json();
                        // The server counted it. (A second ask in this session is free, server side.)
                        this.counted.add(file.id);
                        try {
                            // No cookies for the bucket; if its CORS rules don't allow this page,
                            // this throws and the URL is shown directly below.
                            const fetched = await fetch(meta.url, { mode: 'cors', credentials: 'omit', signal });
                            if (!fetched.ok) throw new Error('status ' + fetched.status);
                            const blob = await fetched.blob();
                            entry = { blob: new Blob([blob], { type: meta.content_type }), name: meta.name };
                            this.keepBlob(file.id, entry);
                        } catch (error) {
                            if (error && error.name === 'AbortError') throw error;
                            entry = null;
                        }
                    }
                    if (this.current.id !== file.id) return;
                    await this.showFull(file, entry, meta);
                } catch (error) {
                    if (error && error.name === 'AbortError') return;
                    if (this.current.id === file.id) {
                        this.fullState = 'error';
                        this.fullFailedMessage = "Couldn't load the full file. You can still download it.";
                    }
                }
            },

            keepBlob(id, entry) {
                this.blobs.delete(id);
                this.blobs.set(id, entry);
                while (this.blobs.size > MAX_BLOBS) this.blobs.delete(this.blobs.keys().next().value);
            },

            async showFull(file, entry, meta) {
                if (this.isText) {
                    if (!entry) {
                        // Can't read the bucket from here: nothing to show, but it is counted and
                        // the Download that follows is not counted again.
                        this.fullState = 'error';
                        this.fullFailedMessage = "The whole file can't be shown here. Use Download: it won't count twice.";
                        return;
                    }
                    const whole = await entry.blob.text();
                    if (this.current.id !== file.id) return;
                    // The whole file is plain text (Markdown too: the formatted view is the
                    // sample's).
                    this.text = whole;
                    this.html = '';
                    this.view = 'raw';
                    this.status = 'ready';
                    this.fullState = 'ready';
                    return;
                }
                this.releaseObjectUrl();
                if (entry) {
                    this.objectUrl = URL.createObjectURL(entry.blob);
                    this.fullSrc = this.objectUrl;
                } else {
                    // The bucket's own URL: shown directly (the Download that follows is not
                    // counted again by the server).
                    this.fullSrc = meta.url;
                }
                this.status = 'loading';
                this.fullState = 'ready';
            },

            // Save a file that "View full" already fetched: no request, so no second download.
            // Without one it is the ordinary link, which the server does not count twice either.
            saveFile(event, id) {
                const entry = this.blobs.get(id);
                if (!entry) return;
                event.preventDefault();
                const url = URL.createObjectURL(entry.blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = entry.name;
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
            },

            // Left and Right move between the previewable files; Page Up and Page Down between a
            // document's pages. Not inside the text areas (where the arrows scroll) or fields.
            onKey(event) {
                if (!this.isOpen) return;
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.close();
                    return;
                }
                if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
                const target = event.target;
                if (target && target.closest && target.closest('[data-keys-own]')) return;
                if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
                if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    this.step(event.key === 'ArrowLeft' ? -1 : 1);
                } else if ((event.key === 'PageUp' || event.key === 'PageDown') && this.hasPageNav) {
                    event.preventDefault();
                    this.pageStep(event.key === 'PageUp' ? -1 : 1);
                }
            },

            // Keep Tab inside the dialog.
            trap(event) {
                if (event.key !== 'Tab') return;
                const dialog = this.$refs.dialog;
                const focusable = Array.from(dialog.querySelectorAll(
                    'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
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
