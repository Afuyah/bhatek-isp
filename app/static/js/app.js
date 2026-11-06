/* Application shell behaviour — vanilla JS, no jQuery.
   Navbar scroll, mobile menu, smooth anchors, toasts, global loader. */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------- navbar: shadow on scroll + auto-hide ---------------- */
  function initNavbar() {
    var navbar = document.getElementById('navbar');
    if (!navbar) return;
    var lastScroll = 0;
    var ticking = false;

    function update() {
      var y = window.scrollY || window.pageYOffset;
      navbar.classList.toggle('navbar-raised', y > 30);
      var hidden = y > lastScroll && y > 120;
      navbar.classList.toggle('navbar-hidden', hidden && !reduceMotion);
      lastScroll = y;
      ticking = false;
    }

    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
    }, { passive: true });

    navbar.addEventListener('mouseenter', function () {
      navbar.classList.remove('navbar-hidden');
    });
  }

  /* ---------------- mobile menu ---------------- */
  function initMobileMenu() {
    var button = document.getElementById('mobileMenuButton');
    var menu = document.getElementById('mobileMenu');
    if (!button || !menu) return;

    function setOpen(open) {
      menu.classList.toggle('menu-open', open);
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
      button.classList.toggle('bg-gray-200', open);
    }

    button.addEventListener('click', function () {
      setOpen(!menu.classList.contains('menu-open'));
    });

    menu.addEventListener('click', function (event) {
      if (event.target.closest('a')) setOpen(false);
    });
  }

  /* ---------------- smooth scroll for same-page anchors ---------------- */
  function initSmoothScroll() {
    document.addEventListener('click', function (event) {
      var link = event.target.closest('a[href^="#"]');
      if (!link) return;
      var id = link.getAttribute('href');
      if (!id || id === '#') return;
      var target = document.querySelector(id);
      if (!target) return;
      event.preventDefault();
      var top = target.getBoundingClientRect().top + window.scrollY - 85;
      window.scrollTo({ top: top, behavior: reduceMotion ? 'auto' : 'smooth' });
    });
  }

  /* ---------------- toasts ---------------- */
  var TOAST_STYLE = {
    success: 'from-green-500 to-green-600',
    error: 'from-red-500 to-red-600',
    warning: 'from-yellow-500 to-orange-500',
    info: 'from-blue-500 to-blue-600'
  };
  var TOAST_ICON = { success: 'check', error: 'alert', warning: 'alert', info: 'info' };

  window.showToast = function (message, type, duration) {
    type = TOAST_STYLE[type] ? type : 'success';
    duration = duration || 4000;

    var container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container fixed bottom-6 right-6 z-50 space-y-3';
      document.body.appendChild(container);
    }

    var el = document.createElement('div');
    el.className = 'toast toast-animation';
    el.setAttribute('role', 'alert');
    el.innerHTML =
      '<div class="bg-gradient-to-r ' + TOAST_STYLE[type] + ' rounded-xl shadow-2xl overflow-hidden min-w-[280px] max-w-md">' +
        '<div class="relative p-4">' +
          '<div class="flex items-start gap-3">' +
            '<span class="text-white text-lg">' + window.icon(TOAST_ICON[type]) + '</span>' +
            '<div class="flex-1 text-white text-sm font-medium leading-relaxed"></div>' +
            '<button class="toast-close text-white/70 hover:text-white text-xl leading-none" aria-label="Dismiss">&times;</button>' +
          '</div>' +
          '<div class="absolute bottom-0 left-0 h-1 bg-white/40 toast-progress" style="animation-duration:' + duration + 'ms"></div>' +
        '</div>' +
      '</div>';
    el.querySelector('.text-sm').textContent = String(message);

    function close() {
      el.classList.add('toast-out');
      window.setTimeout(function () { el.remove(); }, 300);
    }

    el.querySelector('.toast-close').addEventListener('click', close);
    container.appendChild(el);
    window.setTimeout(close, duration);
  };

  /* ---------------- global loading overlay ---------------- */
  window.showLoading = function (message) {
    if (document.getElementById('globalLoading')) return;
    var el = document.createElement('div');
    el.id = 'globalLoading';
    el.className = 'fixed inset-0 bg-black/50 backdrop-blur-sm z-[9999] flex items-center justify-center animate-fade-in';
    el.innerHTML =
      '<div class="bg-white rounded-2xl p-8 shadow-2xl text-center max-w-sm mx-4">' +
        '<div class="loading-spinner mx-auto mb-4"></div>' +
        '<p class="text-gray-700 font-medium text-sm">' + String(message || 'Loading…') + '</p>' +
      '</div>';
    document.body.appendChild(el);
  };

  window.hideLoading = function () {
    var el = document.getElementById('globalLoading');
    if (!el) return;
    el.classList.add('animate-fade-out');
    window.setTimeout(function () { el.remove(); }, 200);
  };

  /* ---------------- boot ---------------- */
  function init() {
    initNavbar();
    initMobileMenu();
    initSmoothScroll();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
