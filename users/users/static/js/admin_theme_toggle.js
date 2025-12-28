(function () {
  'use strict';

  const STORAGE_KEY = 'qr_code_admin_theme';
  const Theme = {
    LIGHT: 'light',
    DARK: 'dark',
  };

  function getStoredTheme() {
    try {
      const value = window.localStorage.getItem(STORAGE_KEY);
      if (value === Theme.DARK || value === Theme.LIGHT) {
        return value;
      }
    } catch {
      // Ignore storage errors.
    }
    return null;
  }

  function setStoredTheme(theme) {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Ignore storage errors.
    }
  }

  function getDarkThemeLink() {
    return document.getElementById('jazzmin-dark-mode-theme');
  }

  function applyTheme(theme) {
    const body = document.body;
    const darkThemeLink = getDarkThemeLink();

    if (theme === Theme.DARK) {
      body.classList.add('dark-mode');
      if (darkThemeLink) {
        // Override the prefers-color-scheme media query.
        darkThemeLink.media = 'all';
      }
      return;
    }

    body.classList.remove('dark-mode');
    if (darkThemeLink) {
      // Effectively disable without removing the element.
      darkThemeLink.media = 'not all';
    }
  }

  function nextTheme(theme) {
    return theme === Theme.DARK ? Theme.LIGHT : Theme.DARK;
  }

  function currentThemeFromDom() {
    return document.body.classList.contains('dark-mode') ? Theme.DARK : Theme.LIGHT;
  }

  function setToggleIcon(linkEl, theme) {
    const icon = linkEl.querySelector('i');
    if (!icon) {
      return;
    }

    if (theme === Theme.DARK) {
      icon.className = 'fas fa-moon';
      linkEl.title = 'Dark mode (click to switch to light)';
    } else {
      icon.className = 'fas fa-sun';
      linkEl.title = 'Light mode (click to switch to dark)';
    }
  }

  function insertToggle() {
    const navbarList = document.querySelector('#jazzy-navbar ul.navbar-nav.ml-auto');
    if (!navbarList) {
      return;
    }

    const li = document.createElement('li');
    li.className = 'nav-item';

    const a = document.createElement('a');
    a.className = 'nav-link';
    a.href = '#';
    a.setAttribute('role', 'button');
    a.setAttribute('aria-label', 'Toggle light/dark mode');

    const icon = document.createElement('i');
    icon.className = 'fas fa-sun';
    a.appendChild(icon);

    a.addEventListener('click', function (event) {
      event.preventDefault();

      const current = currentThemeFromDom();
      const next = nextTheme(current);

      applyTheme(next);
      setStoredTheme(next);
      setToggleIcon(a, next);
    });

    li.appendChild(a);

    // Insert near the right side, before the user menu if present.
    const userMenu = navbarList.querySelector('#jazzy-usermenu');
    if (userMenu && userMenu.parentElement && userMenu.parentElement.classList.contains('dropdown')) {
      navbarList.insertBefore(li, userMenu.parentElement);
    } else {
      navbarList.appendChild(li);
    }

    setToggleIcon(a, currentThemeFromDom());
  }

  document.addEventListener('DOMContentLoaded', function () {
    // Default to light; if user previously chose a theme, apply it.
    const stored = getStoredTheme();
    if (stored) {
      applyTheme(stored);
    } else {
      // Ensure we don't accidentally force dark due to a stale stylesheet.
      applyTheme(Theme.LIGHT);
    }

    insertToggle();
  });
})();
