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

  // ?apex=1 opens the panel on load (used by the /apex and /coach redirects)
  if (new URLSearchParams(window.location.search).get('apex') === '1') {
    togglePanel();
    window.history.replaceState({}, '', window.location.pathname);
  }

  let clearArmed = false;
  let clearTimer = null;
  clearBtn && clearBtn.addEventListener('click', async () => {
    // No window.confirm(): it silently returns false inside the native
    // WKWebView (no JS-dialog delegate), making Clear appear broken.
    // Use a two-tap inline confirm instead.
    if (!clearArmed) {
      clearArmed = true;
      const orig = clearBtn.textContent;
      clearBtn.textContent = 'Tap to confirm';
      clearTimer = setTimeout(() => { clearArmed = false; clearBtn.textContent = orig; }, 3000);
      return;
    }
    clearTimeout(clearTimer);
    clearArmed = false;
    clearBtn.textContent = 'Clearing…';
    try {
      const resp = await fetch('/api/apex-chat/clear', { method: 'POST' });
      if (!resp.ok) throw new Error('status ' + resp.status);
      history = [];
      messages.innerHTML = '<div class="msg msg-apex">Chat cleared. Ask me anything — or say "create my weekly plan".</div>';
    } catch (e) {
      messages.innerHTML += '<div class="msg msg-apex">Couldn\'t clear chat (' + e.message + '). Try again.</div>';
    } finally {
      clearBtn.textContent = 'Clear';
    }
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
    planBtn.textContent = 'My Plan';
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
    div.innerHTML = '<a href="/apex-plan" style="display:block;text-align:center;padding:10px;background:var(--accent);color:#fff;border-radius:12px;font-weight:600;text-decoration:none;">View My Plan →</a>';
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
function wireApexCue(btn) {
  btn.addEventListener('click', async () => {
    const body = btn.closest('.ex-body');
    const wrap = body.querySelector('.ex-cue-wrap');
    const cueEl = body.querySelector('.ex-cue-body');
    if (wrap.style.display !== 'none') {
      wrap.style.display = 'none';
      btn.textContent = 'Form tips';
      return;
    }
    if (cueEl.dataset.loaded) {
      wrap.style.display = 'block';
      btn.textContent = 'Hide tips';
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Thinking…';
    const name = body.dataset.exName;
    const id = body.dataset.exId;
    try {
      const resp = await fetch(`/api/exercise-cue?name=${encodeURIComponent(name)}&id=${encodeURIComponent(id)}`);
      const data = await resp.json();
      if (data.cue) {
        cueEl.textContent = data.cue;
        cueEl.dataset.loaded = '1';
        wrap.style.display = 'block';
        btn.textContent = 'Hide tips';
      } else {
        btn.textContent = 'Apex offline';
      }
    } catch {
      btn.textContent = 'Error';
    }
    btn.disabled = false;
  });
}
document.querySelectorAll('.apex-cue-btn').forEach(wireApexCue);

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
      if (!data.ok) {
        status.textContent = 'Could not save.';
      } else if (data.sparky_synced === false) {
        status.textContent = 'Logged ✓ (Sparky sync failed)';
      } else {
        status.textContent = 'Logged ✓';
      }
    } catch (err) {
      status.textContent = 'Could not reach server.';
    }
  });
}

/* ---------- Weekly APEX digest ---------- */
function loadWeeklyDigest() {
  const card = document.getElementById('weekly-digest-card');
  const body = document.getElementById('weekly-digest-body');
  if (!card || !body) return;
  fetch('/api/weekly-digest')
    .then(r => r.json())
    .then(data => {
      if (!data.digest) return;
      const lines = data.digest.split('\n').filter(l => l.trim());
      body.innerHTML = lines.map(l => `<p style="margin:0 0 6px;">${l.replace(/^-\s*/, '').trim()}</p>`).join('');
      card.style.display = '';
    })
    .catch(() => {});
}

function loadDailyBrief() {
  const card = document.getElementById('daily-brief-card');
  const body = document.getElementById('daily-brief-body');
  if (!card || !body) return;
  fetch('/api/daily-brief')
    .then(r => r.json())
    .then(data => {
      if (!data.brief) return;
      body.textContent = data.brief;
      card.style.display = '';
    })
    .catch(() => {});
}

/* ---------- Push notifications opt-in (profile page) ---------- */
function setupPushToggle() {
  const btn = document.getElementById('push-toggle');
  const status = document.getElementById('push-status');
  if (!btn) return;

  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  if (!supported) {
    btn.disabled = true;
    btn.textContent = 'Not supported on this device';
    if (status) status.textContent = 'On iPhone, add HomeFit to your Home Screen first (Share → Add to Home Screen), then enable here.';
    return;
  }

  function urlB64ToUint8Array(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  async function refresh() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    btn.textContent = sub ? 'Disable notifications' : 'Enable notifications';
    btn.dataset.enabled = sub ? '1' : '';
    return sub;
  }

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const reg = await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      if (existing) {
        await fetch('/api/push/unsubscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: existing.endpoint }),
        });
        await existing.unsubscribe();
        if (status) status.textContent = 'Notifications disabled.';
      } else {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
          if (status) status.textContent = 'Permission denied — enable notifications for this site in your browser settings.';
          btn.disabled = false;
          return;
        }
        const keyResp = await fetch('/api/push/public-key');
        const { publicKey } = await keyResp.json();
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlB64ToUint8Array(publicKey),
        });
        await fetch('/api/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub.toJSON()),
        });
        if (status) status.textContent = 'Notifications enabled on this device.';
      }
    } catch (e) {
      if (status) status.textContent = 'Could not update notifications: ' + e.message;
    }
    btn.disabled = false;
    refresh();
  });

  refresh().catch(() => {
    btn.textContent = 'Enable notifications';
  });
}

function loadStrengthChart() {
  const card = document.getElementById('strength-card');
  const select = document.getElementById('strength-select');
  const canvas = document.getElementById('strength-chart');
  if (!card || !select || !canvas) return;
  fetch('/api/strength-history')
    .then(r => r.json())
    .then(data => {
      const exercises = data.exercises || [];
      if (!exercises.length) return;
      select.innerHTML = exercises.map((e, i) =>
        `<option value="${i}">${e.name} (${e.points.length} sessions)</option>`).join('');
      const draw = () => drawStrengthSeries(canvas, exercises[parseInt(select.value, 10) || 0].points);
      select.addEventListener('change', draw);
      card._draw = draw;
      card.style.display = '';
      // Draw now only if the card is already open; otherwise the toggle handler draws it.
      if (card.open || !card.classList.contains('collapsible')) draw();
    })
    .catch(() => {});
}

function drawStrengthSeries(canvas, points) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 600;
  const cssH = 200;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.height = cssH + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const padding = { t: 16, r: 16, b: 26, l: 40 };
  const innerW = cssW - padding.l - padding.r;
  const innerH = cssH - padding.t - padding.b;
  const weights = points.map(p => p.weight);
  const min = Math.min(...weights) - 2.5;
  const max = Math.max(...weights) + 2.5;
  const range = Math.max(1, max - min);

  ctx.clearRect(0, 0, cssW, cssH);
  ctx.fillStyle = '#94a3b8';
  ctx.font = '11px -apple-system, system-ui, sans-serif';
  for (let i = 0; i <= 4; i++) {
    const v = min + (range * i) / 4;
    const y = padding.t + innerH - (innerH * i) / 4;
    ctx.fillText(v.toFixed(0), 4, y + 4);
    ctx.strokeStyle = 'rgba(38, 56, 89, 0.4)';
    ctx.beginPath();
    ctx.moveTo(padding.l, y);
    ctx.lineTo(padding.l + innerW, y);
    ctx.stroke();
  }
  const xy = (p, i) => [
    padding.l + (innerW * i) / Math.max(1, points.length - 1),
    padding.t + innerH - ((p.weight - min) / range) * innerH,
  ];
  ctx.strokeStyle = '#f97316';
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const [x, y] = xy(p, i);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = '#fb923c';
  points.forEach((p, i) => {
    const [x, y] = xy(p, i);
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });
  // first/last date labels
  ctx.fillStyle = '#94a3b8';
  ctx.fillText(points[0].date.slice(5), padding.l, cssH - 8);
  const lastLabel = points[points.length - 1].date.slice(5);
  ctx.fillText(lastLabel, padding.l + innerW - ctx.measureText(lastLabel).width, cssH - 8);
}

function loadEnergyBalance() {
  const card = document.getElementById('energy-card');
  const rows = document.getElementById('energy-rows');
  if (!card || !rows) return;
  fetch('/api/energy-balance')
    .then(r => r.json())
    .then(data => {
      const days = data.days || [];
      if (!days.some(d => d.eaten || d.burned)) return;
      const maxVal = Math.max(...days.map(d => Math.max(d.eaten, d.burned)), 1);
      rows.innerHTML = days.map(d => {
        const inW = Math.round((d.eaten / maxVal) * 100);
        const outW = Math.round((d.burned / maxVal) * 100);
        return `
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px; font-size:12px;">
            <span style="width:32px; color:var(--subtle); flex-shrink:0;">${d.day}</span>
            <div style="flex:1;">
              <div style="height:8px; border-radius:4px; width:${inW}%; min-width:2px; background:var(--accent);"></div>
              <div style="height:8px; border-radius:4px; width:${outW}%; min-width:2px; background:var(--success); margin-top:3px;"></div>
            </div>
            <span style="width:112px; text-align:right; color:var(--subtle); flex-shrink:0;">${d.eaten} in · ${d.burned} out</span>
          </div>`;
      }).join('') +
      '<p class="subtle" style="font-size:12px; margin:10px 0 0;">' +
      '<span style="color:var(--accent);">&#9632;</span> eaten &nbsp; ' +
      '<span style="color:var(--success);">&#9632;</span> burned</p>';
      card.style.display = '';
    })
    .catch(() => {});
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
      const localWeekday = (new Date().getDay() + 6) % 7;
      const planResp = await fetch('/api/apex-plan/today', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({weekday: localWeekday}) });
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
        regenBtn.textContent = 'Coach offline';
      }
    } catch {
      regenBtn.textContent = 'Error';
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

  // Persist checkbox changes
  const persistDone = () => {
    state.done = Array.from(root.querySelectorAll('.exercise-item'))
      .filter((li) => li.querySelector('.ex-done').checked)
      .map((li) => li.dataset.exerciseId);
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  };

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

  function wireRest(btn) {
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
  }

  // Weight logging
  function makeSetRow(reps, weight) {
    const row = document.createElement('div');
    row.className = 'weight-set-row';
    row.innerHTML = `
      <input type="number" class="set-weight" placeholder="lbs" min="0" step="0.5" style="width:72px;">
      <span style="margin:0 6px;">×</span>
      <input type="number" class="set-reps" value="${reps}" min="1" style="width:52px;">
      <span style="margin-left:4px; color:#94a3b8; font-size:13px;">reps</span>
      <button type="button" class="remove-set-btn" style="margin-left:8px; background:none; border:none; color:#f87171; cursor:pointer; font-size:16px;">×</button>
    `;
    if (weight != null && weight !== '') row.querySelector('.set-weight').value = weight;
    row.querySelector('.remove-set-btn').addEventListener('click', () => { row.remove(); persistSets(); });
    return row;
  }

  // Persist entered weights/reps so they survive reloads (iOS backgrounds the
  // PWA aggressively — without this, weights typed mid-workout are lost before
  // Finish reads them).
  function persistSets() {
    const all = {};
    root.querySelectorAll('.exercise-item').forEach((li) => {
      const rows = Array.from(li.querySelectorAll('.weight-set-row')).map((r) => ({
        weight: r.querySelector('.set-weight').value || null,
        reps: r.querySelector('.set-reps').value || null,
      }));
      if (rows.length) all[li.dataset.exerciseId] = rows;
    });
    state.sets = all;
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  }

  function wireWeightLog(wl) {
    const toggleBtn = wl.querySelector('.weight-log-toggle');
    const body = wl.querySelector('.weight-log-body');
    const setsContainer = wl.querySelector('.weight-sets');
    const addSetBtn = wl.querySelector('.add-set-btn');
    const defaultSets = parseInt(wl.dataset.sets, 10) || 3;
    const defaultReps = parseInt(wl.dataset.reps, 10) || 10;
    const exId = wl.closest('.exercise-item').dataset.exerciseId;

    const openLog = () => {
      body.style.display = 'block';
      toggleBtn.textContent = 'Hide weight log';
    };

    toggleBtn.addEventListener('click', () => {
      const open = body.style.display === 'none';
      body.style.display = open ? 'block' : 'none';
      toggleBtn.textContent = open ? 'Hide weight log' : 'Log weights';
      if (open && setsContainer.children.length === 0) {
        const suggestedWeight = parseFloat(wl.dataset.suggestedWeight) || null;
        for (let i = 0; i < defaultSets; i++) {
          setsContainer.appendChild(makeSetRow(defaultReps, suggestedWeight));
        }
        persistSets();
      }
    });

    addSetBtn.addEventListener('click', () => { setsContainer.appendChild(makeSetRow(defaultReps)); persistSets(); });
    setsContainer.addEventListener('input', persistSets);

    // Restore previously entered sets; otherwise auto-open the log for weighted
    // exercises (and when APEX suggests a bump) so the inputs are there by
    // default. Bodyweight / timed moves stay collapsed behind the button.
    const saved = (state.sets || {})[exId];
    if (saved && saved.length) {
      saved.forEach((s) => setsContainer.appendChild(makeSetRow(s.reps || defaultReps, s.weight)));
      openLog();
    } else if (wl.dataset.hintReady === 'true' || wl.dataset.weighted === 'true') {
      toggleBtn.click();
    }
  }

  // Wire one exercise <li> — used for both server-rendered exercises and ones
  // added in-page via the picker. Idempotent.
  function wireExercise(li) {
    if (li.dataset.wired === '1') return;
    li.dataset.wired = '1';
    const cb = li.querySelector('.ex-done');
    if (cb) {
      if (state.done.includes(li.dataset.exerciseId)) cb.checked = true;
      cb.addEventListener('change', persistDone);
    }
    li.querySelectorAll('.rest-btn').forEach(wireRest);
    li.querySelectorAll('.weight-log').forEach(wireWeightLog);
  }
  root.querySelectorAll('.exercise-item').forEach(wireExercise);

  // In-page "Add exercise" (no page navigation — works on flaky connections)
  setupAddExercise(root, state, STORE_KEY, wireExercise);

  function getLoggedSets(li) {
    return Array.from(li.querySelectorAll('.weight-set-row')).map((row) => ({
      weight: parseFloat(row.querySelector('.set-weight').value) || null,
      reps: parseInt(row.querySelector('.set-reps').value, 10) || null,
    })).filter((s) => s.weight !== null || s.reps !== null);
  }

  // Finish workout
  const finishBtn = document.getElementById('finish-btn');
  finishBtn.addEventListener('click', () => {
    // Guard against the common slip: doing the last exercise, hitting finish,
    // but forgetting to tick its checkbox. Surface the unchecked ones first.
    const unchecked = Array.from(root.querySelectorAll('.exercise-item'))
      .filter((li) => !li.querySelector('.ex-done').checked);
    if (unchecked.length) {
      const list = document.getElementById('skip-list');
      list.innerHTML = '';
      unchecked.forEach((li) => {
        const name = (li.querySelector('.ex-title strong') || {}).textContent || 'Exercise';
        const item = document.createElement('li');
        item.textContent = name.trim();
        list.appendChild(item);
      });
      document.getElementById('skip-confirm').classList.remove('hidden');
      return;
    }
    doFinish();
  });

  const skipConfirm = document.getElementById('skip-confirm');
  document.getElementById('skip-back').addEventListener('click', () => {
    skipConfirm.classList.add('hidden');
  });
  document.getElementById('skip-finish').addEventListener('click', () => {
    skipConfirm.classList.add('hidden');
    doFinish();
  });

  async function doFinish() {
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
      showCompletionScreen(duration, data.kcal, data.exercises_completed, payload.day_name, payload.exercises);
    } catch (err) {
      finishBtn.disabled = false;
      finishBtn.textContent = 'Retry finish';
    }
  }

  function showCompletionScreen(durationSecs, kcal, exerciseCount, dayName, exercises) {
    const wcSection = document.getElementById('workout-complete');
    const workoutSection = document.getElementById('workout-body');
    if (!wcSection) return;

    // Populate stats immediately
    const mins = Math.floor(durationSecs / 60);
    const secs = durationSecs % 60;
    const timeEl = document.getElementById('wc-time');
    const kcalEl = document.getElementById('wc-kcal');
    const exEl = document.getElementById('wc-exercises');
    const nameEl = document.getElementById('wc-day-name');
    if (timeEl) timeEl.textContent = `${String(mins).padStart(2,'0')}:${String(secs).padStart(2,'0')}`;
    if (kcalEl) kcalEl.textContent = kcal ? Math.round(kcal) : '--';
    if (exEl) exEl.textContent = exerciseCount ?? '--';
    if (nameEl) nameEl.textContent = dayName || 'Workout';
    if (workoutSection) workoutSection.style.display = 'none';
    wcSection.classList.remove('hidden');

    // Fetch APEX insight asynchronously
    fetch('/api/post-workout-insight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exercises }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        const body = document.getElementById('apex-summary-body');
        if (body) {
          body.innerHTML = data.insight
            ? `<p style="font-size:14px;line-height:1.65;margin:0;color:var(--text);">${data.insight}</p>`
            : `<p style="color:var(--subtle);font-size:14px;margin:0;">No analysis available.</p>`;
        }
        if (data.overload && data.overload.length) {
          const card = document.getElementById('overload-card');
          const list = document.getElementById('overload-list');
          if (card && list) {
            list.innerHTML = data.overload.map(s =>
              `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:14px;">
                <span>${s.exercise_name}</span>
                <span style="color:var(--accent);font-weight:600;">${s.current_weight} → ${s.suggested_weight} lbs</span>
              </div>`
            ).join('');
            card.classList.remove('hidden');
          }
        }
      })
      .catch(() => {
        const body = document.getElementById('apex-summary-body');
        if (body) body.innerHTML = `<p style="color:var(--subtle);font-size:14px;margin:0;">Analysis unavailable.</p>`;
      });
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
  const goal = parseFloat(canvas.dataset.goal) || null;
  // Include the goal in the scale so the goal line is always visible
  const lo = goal ? Math.min(...weights, goal) : Math.min(...weights);
  const hi = goal ? Math.max(...weights, goal) : Math.max(...weights);
  const min = lo - 1;
  const max = hi + 1;
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

  // Goal line (dashed green)
  if (goal) {
    const gy = padding.t + innerH - ((goal - min) / range) * innerH;
    ctx.strokeStyle = '#34d399';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.l, gy);
    ctx.lineTo(padding.l + innerW, gy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#34d399';
    ctx.fillText('goal', padding.l + innerW - 26, gy - 5);
  }

  // 7-day moving average (smooth trend behind the raw line)
  if (points.length >= 3) {
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.7)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
      const start = Math.max(0, i - 6);
      const slice = points.slice(start, i + 1);
      const avg = slice.reduce((s, q) => s + q.weight, 0) / slice.length;
      const x = padding.l + (innerW * i) / Math.max(1, points.length - 1);
      const y = padding.t + innerH - ((avg - min) / range) * innerH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
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

/* ---------- In-page Add Exercise picker (used on the workout screen) ---------- */
function setupAddExercise(root, state, STORE_KEY, wireExercise) {
  const libEl = document.getElementById('exercise-library');
  const modal = document.getElementById('add-ex-modal');
  const openBtn = document.getElementById('add-ex-btn');
  if (!libEl || !modal || !openBtn) return;

  let library = [];
  try { library = JSON.parse(libEl.textContent) || []; } catch (_) {}
  const byId = {};
  library.forEach((e) => { byId[e.id] = e; });
  const list = root.querySelector('.exercise-list');
  state.added = state.added || [];

  const isWeighted = (e) => {
    const eq = (e.equipment || [])[0] || '';
    const bw = ['bodyweight', 'none', '', 'pull_up_bar', 'bench_or_chair', 'resistance_bands'];
    return !(e.unit === 'seconds' || !eq || bw.includes(eq));
  };

  function buildNode(e) {
    const li = document.createElement('li');
    li.className = 'exercise-item';
    li.dataset.exerciseId = e.id;
    const repLabel = e.unit === 'seconds' ? `${e.default_reps}s` : `${e.default_reps} reps`;
    const weighted = isWeighted(e) ? 'true' : 'false';
    const yt = encodeURIComponent(e.name + ' proper form');
    const anim = (e.anim && e.anim.length >= 2)
      ? `<div class="ex-demo-wrap ex-anim"><img class="ex-demo-img" src="${e.anim[0]}" loading="lazy"><img class="ex-demo-img ex-anim-frame2" src="${e.anim[1]}" loading="lazy"></div>`
      : '';
    li.innerHTML = `
      <details>
        <summary>
          <div class="ex-head">
            <span class="ex-num"></span>
            <div class="ex-title">
              <strong></strong>
              <span class="ex-meta">${e.default_sets} sets × ${repLabel} · ${e.rest_seconds}s rest</span>
            </div>
            <label class="ex-done-wrap"><input type="checkbox" class="ex-done" aria-label="Mark complete"></label>
          </div>
        </summary>
        <div class="ex-body" data-ex-id="${e.id}">
          ${anim}
          <p class="ex-instr"></p>
          <div class="ex-cue-wrap" style="display:none;"><div class="ex-cue-body subtle" style="font-size:13px; line-height:1.6; white-space:pre-wrap;"></div></div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <a class="btn btn-secondary demo-link" target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${yt}" style="flex:1; text-align:center;">YouTube</a>
            <button type="button" class="btn btn-secondary apex-cue-btn" style="flex:1;">Form tips</button>
          </div>
          <button type="button" class="btn btn-secondary rest-btn" data-rest="${e.rest_seconds}">Start ${e.rest_seconds}s rest timer</button>
          <div class="weight-log" data-sets="${e.default_sets}" data-reps="${e.default_reps}" data-weighted="${weighted}" data-hint-ready="false" data-suggested-weight="">
            <button type="button" class="btn btn-secondary weight-log-toggle">Log weights</button>
            <div class="weight-log-body" style="display:none; margin-top:10px;">
              <p class="hint" style="margin-bottom:8px;">Recommended: ${e.default_sets} sets × ${repLabel}</p>
              <div class="weight-sets"></div>
              <button type="button" class="btn btn-secondary add-set-btn" style="margin-top:6px; font-size:13px;">+ Add set</button>
            </div>
          </div>
        </div>
      </details>`;
    // Set text via textContent to avoid any HTML injection from names/instructions
    li.querySelector('.ex-title strong').textContent = e.name;
    li.querySelector('.ex-body').dataset.exName = e.name;
    li.querySelector('.ex-instr').textContent = e.instructions || '';
    return li;
  }

  function renumber() {
    list.querySelectorAll('.exercise-item .ex-num').forEach((el, i) => { el.textContent = i + 1; });
  }

  function addExercise(e, persist) {
    if (!e || root.querySelector(`.exercise-item[data-exercise-id="${e.id}"]`)) return;
    const li = buildNode(e);
    list.appendChild(li);
    wireExercise(li);
    li.querySelectorAll('.apex-cue-btn').forEach(wireApexCue);
    renumber();
    if (persist) {
      if (!state.added.includes(e.id)) state.added.push(e.id);
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    }
  }

  // Re-add exercises that were added before a reload
  state.added.forEach((id) => addExercise(byId[id], false));
  renumber();

  // Modal wiring
  const closeBtn = document.getElementById('add-ex-close');
  const search = document.getElementById('add-ex-search');
  const filter = document.getElementById('add-ex-filter');
  const listEl = document.getElementById('add-ex-list');

  function renderList() {
    const q = (search.value || '').trim().toLowerCase();
    const cat = filter.value;
    const inWorkout = new Set(Array.from(root.querySelectorAll('.exercise-item')).map((li) => li.dataset.exerciseId));
    listEl.innerHTML = '';
    library.forEach((e) => {
      if (!e.available) return;
      if (cat !== 'all' && e.category !== cat) return;
      if (q && !e.name.toLowerCase().includes(q)) return;
      const here = inWorkout.has(e.id);
      const li = document.createElement('li');
      li.className = 'add-ex-item' + (here ? ' is-added' : '');
      const eq = (e.equipment || []).filter((x) => x !== 'bodyweight' && x !== 'none');
      const meta = (e.category || '').toUpperCase() + (eq.length ? ' · ' + eq.join(', ') : '');
      const nameDiv = document.createElement('div');
      const nameEl = document.createElement('div'); nameEl.className = 'ax-name'; nameEl.textContent = e.name;
      const metaEl = document.createElement('div'); metaEl.className = 'ax-meta'; metaEl.textContent = meta;
      nameDiv.appendChild(nameEl); nameDiv.appendChild(metaEl);
      const plus = document.createElement('span'); plus.className = 'ax-plus'; plus.textContent = here ? '✓' : '+';
      li.appendChild(nameDiv); li.appendChild(plus);
      if (!here) li.addEventListener('click', () => { addExercise(e, true); renderList(); });
      listEl.appendChild(li);
    });
  }

  openBtn.addEventListener('click', () => { modal.classList.remove('hidden'); renderList(); setTimeout(() => search.focus(), 50); });
  closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
  modal.addEventListener('click', (ev) => { if (ev.target === modal) modal.classList.add('hidden'); });
  ['input', 'keyup', 'change', 'search'].forEach((ev) => search.addEventListener(ev, renderList));
  filter.addEventListener('change', renderList);

  // --- Identify a move from video -------------------------------------------
  const idBtn = document.getElementById('identify-btn');
  const videoInput = document.getElementById('add-ex-video');
  const idResult = document.getElementById('identify-result');
  if (idBtn && videoInput && idResult) {
    const reset = () => { idResult.classList.add('hidden'); idResult.innerHTML = ''; };
    idBtn.addEventListener('click', () => videoInput.click());
    videoInput.addEventListener('change', async () => {
      const file = videoInput.files[0];
      if (!file) return;
      idResult.classList.remove('hidden');
      idResult.innerHTML = '<p class="subtle" style="padding:8px 0;">Analyzing the clip… this takes a few seconds.</p>';
      const fd = new FormData(); fd.append('video', file);
      let data;
      try {
        const r = await fetch('/api/identify-exercise', { method: 'POST', body: fd });
        data = await r.json();
      } catch (_) { idResult.innerHTML = '<p class="subtle" style="padding:8px 0;">Upload failed — try again.</p>'; return; }
      videoInput.value = '';
      if (!data || data.error) { idResult.innerHTML = `<p class="subtle" style="padding:8px 0;">Couldn’t identify it: ${(data && data.error) || 'unknown error'}</p>`; return; }
      renderProposal(data);
    });

    function renderProposal(data) {
      const ex = data.exercise;
      const repLabel = ex.unit === 'seconds' ? `${ex.default_reps}s` : `${ex.default_reps} reps`;
      const demo = (data.demo && data.demo.length >= 2)
        ? `<div class="ex-demo-wrap ex-anim"><img class="ex-demo-img" src="${data.demo[0]}" loading="lazy"><img class="ex-demo-img ex-anim-frame2" src="${data.demo[1]}" loading="lazy"></div>`
        : '<p class="subtle" style="font-size:13px;">No matching demo in the database — it’ll be added without one.</p>';
      const match = data.db_match
        ? `Matched to “${data.db_match}” (${Math.round((data.confidence || 0) * 100)}% confidence).`
        : 'No confident database match — built from the video itself.';
      idResult.innerHTML = `
        <div class="identify-card" style="border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:10px;">
          <strong class="id-name"></strong>
          <div class="ex-meta id-meta"></div>
          ${demo}
          <p class="subtle id-match" style="font-size:12px;"></p>
          <p class="ex-instr id-instr" style="font-size:13px;"></p>
          <div style="display:flex; gap:8px; margin-top:8px;">
            <button type="button" class="btn id-add" style="flex:1;">Add to workout</button>
            <button type="button" class="btn btn-secondary id-cancel" style="flex:1;">Cancel</button>
          </div>
        </div>`;
      idResult.querySelector('.id-name').textContent = ex.name;
      idResult.querySelector('.id-meta').textContent = `${ex.category} · ${ex.equipment} · ${ex.default_sets} × ${repLabel}`;
      idResult.querySelector('.id-match').textContent = match;
      idResult.querySelector('.id-instr').textContent = ex.instructions || '';
      idResult.querySelector('.id-cancel').addEventListener('click', reset);
      idResult.querySelector('.id-add').addEventListener('click', async () => {
        try {
          await fetch('/api/identify-exercise/confirm', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ exercise: ex, demo: data.demo }),
          });
        } catch (_) {}
        addExercise({
          id: ex.id, name: ex.name, category: ex.category, equipment: [ex.equipment],
          default_sets: ex.default_sets, default_reps: ex.default_reps, unit: ex.unit || 'reps',
          instructions: ex.instructions, rest_seconds: ex.rest_seconds,
          anim: (data.demo && data.demo.length >= 2) ? data.demo : null, available: true,
        }, true);
        reset();
        modal.classList.add('hidden');
      });
    }
  }
}
