// Alpine component behind core/components/date_picker.html: a date field you can type into (in your
// order, ISO accepted too) or pick from a calendar. Dates are plain 'YYYY-MM-DD' strings
// throughout (never Date objects across the UTC boundary), so a day never shifts.
//
// The hidden input is what the form submits (ISO). It fires bubbling `input` and `change` events
// when the value changes, so an htmx form that refreshes on change and core/js/field_errors.js see
// it. A bad or out-of-range typed date shows a message under the field and leaves the hidden value
// at its previous date: a typo never clears a saved one. Other scripts set the value with a
// `field-set` event on the hidden input ({ detail: { value } }); a parent Alpine scope can bind it
// with `x-model` (the component is `x-modelable` on its value).
document.addEventListener('alpine:init', () => {
  const Z = window.ZTDateTime;
  const todayIso = () => {
    const t = new Date();
    return Z.isoDate(t.getFullYear(), t.getMonth() + 1, t.getDate());
  };
  let uid = 0;

  Alpine.data('datePicker', () => ({
    open: false,
    value: '',
    text: '',
    error: '',
    min: '',
    max: '',
    year: 0,
    month: 0, // 0-based
    weekdays: ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'],
    fmt: { order: 'mdy', clock: '12h' },
    messageId: '',
    baseDescribedBy: '',

    init() {
      const d = this.$el.dataset;
      const f = Z.formatsFor();
      this.fmt = { order: Z.numericOrder(f), clock: f.clock };
      // local_time.js may already have written the hidden input (a UTC pair split into a local date).
      this.value = this.$refs.hidden.value || d.value || '';
      this.min = d.min || '';
      this.max = d.max || '';
      this.messageId = `date-field-msg-${++uid}`;
      this.baseDescribedBy = this.$refs.text.getAttribute('aria-describedby') || '';
      this.text = this.format(this.value);
      this.showMonthOf(this.value || todayIso());
      this.$watch('value', (v) => {
        this.$refs.hidden.value = v;
        this.text = this.format(v);
        this.setError('');
      });
    },

    format(iso) {
      return iso ? Z.formatIsoDate(iso, 'full', this.fmt) : '';
    },

    get placeholder() {
      return this.fmt.order === 'dmy' ? 'dd/mm/yyyy' : 'mm/dd/yyyy';
    },

    get disabled() {
      return this.$refs.text.disabled;
    },

    setError(message) {
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

    // Make `iso` the value, show it, and tell the form: bubbling input + change on the hidden input.
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

    // Another script wrote a value: show it. No events (the writer knows).
    setExternal(iso) {
      this.value = iso || '';
      this.text = this.format(this.value);
      this.setError('');
      this.showMonthOf(this.value || todayIso());
    },

    // Reads the text box (on blur and on Enter). Returns whether the field is in a good state.
    commit() {
      const typed = this.text.trim();
      if (typed === this.format(this.value)) {
        this.setError('');
        return true;
      }
      if (typed === '') {
        this.commitValue('');
        return true;
      }
      const iso = Z.parseDate(typed, this.fmt.order);
      if (!iso) {
        this.setError(`Enter a date as ${this.placeholder}, for example ${this.format(todayIso())}.`);
        return false;
      }
      if (this.min && iso < this.min) {
        this.setError(`Choose ${this.format(this.min)} or later.`);
        return false;
      }
      if (this.max && iso > this.max) {
        this.setError(`Choose ${this.format(this.max)} or earlier.`);
        return false;
      }
      this.commitValue(iso);
      this.showMonthOf(iso);
      return true;
    },

    // Enter reads the text; a good date lets the form submit as usual, a bad one stays put.
    onEnter(event) {
      if (!this.commit()) event.preventDefault();
    },

    showMonthOf(isoDate) {
      const [y, m] = isoDate.split('-').map(Number);
      this.year = y;
      this.month = m - 1;
    },

    toggle() {
      if (this.disabled) return;
      this.open = !this.open;
      if (this.open) this.showMonthOf(this.value || todayIso());
    },

    get monthLabel() {
      return `${Z.MONTH_NAMES[this.month]} ${this.year}`;
    },

    // Calendar cells, Monday first; null pads the first week.
    get cells() {
      const lead = (new Date(this.year, this.month, 1).getDay() + 6) % 7;
      const days = new Date(this.year, this.month + 1, 0).getDate();
      const today = todayIso();
      const cells = Array(lead).fill(null);
      for (let day = 1; day <= days; day++) {
        const value = Z.isoDate(this.year, this.month + 1, day);
        cells.push({
          day,
          value,
          selected: value === this.value,
          today: value === today,
          disabled: Boolean((this.min && value < this.min) || (this.max && value > this.max)),
        });
      }
      return cells;
    },

    shiftMonth(delta) {
      const d = new Date(this.year, this.month + delta, 1);
      this.year = d.getFullYear();
      this.month = d.getMonth();
    },

    pick(cell) {
      if (!cell || cell.disabled) return;
      this.commitValue(cell.value);
      this.open = false;
    },

    goToday() {
      const today = todayIso();
      if ((this.min && today < this.min) || (this.max && today > this.max)) return;
      this.commitValue(today);
      this.open = false;
    },

    clear() {
      this.commitValue('');
      this.open = false;
    },
  }));
});
