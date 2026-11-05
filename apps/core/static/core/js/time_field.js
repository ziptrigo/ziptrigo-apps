// Alpine component behind core/components/time_field.html: a time field you can type into (10pm,
// 22:00, 2200, 10:30 p, 9) or choose from half-hour suggestions, shown on the user's clock
// (12-hour `10:00 PM` or 24-hour `22:00`). An ARIA combobox: the text box, a listbox of
// suggestions, arrow keys to move, Enter to choose, Escape to close.
//
// The hidden input is what the form submits (`HH:MM`). Like the date field it fires bubbling
// `input` and `change` events when the value changes, and a nonsense value shows a message and
// leaves the hidden value at its previous time. Other scripts set the value with a `field-set`
// event on the hidden input ({ detail: { value } }); a parent Alpine scope can bind it with
// `x-model`.
document.addEventListener('alpine:init', () => {
  const Z = window.ZTDateTime;
  let uid = 0;

  Alpine.data('timeField', () => ({
    open: false,
    value: '',
    text: '',
    error: '',
    active: -1,
    required: false,
    fmt: { order: 'mdy', clock: '12h' },
    options: [],
    listId: '',
    messageId: '',
    baseDescribedBy: '',

    init() {
      const d = this.$el.dataset;
      this.fmt = Z.formatsFor();
      this.required = d.required === 'true';
      this.value = this.$refs.hidden.value || d.value || '';
      const n = ++uid;
      this.baseDescribedBy = this.$refs.text.getAttribute('aria-describedby') || '';
      this.listId = `time-field-list-${n}`;
      this.messageId = `time-field-msg-${n}`;
      const options = [];
      for (let minutes = 0; minutes < 24 * 60; minutes += 30) {
        const hh = Math.floor(minutes / 60);
        const mm = minutes % 60;
        options.push({ value: `${Z.pad(hh)}:${Z.pad(mm)}`, label: Z.formatTime(hh, mm, this.fmt) });
      }
      this.options = options;
      this.text = this.format(this.value);
      this.$watch('value', (v) => {
        this.$refs.hidden.value = v;
        this.text = this.format(v);
        this.setError('');
      });
    },

    format(iso) {
      return iso ? Z.formatIsoTime(iso, this.fmt) : '';
    },

    get placeholder() {
      return this.fmt.clock === '24h' ? '21:00' : '9:00 PM';
    },

    get disabled() {
      return this.$refs.text.disabled;
    },

    setError(message) {
      // Nothing of ours to clear: leave what core/js/field_errors.js set on the box (a refused
      // form's message) alone when focus merely passes through.
      if (!message && !this.error) return;
      this.error = message;
      const text = this.$refs.text;
      if (message) {
        text.setAttribute('aria-invalid', 'true');
        text.setAttribute('aria-describedby', `${this.messageId} ${this.baseDescribedBy}`.trim());
      } else {
        text.removeAttribute('aria-invalid');
        if (this.baseDescribedBy) text.setAttribute('aria-describedby', this.baseDescribedBy);
        else if ((text.getAttribute('aria-describedby') || '').startsWith(this.messageId)) text.removeAttribute('aria-describedby');
      }
    },

    commitValue(iso) {
      const changed = iso !== this.value;
      this.value = iso;
      this.text = this.format(iso);
      this.setError('');
      const hidden = this.$refs.hidden;
      hidden.value = iso;
      if (changed) {
        hidden.dispatchEvent(new Event('input', { bubbles: true }));
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
      }
    },

    setExternal(iso) {
      this.value = iso || '';
      this.text = this.format(this.value);
      this.setError('');
    },

    // Reads the text box (on blur and on Enter). Returns whether the field is in a good state.
    commit() {
      const typed = this.text.trim();
      if (typed === '') {
        if (this.required) {
          this.setError('Enter a time.');
          return false;
        }
        if (this.value) this.commitValue('');
        else this.setError('');
        return true;
      }
      if (typed === this.format(this.value)) {
        this.setError('');
        return true;
      }
      const iso = Z.parseTime(typed);
      if (!iso) {
        const example = this.fmt.clock === '24h' ? '21:00' : '9:00 PM';
        this.setError(`Enter a time like ${example}.`);
        return false;
      }
      this.commitValue(iso);
      return true;
    },

    // The index of the suggestion nearest to the typed or saved time.
    nearest() {
      const iso = Z.parseTime(this.text) || this.value;
      if (!iso) return -1;
      const [hh, mm] = iso.split(':').map(Number);
      return Math.min(Math.round((hh * 60 + mm) / 30), this.options.length - 1);
    },

    show() {
      if (this.disabled) return;
      this.open = true;
      this.active = this.nearest();
      this.reveal();
    },

    reveal() {
      this.$nextTick(() => {
        const el = document.getElementById(`${this.listId}-${this.active}`);
        if (el) el.scrollIntoView({ block: 'nearest' });
      });
    },

    move(delta) {
      if (!this.open) {
        this.show();
        return;
      }
      const last = this.options.length - 1;
      this.active = this.active < 0 ? (delta > 0 ? 0 : last) : Math.max(0, Math.min(last, this.active + delta));
      this.reveal();
    },

    choose(index) {
      const option = this.options[index];
      if (!option) return;
      this.commitValue(option.value);
      this.open = false;
      this.active = -1;
    },

    onEnter(event) {
      if (this.open && this.active >= 0 && this.options[this.active]) {
        event.preventDefault();
        this.choose(this.active);
        return;
      }
      this.open = false;
      if (!this.commit()) event.preventDefault();
    },

    onEscape(event) {
      if (!this.open) return;
      event.stopPropagation();
      this.open = false;
      this.active = -1;
    },

    onBlur(event) {
      const next = event.relatedTarget;
      if (!next || !this.$el.contains(next)) this.open = false;
      this.commit();
    },
  }));
});
