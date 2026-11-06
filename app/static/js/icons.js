/* Local icon helper — renders from the inline SVG sprite (#i-<name>).
   No icon font, no network request. Accepts either a sprite name ('check')
   or a legacy Font Awesome name ('fa-check-circle'), which is normalised. */
(function () {
  'use strict';

  var ALIASES = {
    'check-circle': 'check', 'circle-check': 'check', 'check-double': 'check',
    'times': 'x', 'times-circle': 'x', 'close': 'x',
    'exclamation-circle': 'alert', 'exclamation-triangle': 'alert', 'triangle-exclamation': 'alert',
    'info-circle': 'info', 'circle-info': 'info', 'question-circle': 'info',
    'sync-alt': 'refresh', 'spinner': 'refresh', 'rotate': 'refresh',
    'chart-line': 'chart', 'chart-pie': 'chart', 'tachometer-alt': 'chart',
    'trash-alt': 'trash', 'pencil-alt': 'edit',
    'sign-out-alt': 'logout', 'sliders-h': 'settings', 'bars': 'menu',
    'credit-card': 'card', 'money-bill-wave': 'card',
    'map-marker-alt': 'pin', 'mobile-alt': 'phone',
    'envelope': 'mail', 'paper-plane': 'mail',
    'user-circle': 'user', 'user-friends': 'users', 'user-plus': 'users'
  };

  function normalise(name) {
    var n = String(name || '').trim().replace(/^fa[srb]?\s+/, '').replace(/^fa-/, '');
    return ALIASES[n] || n || 'circle';
  }

  function icon(name, cls) {
    return '<svg class="icon ' + (cls || '') + '" width="1em" height="1em" viewBox="0 0 24 24" ' +
      'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true" focusable="false">' +
      '<use href="#i-' + normalise(name) + '"></use></svg>';
  }

  window.icon = icon;
  window.iconName = normalise;
})();
