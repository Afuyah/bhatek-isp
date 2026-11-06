/* Minimal drop-in replacement for the Chart.js calls used in this app.
 *
 * Supports line, bar, doughnut and pie charts, rendering to inline SVG.
 * API-compatible where this codebase uses it:
 *     const ctx = canvas.getContext('2d');
 *     const c = new Chart(ctx, { type, data: { labels, datasets }, options });
 *     c.destroy(); c.update();
 *
 * No canvas 2D context is used, no external library, no network request.
 * Charts are responsive: they re-render on container resize.
 */
(function () {
  'use strict';

  var PALETTE = ['#1a56db', '#2d6a4f', '#f59e0b', '#ef4444', '#8b5cf6',
                 '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#64748b'];

  function colorAt(value, index) {
    if (Array.isArray(value)) return value[index % value.length];
    return value || PALETTE[index % PALETTE.length];
  }

  function num(value) {
    var n = Number(value);
    return isFinite(n) ? n : 0;
  }

  function escapeText(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function niceMax(value) {
    if (value <= 0) return 1;
    var magnitude = Math.pow(10, Math.floor(Math.log10(value)));
    var scaled = value / magnitude;
    var step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return step * magnitude;
  }

  function SvgCanvas(canvas, width, height) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', height);
    svg.setAttribute('role', 'img');
    svg.setAttribute('class', 'local-chart');
    if (canvas.getAttribute('aria-label')) svg.setAttribute('aria-label', canvas.getAttribute('aria-label'));
    /* keep the canvas in the DOM (hidden) so we can measure and re-render */
    canvas.style.display = 'none';
    canvas.parentNode.insertBefore(svg, canvas.nextSibling);
    return svg;
  }

  function el(name, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (var key in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, key)) node.setAttribute(key, attrs[key]);
    }
    return node;
  }

  function drawEmpty(svg, width, height) {
    svg.appendChild(el('text', {
      x: width / 2, y: height / 2, 'text-anchor': 'middle',
      fill: '#94a3b8', 'font-size': '12', 'font-family': 'inherit'
    })).textContent = 'No data yet';
  }

  function drawAxes(svg, box, max, labels, height) {
    var grid = el('g', { stroke: '#eef2f7', 'stroke-width': '1' });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (fraction) {
      var y = box.bottom - (box.bottom - box.top) * fraction;
      grid.appendChild(el('line', { x1: box.left, y1: y, x2: box.right, y2: y }));
    });
    svg.appendChild(grid);

    var every = Math.max(1, Math.ceil(labels.length / 6));
    labels.forEach(function (label, index) {
      if (index % every !== 0) return;
      var x = labels.length === 1
        ? (box.left + box.right) / 2
        : box.left + ((box.right - box.left) * index) / (labels.length - 1);
      var text = el('text', {
        x: x, y: height - 6, 'text-anchor': 'middle',
        fill: '#94a3b8', 'font-size': '10', 'font-family': 'inherit'
      });
      text.textContent = escapeText(label);
      svg.appendChild(text);
    });
  }

  function drawLine(svg, box, labels, dataset, width, height) {
    var series = (dataset.data || []).map(num);
    var max = niceMax(Math.max.apply(null, series.concat([0])));
    drawAxes(svg, box, max, labels, height);

    var span = box.right - box.left;
    var points = series.map(function (value, index) {
      var x = series.length === 1 ? (box.left + box.right) / 2 : box.left + (span * index) / (series.length - 1);
      var y = box.bottom - ((box.bottom - box.top) * value) / max;
      return [x, y];
    });

    if (!points.length) { drawEmpty(svg, width, height); return; }

    var stroke = dataset.borderColor || PALETTE[0];
    var path = points.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');

    if (dataset.fill) {
      var area = path + ' L' + points[points.length - 1][0].toFixed(1) + ' ' + box.bottom +
                 ' L' + points[0][0].toFixed(1) + ' ' + box.bottom + ' Z';
      svg.appendChild(el('path', { d: area, fill: dataset.backgroundColor || 'rgba(45,106,79,0.08)', stroke: 'none' }));
    }

    svg.appendChild(el('path', {
      d: path, fill: 'none', stroke: stroke, 'stroke-width': '2',
      'stroke-linejoin': 'round', 'stroke-linecap': 'round'
    }));

    points.forEach(function (p) {
      svg.appendChild(el('circle', { cx: p[0], cy: p[1], r: '2.5', fill: stroke }));
    });
  }

  function drawBar(svg, box, labels, dataset, width, height) {
    var series = (dataset.data || []).map(num);
    var max = niceMax(Math.max.apply(null, series.concat([0])));
    drawAxes(svg, box, max, labels, height);

    var count = series.length;
    if (!count) { drawEmpty(svg, width, height); return; }
    var slot = (box.right - box.left) / count;
    var barWidth = Math.max(2, Math.min(42, slot * 0.6));

    series.forEach(function (value, index) {
      var x = box.left + slot * index + (slot - barWidth) / 2;
      var barHeight = ((box.bottom - box.top) * value) / max;
      svg.appendChild(el('rect', {
        x: x.toFixed(1), y: (box.bottom - barHeight).toFixed(1),
        width: barWidth.toFixed(1), height: Math.max(1, barHeight).toFixed(1),
        rx: '3', fill: colorAt(dataset.backgroundColor, index)
      }));
    });
  }

  function arcPath(cx, cy, radius, start, end) {
    var x1 = cx + radius * Math.cos(start), y1 = cy + radius * Math.sin(start);
    var x2 = cx + radius * Math.cos(end), y2 = cy + radius * Math.sin(end);
    var large = end - start > Math.PI ? 1 : 0;
    return 'M' + cx + ' ' + cy + ' L' + x1.toFixed(2) + ' ' + y1.toFixed(2) +
           ' A' + radius + ' ' + radius + ' 0 ' + large + ' 1 ' + x2.toFixed(2) + ' ' + y2.toFixed(2) + ' Z';
  }

  function drawCircular(svg, labels, dataset, width, height, doughnut) {
    var series = (dataset.data || []).map(num);
    var total = series.reduce(function (a, b) { return a + b; }, 0);
    if (!total) { drawEmpty(svg, width, height); return; }

    var cx = width / 2, cy = height / 2;
    var radius = Math.max(20, Math.min(width, height) / 2 - 8);
    var angle = -Math.PI / 2;

    series.forEach(function (value, index) {
      if (value <= 0) return;
      var sweep = (value / total) * Math.PI * 2;
      svg.appendChild(el('path', {
        d: arcPath(cx, cy, radius, angle, angle + sweep),
        fill: colorAt(dataset.backgroundColor, index),
        stroke: dataset.borderColor || '#fff',
        'stroke-width': dataset.borderWidth || 0
      }));
      angle += sweep;
    });

    if (doughnut) {
      svg.appendChild(el('circle', { cx: cx, cy: cy, r: radius * 0.58, fill: '#fff' }));
    }
  }

  function Chart(target, config) {
    this.canvas = target && target.canvas ? target.canvas : target;
    this.config = config || {};
    this.svg = null;
    this._onResize = this.render.bind(this);
    this.render();
    if (window.ResizeObserver && this.canvas && this.canvas.parentNode) {
      this._observer = new ResizeObserver(this._onResize);
      this._observer.observe(this.canvas.parentNode);
    }
  }

  Chart.prototype.render = function () {
    var canvas = this.canvas;
    if (!canvas || !canvas.parentNode) return;
    if (this.svg && this.svg.parentNode) this.svg.parentNode.removeChild(this.svg);

    var width = (canvas.parentNode && canvas.parentNode.clientWidth) ||
                parseInt(canvas.getAttribute('width'), 10) || 320;
    var height = parseInt(canvas.getAttribute('height'), 10) || 200;
    if (this._observer) this._observer.disconnect();

    var svg = SvgCanvas(canvas, width, height);
    this.svg = svg;

    var type = (this.config.type || 'line').toLowerCase();
    var data = this.config.data || {};
    var labels = data.labels || [];
    var dataset = (data.datasets && data.datasets[0]) || { data: [] };

    if (type === 'doughnut' || type === 'pie') {
      drawCircular(svg, labels, dataset, width, height, type === 'doughnut');
    } else {
      var box = { left: 34, right: width - 8, top: 10, bottom: height - 20 };
      if (type === 'bar') drawBar(svg, box, labels, dataset, width, height);
      else drawLine(svg, box, labels, dataset, width, height);
    }

    if (this._observer) this._observer.observe(canvas.parentNode);
  };

  Chart.prototype.update = function () { this.render(); };
  Chart.prototype.destroy = function () {
    if (this._observer) { this._observer.disconnect(); this._observer = null; }
    if (this.svg && this.svg.parentNode) this.svg.parentNode.removeChild(this.svg);
    this.svg = null;
  };

  window.Chart = Chart;
})();
