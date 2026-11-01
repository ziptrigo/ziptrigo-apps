/*
 * A light/dark toggle in the admin's top navbar.
 *
 * jazzmin 3 is Bootstrap 5: the colour scheme is `data-bs-theme` on <html>, which jazzmin's own
 * early script in `admin/base.html` sets from `localStorage['jazzmin-theme-mode']` before the page
 * paints. This toggle writes that same key, so the choice survives a reload with no flash. (It used
 * to look for jazzmin 2's `ul.navbar-nav.ml-auto` and `body.dark-mode`, so it never appeared.)
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'jazzmin-theme-mode';
  const LIGHT = 'light';
  const DARK = 'dark';

  function current() {
    return document.documentElement.getAttribute('data-bs-theme') === DARK ? DARK : LIGHT;
  }

  function apply(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Storage unavailable (private window): the choice lasts for this page only.
    }
  }

  function setIcon(link, theme) {
    const icon = link.querySelector('i');
    if (icon) icon.className = theme === DARK ? 'fas fa-moon' : 'fas fa-sun';
    link.title = theme === DARK ? 'Dark mode (click to switch to light)' : 'Light mode (click to switch to dark)';
    link.setAttribute('aria-pressed', theme === DARK ? 'true' : 'false');
  }

  function insertToggle() {
    const navbarList = document.querySelector('#jazzy-navbar ul.navbar-nav.ms-auto');
    if (!navbarList) return;

    const li = document.createElement('li');
    li.className = 'nav-item';
    const link = document.createElement('a');
    link.className = 'nav-link';
    link.href = '#';
    link.setAttribute('role', 'button');
    link.setAttribute('aria-label', 'Dark mode');
    link.appendChild(document.createElement('i'));
    link.addEventListener('click', function (event) {
      event.preventDefault();
      const next = current() === DARK ? LIGHT : DARK;
      apply(next);
      setIcon(link, next);
    });
    li.appendChild(link);

    // Before the user menu, when there is one.
    const userMenu = navbarList.querySelector('#jazzy-usermenu');
    const userItem = userMenu ? userMenu.closest('li') : null;
    if (userItem && userItem.parentElement === navbarList) {
      navbarList.insertBefore(li, userItem);
    } else {
      navbarList.appendChild(li);
    }
    setIcon(link, current());
  }

  document.addEventListener('DOMContentLoaded', insertToggle);
})();
