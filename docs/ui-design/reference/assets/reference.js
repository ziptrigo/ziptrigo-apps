/*
 * Shared setup for the UI reference pages in docs/ui-design/reference/.
 *
 * Load it right after the Tailwind Play CDN script. It:
 *   1. applies the same Tailwind config as apps/core/templates/core/base.html (sage palette,
 *      brand-* aliases, class-based dark mode),
 *   2. applies the saved theme before first paint and wires the header theme toggle,
 *   3. wires the demo behaviours (dropdowns, modals, search clear) the way the qr_code pages do,
 *   4. renders an "HTML" disclosure under every [data-example] block, generated from the live
 *      markup so the snippet can never drift from what is shown.
 *
 * These pages are documentation only. The site itself never loads this file.
 */

tailwind.config = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        sage: {
          50: '#f4f7f6',
          100: '#d4e0da',
          200: '#b5c7be',
          300: '#8fa89e',
          400: '#728e84',
          500: '#5a736a',
          600: '#475a53',
          700: '#3b4a47',
          800: '#2c3432',
          900: '#1e2422'
        },
        brand: {
          primary: '#8fa89e',
          dark: '#2c3432'
        }
      }
    }
  }
};

(function () {
  const STORAGE_KEY = 'ui-reference-theme';
  const mql = window.matchMedia('(prefers-color-scheme: dark)');

  function currentMode() {
    try {
      return localStorage.getItem(STORAGE_KEY) || 'system';
    } catch (_) {
      return 'system';
    }
  }

  function applyTheme(mode) {
    const dark = mode === 'dark' || (mode === 'system' && mql.matches);
    document.documentElement.classList.toggle('dark', dark);
    const icon = document.getElementById('theme-icon');
    if (icon && icon.dataset[mode]) icon.src = icon.dataset[mode];
    const label = document.getElementById('theme-label');
    if (label) label.textContent = mode;
  }

  // Before first paint, like the early theme boot in base.html.
  applyTheme(currentMode());
  mql.addEventListener('change', function () {
    if (currentMode() === 'system') applyTheme('system');
  });

  document.addEventListener('DOMContentLoaded', function () {
    applyTheme(currentMode());
    renderSnippets();
  });

  // ---- Snippets --------------------------------------------------------------------------

  function dedent(text) {
    const lines = text.replace(/^\s*\n/, '').replace(/\s+$/, '').split('\n');
    const indents = lines
      .filter(function (l) { return l.trim(); })
      .map(function (l) { return l.match(/^ */)[0].length; });
    const min = indents.length ? Math.min.apply(null, indents) : 0;
    return lines.map(function (l) { return l.slice(min); }).join('\n');
  }

  function renderSnippets() {
    document.querySelectorAll('[data-example]').forEach(function (el) {
      const details = document.createElement('details');
      details.className = 'mt-3 group';
      const summary = document.createElement('summary');
      summary.className =
        'cursor-pointer select-none text-xs font-medium text-gray-500 dark:text-gray-400 ' +
        'hover:text-gray-700 dark:hover:text-gray-200';
      summary.innerHTML = '<i class="fas fa-code mr-1"></i>HTML';
      const pre = document.createElement('pre');
      pre.className =
        'mt-2 p-4 overflow-x-auto rounded-md bg-gray-900 text-gray-100 text-xs leading-relaxed';
      const code = document.createElement('code');
      code.textContent = dedent(el.innerHTML).replace(/ data-demo-[a-z-]+(="[^"]*")?/g, '');
      pre.appendChild(code);
      details.appendChild(summary);
      details.appendChild(pre);
      el.insertAdjacentElement('afterend', details);
    });
  }

  // ---- Demo behaviours -------------------------------------------------------------------

  function closeAllDropdowns(except) {
    document.querySelectorAll('[data-demo-dropdown-menu]').forEach(function (menu) {
      if (menu !== except) menu.classList.add('hidden');
    });
  }

  function openModal(overlay) {
    overlay.classList.remove('hidden');
    requestAnimationFrame(function () {
      overlay.classList.remove('opacity-0');
      const panel = overlay.firstElementChild;
      if (panel) panel.classList.remove('scale-95');
    });
  }

  function closeModal(overlay) {
    overlay.classList.add('opacity-0');
    const panel = overlay.firstElementChild;
    if (panel) panel.classList.add('scale-95');
    setTimeout(function () { overlay.classList.add('hidden'); }, 150);
  }

  document.addEventListener('click', function (e) {
    const target = e.target instanceof Element ? e.target : null;
    if (!target) return;

    // Theme toggle: system -> light -> dark -> system, same cycle as base.html.
    if (target.closest('#theme-toggle')) {
      const cur = currentMode();
      const next = cur === 'system' ? 'light' : cur === 'light' ? 'dark' : 'system';
      try { localStorage.setItem(STORAGE_KEY, next); } catch (_) { /* ignore */ }
      applyTheme(next);
      return;
    }

    // Dropdowns: the button is followed by its menu inside a `relative` wrapper.
    const ddBtn = target.closest('[data-demo-dropdown]');
    if (ddBtn) {
      e.stopPropagation();
      const menu = ddBtn.parentElement.querySelector('[data-demo-dropdown-menu]');
      const wasOpen = menu && !menu.classList.contains('hidden');
      closeAllDropdowns();
      if (menu && !wasOpen) {
        menu.classList.remove('hidden');
        // Flip above the button when the menu would overflow the viewport bottom.
        const rect = menu.getBoundingClientRect();
        const flip = rect.bottom > window.innerHeight;
        menu.style.top = flip ? 'auto' : '100%';
        menu.style.bottom = flip ? '100%' : 'auto';
        menu.style.marginTop = flip ? '0' : '0.5rem';
        menu.style.marginBottom = flip ? '0.5rem' : '0';
      }
      return;
    }
    if (!target.closest('[data-demo-dropdown-menu]')) closeAllDropdowns();

    // Modals.
    const opener = target.closest('[data-demo-modal-open]');
    if (opener) {
      const overlay = document.getElementById(opener.getAttribute('data-demo-modal-open'));
      if (overlay) {
        closeAllDropdowns();
        openModal(overlay);
      }
      return;
    }
    const closer = target.closest('[data-demo-modal-close]');
    if (closer) {
      closeModal(closer.closest('[data-demo-modal]'));
      return;
    }
    if (target.hasAttribute('data-demo-modal')) {
      closeModal(target); // click on the backdrop itself
      return;
    }

    // Search clear.
    const clear = target.closest('[data-demo-search-clear]');
    if (clear) {
      const input = clear.parentElement.querySelector('input');
      input.value = '';
      clear.classList.add('hidden');
      input.focus();
    }
  });

  document.addEventListener('input', function (e) {
    const input = e.target;
    if (!(input instanceof HTMLInputElement)) return;
    const clear = input.parentElement && input.parentElement.querySelector('[data-demo-search-clear]');
    if (clear) clear.classList.toggle('hidden', !input.value.trim());
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    closeAllDropdowns();
    document.querySelectorAll('[data-demo-modal]:not(.hidden)').forEach(closeModal);
  });
})();
