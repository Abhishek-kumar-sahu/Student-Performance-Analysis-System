/* ══════════════════════════════════════════════════════════
   SPAS v5 — spas.js  (chart helpers + login canvas)
   Core UI code now lives in base.html for performance.
   Chart.js defaults are set per-page after CDN loads.
   ══════════════════════════════════════════════════════════ */

function buildChart(id, type, labels, datasets, options={}) {
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  const merged = {
    plugins: {
      legend: { labels: { color: '#8b949e' } },
      tooltip: {
        backgroundColor: '#161b22',
        borderColor:      'rgba(59,130,246,.3)',
        borderWidth:      1,
        titleColor:       '#58a6ff',
        bodyColor:        '#c9d1d9',
        padding:          10,
      },
    },
    scales: (type !== 'doughnut' && type !== 'pie') ? {
      x: { grid: { color: 'rgba(139,148,158,.08)' }, ticks: { color: '#8b949e' } },
      y: { grid: { color: 'rgba(139,148,158,.08)' }, ticks: { color: '#8b949e' } },
    } : {},
    animation: { duration: 900, easing: 'easeInOutQuart' },
    ...options,
  };
  return new Chart(ctx, { type, data: { labels, datasets }, options: merged });
}

/* ── Login page — floating binary particles ──────────────── */
(function initLoginCanvas() {
  const canvas = document.getElementById('loginCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W, H, pts = [];

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  for (let i = 0; i < 70; i++) {
    pts.push({
      x: Math.random() * 1200, y: Math.random() * 900,
      r: Math.random() * 1.4 + 0.5,
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4,
      a:  Math.random() * 0.5 + 0.15,
      ch: String.fromCharCode(48 + Math.floor(Math.random() * 10)),
    });
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(13,17,23,.88)';
    ctx.fillRect(0, 0, W, H);
    pts.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > W) p.vx *= -1;
      if (p.y < 0 || p.y > H) p.vy *= -1;
      ctx.font = `${p.r * 9}px 'JetBrains Mono'`;
      ctx.fillStyle = `rgba(59,130,246,${p.a * 0.18})`;
      ctx.fillText(p.ch, p.x, p.y);
    });
    requestAnimationFrame(draw);
  }
  draw();
})();

