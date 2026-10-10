// Alpine component behind core/components/date_picker.html. Dates are plain 'YYYY-MM-DD'
// strings throughout (never Date objects across the UTC boundary), so a day never shifts.
document.addEventListener('alpine:init', () => {
  const pad = (n) => String(n).padStart(2, '0');
  const iso = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`;
  const todayIso = () => {
    const t = new Date();
    return iso(t.getFullYear(), t.getMonth(), t.getDate());
  };

  Alpine.data('datePicker', () => ({
    open: false,
    value: '',
    min: '',
    max: '',
    year: 0,
    month: 0, // 0-based
    weekdays: ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'],

    init() {
      const d = this.$el.dataset;
      this.value = d.value || '';
      this.min = d.min || '';
      this.max = d.max || '';
      this.showMonthOf(this.value || todayIso());
    },

    showMonthOf(isoDate) {
      const [y, m] = isoDate.split('-').map(Number);
      this.year = y;
      this.month = m - 1;
    },

    toggle() {
      this.open = !this.open;
      if (this.open) this.showMonthOf(this.value || todayIso());
    },

    get display() {
      if (!this.value) return 'Select a date';
      const [y, m, d] = this.value.split('-').map(Number);
      return new Date(y, m - 1, d).toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      });
    },

    get monthLabel() {
      return new Date(this.year, this.month, 1).toLocaleDateString(undefined, {
        month: 'long', year: 'numeric',
      });
    },

    // Calendar cells, Monday first; null pads the first week.
    get cells() {
      const lead = (new Date(this.year, this.month, 1).getDay() + 6) % 7;
      const days = new Date(this.year, this.month + 1, 0).getDate();
      const today = todayIso();
      const cells = Array(lead).fill(null);
      for (let day = 1; day <= days; day++) {
        const value = iso(this.year, this.month, day);
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
      this.value = cell.value;
      this.open = false;
    },

    goToday() {
      const today = todayIso();
      if ((this.min && today < this.min) || (this.max && today > this.max)) return;
      this.value = today;
      this.open = false;
    },

    clear() {
      this.value = '';
      this.open = false;
    },
  }));
});
