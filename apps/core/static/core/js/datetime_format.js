// The site's date and time wording in the browser (issue #245): the twin of
// apps/core/datetime_format.py, which does it on the server. The page says which wording it
// uses on <html data-date-format="mdy|dmy|public" data-time-format="12h|24h"> (a public page
// says "public": month as three letters, whatever the account's choice); the scripts that show or
// read a date or a time (local_time.js, the date and time fields, the repeat control) use this
// instead of the browser's locale. Everything is plain numbers and strings, never a Date object
// across a time zone, so a day never shifts.
(function () {
    const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
        'September', 'October', 'November', 'December'];
    const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

    function pad(n) {
        return String(n).padStart(2, '0');
    }

    // `source` is an element carrying data-date-format / data-time-format (a <time> from
    // {% local_time %} does), else <html>.
    function formatsFor(source) {
        const own = source && source.dataset ? source.dataset : {};
        const root = document.documentElement.dataset;
        return {
            order: own.dateFormat || root.dateFormat || 'mdy',
            clock: own.timeFormat || root.timeFormat || '12h',
        };
    }

    // The order a numeric date is typed in: the user's, mdy on a public page.
    function numericOrder(formats) {
        return formats.order === 'dmy' ? 'dmy' : 'mdy';
    }

    // Day-of-week (0 = Sunday) of a calendar date, with no time zone involved.
    function weekday(y, m, d) {
        return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    }

    // style: full | compact | weekday | heading. `currentYear` is the year `compact` and
    // `heading` leave out (default: this year in the browser's zone).
    function formatDate(y, m, d, style, formats, currentYear) {
        formats = formats || formatsFor();
        style = style || 'full';
        const year = currentYear || new Date().getFullYear();
        const showYear = style === 'full' || style === 'weekday' || y !== year;
        const wd = WEEKDAYS[weekday(y, m, d)];
        let text;
        if (formats.order === 'public') {
            text = `${MONTH_ABBR[m - 1]} ${d}` + (showYear ? `, ${y}` : '');
        } else {
            const parts = formats.order === 'dmy' ? [d, m] : [m, d];
            text = `${pad(parts[0])}/${pad(parts[1])}` + (showYear ? `/${y}` : '');
        }
        if (style === 'weekday') return `${wd.slice(0, 3)} ${text}`;
        if (style === 'heading') return `${wd}, ${text}`;
        return text;
    }

    function formatTime(hour, minute, formats) {
        formats = formats || formatsFor();
        if (formats.clock === '24h') return `${pad(hour)}:${pad(minute)}`;
        return `${hour % 12 || 12}:${pad(minute)} ${hour < 12 ? 'AM' : 'PM'}`;
    }

    function formatIsoDate(iso, style, formats) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
        if (!m) return '';
        return formatDate(Number(m[1]), Number(m[2]), Number(m[3]), style, formats);
    }

    function formatIsoTime(iso, formats) {
        const m = /^(\d{1,2}):(\d{2})/.exec(iso || '');
        if (!m) return '';
        return formatTime(Number(m[1]), Number(m[2]), formats);
    }

    function monthDayWords(month, day, formats) {
        formats = formats || formatsFor();
        return formats.order === 'dmy' ? `${day} ${MONTH_NAMES[month - 1]}` : `${MONTH_NAMES[month - 1]} ${day}`;
    }

    function validDate(y, m, d) {
        if (!(y >= 1 && y <= 9999 && m >= 1 && m <= 12 && d >= 1)) return false;
        return d <= new Date(Date.UTC(y, m, 0)).getUTCDate();
    }

    function isoDate(y, m, d) {
        return `${String(y).padStart(4, '0')}-${pad(m)}-${pad(d)}`;
    }

    // Reads a typed date: the user's order with "/", "-" or "." between 1- or 2-digit day and
    // month and a 2- or 4-digit year, or ISO (2026-11-05). Returns "YYYY-MM-DD", or null when it
    // isn't a real date. A two-digit year means 20yy.
    function parseDate(text, order) {
        const value = String(text || '').trim();
        let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value);
        if (m) {
            const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
            return validDate(y, mo, d) ? isoDate(y, mo, d) : null;
        }
        m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})$/.exec(value);
        if (!m) return null;
        const first = Number(m[1]);
        const second = Number(m[2]);
        let y = Number(m[3]);
        if (m[3].length === 2) y += 2000;
        const [mo, d] = order === 'dmy' ? [second, first] : [first, second];
        return validDate(y, mo, d) ? isoDate(y, mo, d) : null;
    }

    // Reads a typed time: "22:00", "2200", "10pm", "10:30 p", "10.30pm", "9". Returns "HH:MM"
    // or null. Without am/pm a bare hour is on the 24-hour clock ("9" is 09:00).
    function parseTime(text) {
        let value = String(text || '').trim().toLowerCase().replace(/\s+/g, '');
        if (!value) return null;
        let meridiem = null;
        const suffix = /(a\.?m?\.?|p\.?m?\.?)$/.exec(value);
        if (suffix) {
            meridiem = suffix[1][0];
            value = value.slice(0, suffix.index);
        }
        let hour;
        let minute = 0;
        let m = /^(\d{1,2})[:.](\d{2})$/.exec(value);
        if (m) {
            hour = Number(m[1]);
            minute = Number(m[2]);
        } else if ((m = /^(\d{1,2})$/.exec(value))) {
            hour = Number(m[1]);
        } else if ((m = /^(\d)(\d{2})$/.exec(value)) || (m = /^(\d{2})(\d{2})$/.exec(value))) {
            hour = Number(m[1]);
            minute = Number(m[2]);
        } else {
            return null;
        }
        if (minute > 59) return null;
        if (meridiem) {
            if (hour < 1 || hour > 12) return null;
            hour = (hour % 12) + (meridiem === 'p' ? 12 : 0);
        } else if (hour > 23) {
            return null;
        }
        return `${pad(hour)}:${pad(minute)}`;
    }

    window.ZTDateTime = {
        MONTH_NAMES,
        formatsFor,
        numericOrder,
        formatDate,
        formatTime,
        formatIsoDate,
        formatIsoTime,
        monthDayWords,
        parseDate,
        parseTime,
        validDate,
        isoDate,
        pad,
    };
})();
