/* HomeFit — front-end glue */

// Register the PWA service worker so iPhone users can install to the home screen.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) =>
      console.warn('SW registration failed:', err)
    );
  });
}

/* ---------- Hamburger menu ---------- */
(function () {
  const btn = document.getElementById('hamburger-btn');
  const menu = document.getElementById('mobile-menu');
  const backdrop = document.getElementById('mobile-menu-backdrop');
  if (!btn || !menu) return;
  function close() {
    btn.classList.remove('open');
    menu.classList.remove('open');
    backdrop.classList.remove('open');
  }
  function open() {
    btn.classList.add('open');
    menu.classList.add('open');
    backdrop.classList.add('open');
  }
  btn.addEventListener('click', () => btn.classList.contains('open') ? close() : open());
  btn.addEventListener('touchend', (e) => { e.preventDefault(); btn.classList.contains('open') ? close() : open(); });
  backdrop.addEventListener('click', close);
})();

/* ---------- APEX floating coach panel ---------- */
(function () {
  const fab = document.getElementById('apex-fab');
  const panel = document.getElementById('apex-panel');
  const closeBtn = document.getElementById('apex-close');
  const clearBtn = document.getElementById('apex-clear-btn');
  const planBtn = null; // Now a link — handled by apex_plan.html
  const input = document.getElementById('apex-input');
  const sendBtn = document.getElementById('apex-send');
  const messages = document.getElementById('apex-messages');
  if (!fab || !panel) return;

  let history = [];
  let panelOpen = false;

  // Auto-expand textarea
  if (input) {
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });
  }

  async function loadHistory() {
    try {
      const resp = await fetch('/api/apex-chat');
      const data = await resp.json();
      if (data.messages && data.messages.length) {
        history = data.messages;
        messages.innerHTML = '';
        history.forEach(m => addMsg(m.content, m.role === 'assistant' ? 'apex' : m.role, false));
      }
    } catch {}
  }

  function togglePanel() {
    panelOpen = !panelOpen;
    panel.classList.toggle('open', panelOpen);
    fab.style.opacity = panelOpen ? '0.7' : '1';
    panel.setAttribute('aria-hidden', String(!panelOpen));
    if (panelOpen) {
      if (!history.length) loadHistory();
      setTimeout(() => input && input.focus(), 300);
    }
  }

  let lastToggle = 0;
  function safeToggle(e) {
    e.preventDefault();
    const now = Date.now();
    if (now - lastToggle < 300) return;
    lastToggle = now;
    togglePanel();
  }

  fab.addEventListener('click', safeToggle);
  fab.addEventListener('touchend', safeToggle);
  closeBtn && closeBtn.addEventListener('click', safeToggle);
  closeBtn && closeBtn.addEventListener('touchend', safeToggle);

  clearBtn && clearBtn.addEventListener('click', async () => {
    if (!confirm('Clear chat history?')) return;
    await fetch('/api/apex-chat/clear', { method: 'POST' });
    history = [];
    messages.innerHTML = '<div class="msg msg-apex">Chat cleared. Ask me anything — or say "create my weekly plan".</div>';
  });

  planBtn && planBtn.addEventListener('click', async () => {
    planBtn.disabled = true;
    planBtn.textContent = '⏳ Loading…';
    try {
      const resp = await fetch('/api/apex-plan');
      const data = await resp.json();
      if (data.plan && data.plan.length) {
        const today = new Date().getDay(); // 0=Sun
        const idx = today === 0 ? 6 : today - 1; // convert to Mon=0
        const day = data.plan[idx % data.plan.length];
        if (day.rest) {
          addMsg(`Today (Day ${idx + 1}) is a rest day: ${day.name}. Recovery is part of the plan!`, 'apex');
        } else {
          const postResp = await fetch('/api/apex-plan/today', { method: 'POST' });
          const postData = await postResp.json();
          if (postData.ok) {
            addMsg(`Loading today's plan: ${day.name}. Head to the workout screen!`, 'apex');
            setTimeout(() => window.location.href = '/today-workout', 1500);
          }
        }
      } else {
        addMsg('No weekly plan yet. Say "create my weekly plan" and I\'ll build one for you!', 'apex');
      }
    } catch {
      addMsg('Could not load plan.', 'apex');
    }
    planBtn.disabled = false;
    planBtn.textContent = '📅 My Plan';
  });

  function renderMarkdown(text) {
    return text
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/^### (.+)$/gm, '<strong>$1</strong>')
      .replace(/^## (.+)$/gm, '<strong>$1</strong>')
      .replace(/^- (.+)$/gm, '• $1')
      .replace(/\n/g, '<br>');
  }

  function addMsg(text, role, scroll = true) {
    const div = document.createElement('div');
    div.className = `msg msg-${role === 'user' ? 'user' : 'apex'}`;
    div.innerHTML = renderMarkdown(text);
    messages.appendChild(div);
    if (scroll) messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function addPlanBanner() {
    const div = document.createElement('div');
    div.style.cssText = 'padding:8px 12px;';
    div.innerHTML = '<a href="/apex-plan" style="display:block;text-align:center;padding:10px;background:var(--accent);color:#fff;border-radius:12px;font-weight:600;text-decoration:none;">📅 View My Plan →</a>';
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
  }

  async function send() {
    const text = input.value.trim();
    if (!text || sendBtn.disabled) return;
    input.value = '';
    input.style.height = 'auto';
    sendBtn.disabled = true;
    addMsg(text, 'user');
    const typing = addMsg('Thinking…', 'apex');
    typing.style.opacity = '0.5';
    // Detect plan creation request
    const wantsPlan = /weekly plan|create.*plan|build.*plan|plan.*week/i.test(text);
    if (wantsPlan) {
      typing.textContent = 'Building your 7-day plan… this takes ~30 seconds.';
      try {
        const resp = await fetch('/api/apex-plan/generate', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
          typing.textContent = 'Done! Your 7-day plan is saved. Tap 📅 My Plan to load today\'s workout anytime.';
          typing.style.opacity = '1';
          history.push({ role: 'user', content: text });
          history.push({ role: 'assistant', content: typing.textContent });
        } else {
          typing.textContent = data.error || 'Could not generate plan.';
          typing.style.opacity = '1';
        }
        sendBtn.disabled = false;
        input.focus();
        return;
      } catch {
        typing.textContent = 'Error generating plan.';
        typing.style.opacity = '1';
        sendBtn.disabled = false;
        return;
      }
    }
    history.push({ role: 'user', content: text });
    try {
      const resp = await fetch('/api/coach', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          history: history.slice(0, -1),
          local_date: (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; })(),
          local_day: ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][new Date().getDay()],
        }),
      });
      const data = await resp.json();
      const reply = data.response || data.error || 'Something went wrong.';
      typing.innerHTML = renderMarkdown(reply);
      typing.style.opacity = '1';
      history.push({ role: 'assistant', content: reply });
      if (data.plan_saved) addPlanBanner();
    } catch {
      typing.textContent = 'Connection error.';
      typing.style.opacity = '1';
      history.pop();
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn && sendBtn.addEventListener('click', send);
  input && input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
})();

/* ---------- Apex form cues ---------- */
document.querySelectorAll('.apex-cue-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const body = btn.closest('.ex-body');
    const wrap = body.querySelector('.ex-cue-wrap');
    const cueEl = body.querySelector('.ex-cue-body');
    if (wrap.style.display !== 'none') {
      wrap.style.display = 'none';
      btn.textContent = '🏔️ Form tips';
      return;
    }
    if (cueEl.dataset.loaded) {
      wrap.style.display = 'block';
      btn.textContent = '🏔️ Hide tips';
      return;
    }
    btn.disabled = true;
    btn.textContent = '🏔️ Thinking…';
    const name = body.dataset.exName;
    const id = body.dataset.exId;
    try {
      const resp = await fetch(`/api/exercise-cue?name=${encodeURIComponent(name)}&id=${encodeURIComponent(id)}`);
      const data = await resp.json();
      if (data.cue) {
        cueEl.textContent = data.cue;
        cueEl.dataset.loaded = '1';
        wrap.style.display = 'block';
        btn.textContent = '🏔️ Hide tips';
      } else {
        btn.textContent = '🏔️ Apex offline';
      }
    } catch {
      btn.textContent = '🏔️ Error';
    }
    btn.disabled = false;
  });
});

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
      if (data.ok && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.healthKit) {
        window.webkit.messageHandlers.healthKit.postMessage({ type: 'logWeight', pounds: weight });
      }
      status.textContent = data.ok ? 'Logged ✓' : 'Could not save.';
    } catch (err) {
      status.textContent = 'Could not reach server.';
    }
  });
}

/* ---------- APEX dashboard icon — plan or generate ---------- */
const apexWorkoutBtn = document.getElementById('apex-workout-btn');
if (apexWorkoutBtn) {
  async function handleApexIcon(e) {
    e.preventDefault();
    apexWorkoutBtn.style.opacity = '0.5';
    apexWorkoutBtn.disabled = true;
    try {
      // Try loading today's plan first
      const planResp = await fetch('/api/apex-plan/today', { method: 'POST' });
      if (planResp.ok) {
        const data = await planResp.json();
        if (data.rest) {
          // Rest day — fall through to generate a light workout instead
        } else if (data.ok) {
          window.location.href = '/today-workout';
          return;
        }
      }
      // No plan or rest day — build an AI-generated workout
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = '/start-workout';
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'focus_mode';
      input.value = 'ai';
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
    } catch {
      apexWorkoutBtn.style.opacity = '1';
      apexWorkoutBtn.disabled = false;
    }
  }
  apexWorkoutBtn.addEventListener('click', handleApexIcon);
  apexWorkoutBtn.addEventListener('touchend', (e) => { e.preventDefault(); handleApexIcon(e); });
}

/* ---------- Regenerate workout ---------- */
const regenBtn = document.getElementById('regen-btn');
if (regenBtn) {
  regenBtn.addEventListener('click', async () => {
    if (!confirm('Ask the coach to build a different workout?')) return;
    regenBtn.disabled = true;
    regenBtn.textContent = '🤖 Thinking…';
    try {
      const resp = await fetch('/api/regenerate-workout', { method: 'POST' });
      if (resp.ok) {
        window.location.reload();
      } else {
        regenBtn.textContent = '⚠ Coach offline';
      }
    } catch {
      regenBtn.textContent = '⚠ Error';
    }
  });
}

/* ---------- Active workout ---------- */
function startWorkout() {
  const root = document.getElementById('workout-body');
  if (!root) return;

  const timerEl = document.getElementById('workout-timer');
  const userId = root.dataset.userId || 'default';
  const STORE_KEY = `homefit_wk_${userId}`;
  const dayName = root.dataset.dayName;

  // Restore state — localStorage survives app restarts
  let state = null;
  try { state = JSON.parse(localStorage.getItem(STORE_KEY)); } catch (_) {}
  if (!state || state.dayName !== dayName) {
    state = { dayName, started: Date.now(), offset: 0, done: [] };
  }
  localStorage.setItem(STORE_KEY, JSON.stringify(state));

  // Restore completed checkboxes
  root.querySelectorAll('.exercise-item').forEach((li) => {
    if (state.done.includes(li.dataset.exerciseId)) {
      li.querySelector('.ex-done').checked = true;
    }
  });

  // Persist checkbox changes
  const persistDone = () => {
    state.done = Array.from(root.querySelectorAll('.exercise-item'))
      .filter((li) => li.querySelector('.ex-done').checked)
      .map((li) => li.dataset.exerciseId);
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  };
  root.querySelectorAll('.ex-done').forEach((cb) => cb.addEventListener('change', persistDone));

  // Elapsed seconds = time since started + any manually added offset
  const elapsed = () => Math.floor((Date.now() - state.started) / 1000) + (state.offset || 0);

  const tickTimer = () => {
    const s = elapsed();
    const m = String(Math.floor(s / 60)).padStart(2, '0');
    const r = String(s % 60).padStart(2, '0');
    timerEl.textContent = `${m}:${r}`;
  };
  tickTimer();
  const timerInterval = setInterval(tickTimer, 1000);

  // Tap timer to manually correct the time
  timerEl.addEventListener('click', () => {
    clearInterval(timerInterval);
    const currentMins = Math.floor(elapsed() / 60);
    const input = prompt('Correct workout time (minutes):', currentMins);
    if (input !== null) {
      const mins = parseInt(input, 10);
      if (!isNaN(mins) && mins >= 0) {
        state.started = Date.now();
        state.offset = mins * 60;
        localStorage.setItem(STORE_KEY, JSON.stringify(state));
      }
    }
    tickTimer();
    setInterval(tickTimer, 1000);
  });

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
          if ('vibrate' in navigator) navigator.vibrate(200);
        }
      }, 1000);
    });
  });

  // Weight logging
  function makeSetRow(reps) {
    const row = document.createElement('div');
    row.className = 'weight-set-row';
    row.innerHTML = `
      <input type="number" class="set-weight" placeholder="lbs" min="0" step="0.5" style="width:72px;">
      <span style="margin:0 6px;">×</span>
      <input type="number" class="set-reps" value="${reps}" min="1" style="width:52px;">
      <span style="margin-left:4px; color:#94a3b8; font-size:13px;">reps</span>
      <button type="button" class="remove-set-btn" style="margin-left:8px; background:none; border:none; color:#f87171; cursor:pointer; font-size:16px;">×</button>
    `;
    row.querySelector('.remove-set-btn').addEventListener('click', () => row.remove());
    return row;
  }

  root.querySelectorAll('.weight-log').forEach((wl) => {
    const toggleBtn = wl.querySelector('.weight-log-toggle');
    const body = wl.querySelector('.weight-log-body');
    const setsContainer = wl.querySelector('.weight-sets');
    const addSetBtn = wl.querySelector('.add-set-btn');
    const defaultSets = parseInt(wl.dataset.sets, 10) || 3;
    const defaultReps = parseInt(wl.dataset.reps, 10) || 10;

    toggleBtn.addEventListener('click', () => {
      const open = body.style.display === 'none';
      body.style.display = open ? 'block' : 'none';
      toggleBtn.textContent = open ? '📊 Hide weight log' : '📊 Log weights (optional)';
      if (open && setsContainer.children.length === 0) {
        const suggestedWeight = parseFloat(wl.dataset.suggestedWeight) || null;
        for (let i = 0; i < defaultSets; i++) {
          const row = makeSetRow(defaultReps);
          if (suggestedWeight) row.querySelector('.set-weight').value = suggestedWeight;
          setsContainer.appendChild(row);
        }
      }
    });

    addSetBtn.addEventListener('click', () => setsContainer.appendChild(makeSetRow(defaultReps)));
  });

  function getLoggedSets(li) {
    return Array.from(li.querySelectorAll('.weight-set-row')).map((row) => ({
      weight: parseFloat(row.querySelector('.set-weight').value) || null,
      reps: parseInt(row.querySelector('.set-reps').value, 10) || null,
    })).filter((s) => s.weight !== null || s.reps !== null);
  }

  // Finish workout
  const finishBtn = document.getElementById('finish-btn');
  finishBtn.addEventListener('click', async () => {
    clearInterval(timerInterval);
    localStorage.removeItem(STORE_KEY);
    const duration = elapsed();
    const items = Array.from(root.querySelectorAll('.exercise-item')).map((li) => ({
      id: li.dataset.exerciseId,
      completed: li.querySelector('.ex-done').checked,
      sets: getLoggedSets(li),
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
      const resp = await fetch('/api/complete_workout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error('server error');
      const data = await resp.json();
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.healthKit) {
        window.webkit.messageHandlers.healthKit.postMessage({
          type: 'logWorkout',
          durationSeconds: duration,
          kcal: data.kcal || 0,
          startTime: new Date(Date.now() - duration * 1000).toISOString(),
          endTime: new Date().toISOString(),
        });
      }

      showPostWorkoutInsight(data.insight, data.overload || [], duration, data.kcal);
    } catch (err) {
      finishBtn.disabled = false;
      finishBtn.textContent = 'Retry finish';
    }
  });

  function showPostWorkoutInsight(insight, overload, durationSecs, kcal) {
    // Populate the static workout-complete card
    const wcSection = document.getElementById('workout-complete');
    const workoutSection = document.getElementById('workout-body');
    if (wcSection) {
      const mins = Math.floor(durationSecs / 60);
      const secs = durationSecs % 60;
      const timeEl = document.getElementById('wc-time');
      const kcalEl = document.getElementById('wc-kcal');
      if (timeEl) timeEl.textContent = `${String(mins).padStart(2,'0')}:${String(secs).padStart(2,'0')}`;
      if (kcalEl) kcalEl.textContent = kcal ? Math.round(kcal) : '--';
      if (workoutSection) workoutSection.style.display = 'none';
      wcSection.classList.remove('hidden');
    }

    // If Apex returned extra data, show it above the card
    if (!insight && (!overload || !overload.length)) return;
    const banner = document.createElement('div');
    banner.style.cssText = 'padding:16px;';
    let overloadHtml = '';
    if (overload && overload.length) {
      const items = overload.map(s =>
        `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:14px;">
          <strong>${s.exercise_name}</strong>
          <span style="color:var(--accent);float:right">${s.current_weight} → ${s.suggested_weight} lbs</span>
        </div>`
      ).join('');
      overloadHtml = `<div style="margin:10px 0 4px;font-weight:600;font-size:14px;">📈 Ready to progress:</div>${items}`;
    }
    banner.innerHTML = `<div style="background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px;max-width:480px;margin:0 auto;">
      ${insight ? `<p style="margin:0 0 10px;font-size:14px;line-height:1.6;color:var(--subtle);">${insight}</p>` : ''}
      ${overloadHtml}
    </div>`;
    if (wcSection) wcSection.insertAdjacentElement('afterbegin', banner);
  }
}

/* ---------- Exercise library filtering ---------- */
function setupLibraryFilter() {
  const search = document.getElementById('ex-search');
  const filter = document.getElementById('ex-filter');
  if (!search || !filter) return;
  const apply = () => {
    const q = search.value.trim().toLowerCase();
    const cat = filter.value;
    document.querySelectorAll('.library-list .exercise-item').forEach((li) => {
      const matchCat = cat === 'all' || li.dataset.category === cat;
      const name = (li.dataset.name || '').toLowerCase();
      const matchQ = !q || name.includes(q);
      li.style.display = matchCat && matchQ ? '' : 'none';
    });
  };
  ['input', 'keyup', 'change', 'search'].forEach(ev => search.addEventListener(ev, apply));
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
  ctx.strokeStyle = '#2d3a52';
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
  ctx.strokeStyle = '#f97316';
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
  ctx.fillStyle = '#fb923c';
  points.forEach((p, i) => {
    const x = padding.l + (innerW * i) / Math.max(1, points.length - 1);
    const y = padding.t + innerH - ((p.weight - min) / range) * innerH;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });
}
