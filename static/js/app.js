/* HomeFit — front-end glue */

// Register the PWA service worker so iPhone users can install to the home screen.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) =>
      console.warn('SW registration failed:', err)
    );
  });
}

/* ---------- Weight logging (dashboard) ---------- */
function setupWeightForm() {
  const form = document.getElementById('weight-form');
  const status = document.getElementById('weight-status');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const weight = parseFloat(form.weight.value);
    if (!weight || weight <= 0) return;
    status.textContent = 'Saving…';
    try {
      const res = await fetch('/api/log_weight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ weight }),
      });
      const data = await res.json();
      status.textContent = data.ok ? 'Logged ✓' : 'Could not save.';
    } catch (err) {
      status.textContent = 'Could not reach server.';
    }
  });
}

/* ---------- Active workout ---------- */
function startWorkout() {
  const root = document.getElementById('workout-body');
  if (!root) return;

  // Running workout timer
  const timerEl = document.getElementById('workout-timer');
  const started = Date.now();
  const tickTimer = () => {
    const s = Math.floor((Date.now() - started) / 1000);
    const m = String(Math.floor(s / 60)).padStart(2, '0');
    const r = String(s % 60).padStart(2, '0');
    timerEl.textContent = `${m}:${r}`;
  };
  tickTimer();
  const timerInterval = setInterval(tickTimer, 1000);

  // Rest timer overlay
  const overlay = document.getElementById('rest-overlay');
  const countEl = document.getElementById('rest-count');
  const skipBtn = document.getElementById('rest-skip');
  let restInterval = null;

  const stopRest = () => {
    clearInterval(restInterval);
    restInterval = null;
    overlay.classList.add('hidden');
  };
  skipBtn.addEventListener('click', stopRest);

  root.querySelectorAll('.rest-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const seconds = parseInt(btn.dataset.rest, 10) || 30;
      let remaining = seconds;
      countEl.textContent = String(remaining).padStart(2, '0');
      overlay.classList.remove('hidden');
      if (restInterval) clearInterval(restInterval);
      restInterval = setInterval(() => {
        remaining -= 1;
        countEl.textContent = String(Math.max(0, remaining)).padStart(2, '0');
        if (remaining <= 0) {
          stopRest();
          // small vibration on iPhone Safari (if allowed)
          if ('vibrate' in navigator) navigator.vibrate(200);
        }
      }, 1000);
    });
  });

  // Finish workout
  const finishBtn = document.getElementById('finish-btn');
  finishBtn.addEventListener('click', async () => {
    clearInterval(timerInterval);
    const duration = Math.floor((Date.now() - started) / 1000);
    const items = Array.from(root.querySelectorAll('.exercise-item')).map((li) => ({
      id: li.dataset.exerciseId,
      completed: li.querySelector('.ex-done').checked,
    }));
    const payload = {
      day_number: parseInt(root.dataset.dayNumber, 10),
      day_name: root.dataset.dayName,
      duration_seconds: duration,
      exercises: items,
    };
    finishBtn.disabled = true;
    finishBtn.textContent = 'Saving…';
    try {
      await fetch('/api/complete_workout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      window.location.href = '/progress';
    } catch (err) {
      finishBtn.disabled = false;
      finishBtn.textContent = 'Retry finish';
    }
  });
}

/* ---------- Exercise library filtering ---------- */
function setupLibraryFilter() {
  const search = document.getElementById('ex-search');
  const filter = document.getElementById('ex-filter');
  const items = document.querySelectorAll('.library-list .exercise-item');
  if (!search || !filter) return;
  const apply = () => {
    const q = search.value.trim().toLowerCase();
    const cat = filter.value;
    items.forEach((li) => {
      const matchCat = cat === 'all' || li.dataset.category === cat;
      const matchQ = !q || li.dataset.name.includes(q);
      li.style.display = matchCat && matchQ ? '' : 'none';
    });
  };
  search.addEventListener('input', apply);
  filter.addEventListener('change', apply);
}

/* ---------- Simple weight chart (no external libs) ---------- */
function drawWeightChart() {
  const canvas = document.getElementById('weight-chart');
  if (!canvas) return;
  const points = JSON.parse(canvas.dataset.points || '[]');
  if (points.length < 1) return;

  // High-DPI support
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 600;
  const cssH = 220;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.height = cssH + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const padding = { t: 16, r: 16, b: 26, l: 36 };
  const innerW = cssW - padding.l - padding.r;
  const innerH = cssH - padding.t - padding.b;

  const weights = points.map((p) => p.weight);
  const min = Math.min(...weights) - 1;
  const max = Math.max(...weights) + 1;
  const range = Math.max(1, max - min);

  // Axis
  ctx.strokeStyle = '#263859';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.l, padding.t);
  ctx.lineTo(padding.l, padding.t + innerH);
  ctx.lineTo(padding.l + innerW, padding.t + innerH);
  ctx.stroke();

  // Y labels
  ctx.fillStyle = '#94a3b8';
  ctx.font = '11px -apple-system, system-ui, sans-serif';
  for (let i = 0; i <= 4; i++) {
    const v = min + (range * i) / 4;
    const y = padding.t + innerH - (innerH * i) / 4;
    ctx.fillText(v.toFixed(1), 4, y + 4);
    ctx.strokeStyle = 'rgba(38, 56, 89, 0.4)';
    ctx.beginPath();
    ctx.moveTo(padding.l, y);
    ctx.lineTo(padding.l + innerW, y);
    ctx.stroke();
  }

  // Line
  ctx.strokeStyle = '#22d3ee';
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = padding.l + (innerW * i) / Math.max(1, points.length - 1);
    const y = padding.t + innerH - ((p.weight - min) / range) * innerH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots
  ctx.fillStyle = '#38bdf8';
  points.forEach((p, i) => {
    const x = padding.l + (innerW * i) / Math.max(1, points.length - 1);
    const y = padding.t + innerH - ((p.weight - min) / range) * innerH;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });
}
