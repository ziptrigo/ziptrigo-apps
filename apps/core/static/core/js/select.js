// Alpine component behind core/components/select.html: the site's own dropdown, in place of the
// operating system's. An ARIA select-only combobox: the button shows the choice and opens a listbox;
// Enter, Space or Arrow Down opens it, the arrow keys, Home and End move, typing jumps to the next option
// that starts with the letters, Enter chooses, Escape closes and Tab leaves without choosing.
//
// The options are rendered by the server (`<li role="option" data-value data-label data-hint>`) and read
// from the DOM, so the component needs no data of its own. The hidden input is what the form submits; like
// the date and time fields it fires bubbling `input` and `change` events when the value changes, so a form
// that refreshes on `change` behaves as with a native select. Other scripts set the value with a
// `field-set` event on the hidden input ({ detail: { value } }).
document.addEventListener('alpine:init', () => {
  let uid = 0;

  Alpine.data('themedSelect', () => ({
    open: false,
    value: '',
    active: -1,
    listId: '',
    typed: '',
    typedAt: 0,

    init() {
      this.listId = `select-list-${++uid}`;
      this.value = this.$refs.hidden.value || this.$el.dataset.value || '';
      if (!this.optionFor(this.value) && this.options.length) {
        this.value = this.options[0].dataset.value;
        this.$refs.hidden.value = this.value;
      }
    },

    get options() {
      return Array.from(this.$refs.list.querySelectorAll('[role="option"]'));
    },

    optionFor(value) {
      return this.options.find((o) => o.dataset.value === value);
    },

    get selectedIndex() {
      return this.options.findIndex((o) => o.dataset.value === this.value);
    },

    get label() {
      const o = this.optionFor(this.value);
      return o ? o.dataset.label : '';
    },

    get hint() {
      const o = this.optionFor(this.value);
      return o ? o.dataset.hint || '' : '';
    },

    get disabled() {
      return this.$refs.button.disabled;
    },

    show() {
      if (this.disabled) return;
      this.open = true;
      this.active = Math.max(this.selectedIndex, 0);
      this.reveal();
    },

    close() {
      this.open = false;
      this.active = -1;
    },

    toggle() {
      if (this.open) this.close();
      else this.show();
    },

    reveal() {
      this.$nextTick(() => {
        const el = this.options[this.active];
        if (el) el.scrollIntoView({ block: 'nearest' });
      });
    },

    move(delta) {
      if (!this.open) {
        this.show();
        return;
      }
      const last = this.options.length - 1;
      this.active = Math.max(0, Math.min(last, this.active + delta));
      this.reveal();
    },

    jump(index) {
      if (!this.open) this.show();
      this.active = index;
      this.reveal();
    },

    typeahead(character) {
      const now = Date.now();
      this.typed = now - this.typedAt > 700 ? character : this.typed + character;
      this.typedAt = now;
      const start = this.open ? this.active : this.selectedIndex;
      const options = this.options;
      const text = this.typed.toLowerCase();
      // One repeated letter cycles through the options that start with it, as a native select does.
      const cycling = text.split('').every((c) => c === text[0]);
      const from = cycling ? start + 1 : start;
      for (let step = 0; step < options.length; step++) {
        const i = (from + step) % options.length;
        const wanted = cycling ? text[0] : text;
        if (options[i].dataset.label.toLowerCase().startsWith(wanted)) {
          if (this.open) this.jump(i);
          else this.choose(i);
          return;
        }
      }
    },

    choose(index) {
      const option = this.options[index];
      if (!option) return;
      const value = option.dataset.value;
      const changed = value !== this.value;
      this.value = value;
      const hidden = this.$refs.hidden;
      hidden.value = value;
      this.close();
      if (changed) {
        hidden.dispatchEvent(new Event('input', { bubbles: true }));
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
      }
    },

    setExternal(value) {
      if (this.optionFor(value)) {
        this.value = value;
        this.$refs.hidden.value = value;
      }
    },

    onKey(event) {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          this.move(1);
          break;
        case 'ArrowUp':
          event.preventDefault();
          this.move(-1);
          break;
        case 'Home':
          event.preventDefault();
          this.jump(0);
          break;
        case 'End':
          event.preventDefault();
          this.jump(this.options.length - 1);
          break;
        case 'Enter':
        case ' ':
          event.preventDefault();
          if (this.open && this.active >= 0) this.choose(this.active);
          else this.show();
          break;
        case 'Escape':
          if (this.open) {
            event.preventDefault();
            event.stopPropagation();
            this.close();
          }
          break;
        case 'Tab':
          // Leaving the list chooses nothing: a choice may save a form at once.
          this.close();
          break;
        default:
          if (event.key.length === 1 && event.key !== ' ') this.typeahead(event.key);
      }
    },

    onBlur(event) {
      const next = event.relatedTarget;
      if (!next || !this.$el.contains(next)) this.close();
    },
  }));
});
