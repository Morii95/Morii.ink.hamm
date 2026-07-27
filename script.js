/* ==========================================================================
   MORI INK HAMM — script.js
   Mobiles Menü, barrierefrei: echter Button, aria-expanded, Esc,
   Fokus-Falle, Scroll-Sperre. Ersetzt das frühere inline onclick="toggleMenu()".
   ========================================================================== */

(function () {
  'use strict';

  var toggle = document.getElementById('nav-toggle');
  var menu = document.getElementById('mobile-menu');
  var closeBtn = document.getElementById('menu-close');

  if (!toggle || !menu) return;

  var FOCUSABLE = 'a[href], button:not([disabled])';
  var lastFocused = null;

  function isOpen() {
    return menu.getAttribute('data-open') === 'true';
  }

  function openMenu() {
    lastFocused = document.activeElement;
    menu.setAttribute('data-open', 'true');
    toggle.setAttribute('aria-expanded', 'true');
    document.body.classList.add('menu-open');

    var first = menu.querySelector(FOCUSABLE);
    if (first) first.focus();
  }

  function closeMenu() {
    menu.setAttribute('data-open', 'false');
    toggle.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('menu-open');

    // Fokus zurück auf den auslösenden Button, nicht ins Nichts
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    } else {
      toggle.focus();
    }
  }

  toggle.addEventListener('click', function () {
    if (isOpen()) { closeMenu(); } else { openMenu(); }
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', closeMenu);
  }

  // Menü schließt beim Navigieren — der Sprung soll sichtbar sein
  menu.addEventListener('click', function (event) {
    var link = event.target.closest('a[href]');
    if (link) closeMenu();
  });

  document.addEventListener('keydown', function (event) {
    if (!isOpen()) return;

    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu();
      return;
    }

    // Fokus im offenen Menü halten
    if (event.key === 'Tab') {
      var items = Array.prototype.slice.call(menu.querySelectorAll(FOCUSABLE));
      if (!items.length) return;

      var first = items[0];
      var last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  // Wird auf Desktop-Breite vergrößert, während das Menü offen ist:
  // schließen, sonst bleibt ein unsichtbares Overlay aktiv.
  var desktop = window.matchMedia('(min-width: 901px)');
  var onChange = function (event) {
    if (event.matches && isOpen()) closeMenu();
  };

  if (typeof desktop.addEventListener === 'function') {
    desktop.addEventListener('change', onChange);
  } else if (typeof desktop.addListener === 'function') {
    desktop.addListener(onChange); // Safari < 14
  }
})();
