/* BuiltHere phone workout — panel-style, one exercise at a time. Weight/reps
   steppers, "Log Set" auto-starts the rest timer, prev/next nav. Shares a v2
   draft format with the garage panel so a session resumes across phone <-> panel.
   Overrides the legacy list-based startWorkout() from app.js on this page. */
window.startWorkout = function () {
  const root = document.getElementById('workout-body');
  if (!root) return;
  const userId = root.dataset.userId || 'default';
  const STORE_KEY = `homefit_wk_${userId}`;
  const dayName = root.dataset.dayName;
  const dayNumber = parseInt(root.dataset.dayNumber, 10) || 1;

  let WK = {};
  try { WK = JSON.parse(document.getElementById('wk-data').textContent) || {}; } catch (_) {}
  const exs = (WK.exercises || []).filter((e) => e && e.id);
  if (!exs.length) return;
  const WM = window.WeightMode;
  exs.forEach((e) => { e.sets = Math.max(1, parseInt(e.sets, 10) || 1); e.reps = parseInt(e.reps, 10) || 10; e.mode = WM.modeOf(e); });

  const BODYWEIGHT = ['bodyweight', 'none', '', 'pull_up_bar', 'bench_or_chair', 'resistance_bands'];
  function isWeighted(e) {
    if (e.unit === 'seconds') return false;
    let eq = e.equipment;
    if (Array.isArray(eq)) eq = eq.length ? eq[0] : '';
    return !(!eq || BODYWEIGHT.includes(eq));
  }
  function defaultW(e) {
    const wh = e.weight_hint;
    const w = wh ? (wh.suggested_weight != null ? wh.suggested_weight : (wh.last_weight != null ? wh.last_weight : 0)) : 0;
    return WM.snap(e.mode, w);
  }

  // ---- state (v2 draft shape, shared with the garage panel) ----
  function freshState() {
    const ex = {};
    exs.forEach((e) => { ex[e.id] = { logged: [], workW: defaultW(e), workR: e.reps }; });
    return { v: 2, dayName, started: Date.now(), offset: 0, cur: 0, ex, t: Date.now() };
  }
  // A draft is resumable if it's recent and shares enough exercises with this
  // workout. This handles crash recovery AND phone<->panel handoff (the two
  // surfaces label the day differently, so we match on exercises, not name).
  const RESUME_MAX_MS = 8 * 3600 * 1000;
  const curIds = new Set(exs.map((e) => String(e.id)));
  function usable(raw) {
    let s; try { s = (typeof raw === 'string') ? JSON.parse(raw) : raw; } catch (_) { return null; }
    if (!s || s.v !== 2 || !s.ex) return null;
    if (!s.t || (Date.now() - s.t) > RESUME_MAX_MS) return null;
    const overlap = Object.keys(s.ex).filter((k) => curIds.has(String(k))).length;
    if (overlap < Math.max(1, Math.ceil(exs.length / 2))) return null;
    return s;
  }
  let state = freshState();
  // Pick the freshest of localStorage vs the server draft (panel writes the same
  // shape) so a handoff resumes wherever you left off.
  const cands = [];
  const local = usable(localStorage.getItem(STORE_KEY));
  if (local) cands.push(local);
  let draftRaw = null;
  try { draftRaw = JSON.parse(document.getElementById('draft-state').textContent); } catch (_) {}
  const draft = usable(draftRaw);
  if (draft) cands.push(draft);
  if (cands.length) {
    const best = cands.sort((a, b) => (b.t || 0) - (a.t || 0))[0];
    state = freshState();
    state.started = best.started || state.started;
    state.offset = best.offset || 0;
    state.cur = Math.min(Math.max(0, best.cur || 0), exs.length - 1);
    exs.forEach((e) => {
      const saved = (best.ex || {})[e.id];
      if (saved) state.ex[e.id] = {
        logged: Array.isArray(saved.logged) ? saved.logged : [],
        workW: saved.workW != null ? WM.snap(e.mode, saved.workW) : defaultW(e),
        workR: saved.workR != null ? saved.workR : e.reps,
      };
    });
  }
  let cur = state.cur;
  localStorage.setItem(STORE_KEY, JSON.stringify(state));

  let autosaveTimer = null;
  function save() {
    state.cur = cur; state.t = Date.now();
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => {
      fetch('/api/workout/autosave', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ day_name: dayName, state }),
      }).catch(() => {});
    }, 1200);
  }

  const $ = (id) => document.getElementById(id);
  const exAt = (i) => exs[i];
  const stOf = (e) => state.ex[e.id] || (state.ex[e.id] = { logged: [], workW: defaultW(e), workR: e.reps });
  const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  // ---- timer (tap to correct) ----
  const timerEl = $('workout-timer');
  const elapsed = () => Math.floor((Date.now() - state.started) / 1000) + (state.offset || 0);
  let timerInt = null;
  function tick() {
    const s = elapsed();
    timerEl.textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }
  tick(); timerInt = setInterval(tick, 1000);
  timerEl.addEventListener('click', () => {
    const mins = Math.floor(elapsed() / 60);
    const input = prompt('Correct workout time (minutes):', mins);
    if (input !== null) {
      const m = parseInt(input, 10);
      if (!isNaN(m) && m >= 0) { state.started = Date.now(); state.offset = m * 60; save(); }
    }
    tick();
  });

  // ---- render the current exercise ----
  function render() {
    const e = exAt(cur), s = stOf(e);
    const done = s.logged.length >= e.sets;
    const timed = e.unit === 'seconds';
    $('wk-ex-name').textContent = e.name;
    $('wk-progress').textContent = `${cur + 1} / ${exs.length}`;
    $('wk-setline').textContent = done
      ? `✓ All ${e.sets} sets done`
      : `Set ${s.logged.length + 1} of ${e.sets} · target ${e.reps}${timed ? 's' : ' reps'}`;
    let dots = '';
    for (let i = 0; i < e.sets; i++) {
      const c = i < s.logged.length ? 'done' : (i === s.logged.length && !done ? 'current' : '');
      dots += `<div class="wk-dot ${c}"></div>`;
    }
    $('wk-dots').innerHTML = dots;
    const wstep = $('wk-weight-stepper');
    if (isWeighted(e)) {
      wstep.style.display = '';
      const wd = WM.display(e.mode, s.workW);
      $('wk-weight').textContent = wd.main;
      const wsub = $('wk-weight-sub');
      if (wsub) { wsub.textContent = wd.sub; wsub.style.display = wd.sub ? '' : 'none'; }
    } else { wstep.style.display = 'none'; }
    $('wk-reps-k').textContent = timed ? 'Seconds' : 'Reps';
    $('wk-reps').textContent = s.workR;
    const log = $('wk-log');
    log.textContent = done ? '✓ Done' : '✓ Log Set';
    log.disabled = done; log.style.opacity = done ? '.5' : '1';
    $('wk-prev').disabled = cur === 0;
    $('wk-next').disabled = cur === exs.length - 1;
  }

  // ---- steppers ----
  document.querySelectorAll('.wk-step-btn').forEach((b) => b.addEventListener('click', () => {
    const e = exAt(cur), s = stOf(e), a = b.dataset.act;
    if (a === 'w+') s.workW = WM.step(e.mode, s.workW, +1);
    else if (a === 'w-') s.workW = WM.step(e.mode, s.workW, -1);
    else if (a === 'r+') s.workR += 1;
    else if (a === 'r-') s.workR = Math.max(0, s.workR - 1);
    render(); save();
  }));

  // ---- log set -> auto rest, advance when an exercise is done ----
  $('wk-log').addEventListener('click', () => {
    const e = exAt(cur), s = stOf(e);
    if (s.logged.length >= e.sets) return;
    s.logged.push({ weight: isWeighted(e) ? s.workW : null, reps: s.workR });
    startRest(e.rest || 60);
    if (s.logged.length >= e.sets) {
      const next = exs.findIndex((x, i) => i > cur && stOf(x).logged.length < x.sets);
      if (next !== -1) cur = next;
    }
    render(); save();
  });

  $('wk-prev').addEventListener('click', () => { if (cur > 0) { cur--; render(); save(); } });
  $('wk-next').addEventListener('click', () => { if (cur < exs.length - 1) { cur++; render(); save(); } });

  // ---- rest timer ----
  let restInt = null;
  function startRest(sec) {
    clearInterval(restInt);
    const r = $('wk-rest');
    r.classList.remove('hidden', 'done'); r.classList.add('active');
    let left = sec;
    const t = () => {
      if (left < 0) {
        clearInterval(restInt);
        r.classList.remove('active'); r.classList.add('done');
        $('wk-rest-time').textContent = 'GO';
        if ('vibrate' in navigator) navigator.vibrate(200);
        return;
      }
      $('wk-rest-time').textContent = fmt(left);
      left--;
    };
    t(); restInt = setInterval(t, 1000);
  }
  $('wk-rest-skip').addEventListener('click', () => {
    clearInterval(restInt);
    const r = $('wk-rest'); r.classList.add('hidden'); r.classList.remove('active', 'done');
  });

  // ---- how-to overlay: holds ONE exercise's demo, frees images on close ----
  const ov = $('wk-howto-ov');
  $('wk-howto').addEventListener('click', () => {
    const e = exAt(cur);
    $('wk-howto-name').textContent = e.name;
    const m = $('wk-howto-media');
    if (e.anim && e.anim.length >= 2) {
      m.innerHTML = `<div class="wk-howto-anim"><img src="${e.anim[0]}"><img class="wk-howto-f2" src="${e.anim[1]}"></div>`;
      m.style.display = '';
    } else if (e.demo_image) {
      m.innerHTML = `<img src="${e.demo_image}" alt="">`; m.style.display = '';
    } else { m.innerHTML = ''; m.style.display = 'none'; }
    $('wk-howto-text').textContent = e.instructions || 'No instructions for this move yet.';
    $('wk-howto-yt').href = 'https://www.youtube.com/results?search_query=' + encodeURIComponent(e.name + ' proper form');
    ov.classList.remove('hidden');
  });
  function closeHowto() { ov.classList.add('hidden'); $('wk-howto-media').innerHTML = ''; }
  $('wk-howto-close').addEventListener('click', closeHowto);
  ov.addEventListener('click', (ev) => { if (ev.target === ov) closeHowto(); });

  // ---- add exercise from the library ----
  (function setupAdd() {
    const modal = $('add-ex-modal'); if (!modal) return;
    const listEl = $('add-ex-list'), search = $('add-ex-search'), filter = $('add-ex-filter');
    let lib = [];
    try { lib = JSON.parse(document.getElementById('exercise-library').textContent) || []; } catch (_) {}
    function renderLib() {
      const q = (search.value || '').trim().toLowerCase(), cat = filter.value;
      listEl.innerHTML = '';
      lib.filter((x) => (cat === 'all' || x.category === cat) && (!q || x.name.toLowerCase().includes(q)))
        .slice(0, 80).forEach((x) => {
          const li = document.createElement('li');
          li.className = 'exercise-item';
          li.innerHTML = `<div class="ex-head" style="padding:10px 12px;align-items:center;">
            <div class="ex-title"><strong>${x.name}</strong><span class="ex-meta">${x.category || ''}</span></div>
            <button type="button" class="pill add-this" style="border:none;cursor:pointer;">Add</button></div>`;
          li.querySelector('.add-this').addEventListener('click', () => { addExercise(x); modal.classList.add('hidden'); });
          listEl.appendChild(li);
        });
    }
    ['input', 'keyup', 'change', 'search'].forEach((ev) => search.addEventListener(ev, renderLib));
    filter.addEventListener('change', renderLib);
    $('wk-add').addEventListener('click', () => { renderLib(); modal.classList.remove('hidden'); });
    $('add-ex-close').addEventListener('click', () => modal.classList.add('hidden'));
  })();

  function addExercise(x) {
    const existing = exs.findIndex((z) => z.id === x.id);
    if (existing !== -1) { cur = existing; render(); return; }
    const e = {
      id: x.id, name: x.name, sets: Math.max(1, x.default_sets || 3), reps: x.default_reps || 10,
      unit: x.unit || 'reps', rest: x.rest_seconds || 60, instructions: x.instructions || '',
      equipment: x.equipment, anim: x.anim, demo_image: null, weight_hint: null,
    };
    e.mode = WM.modeOf(e);
    exs.push(e);
    state.ex[e.id] = { logged: [], workW: defaultW(e), workR: e.reps };
    cur = exs.length - 1;
    render(); save();
  }

  // ---- finish ----
  const finishBtn = $('finish-btn');
  finishBtn.addEventListener('click', () => {
    const none = exs.filter((e) => stOf(e).logged.length === 0);
    if (none.length) {
      const list = $('skip-list'); list.innerHTML = '';
      none.forEach((e) => { const li = document.createElement('li'); li.textContent = e.name; list.appendChild(li); });
      $('skip-confirm').classList.remove('hidden');
      return;
    }
    doFinish();
  });
  $('skip-back').addEventListener('click', () => $('skip-confirm').classList.add('hidden'));
  $('skip-finish').addEventListener('click', () => { $('skip-confirm').classList.add('hidden'); doFinish(); });

  async function doFinish() {
    clearInterval(timerInt);
    const duration = elapsed();
    const items = exs.map((e) => ({
      id: e.id, name: e.name,
      completed: stOf(e).logged.length > 0,
      sets: stOf(e).logged,
    }));
    const payload = { day_number: dayNumber, day_name: dayName, duration_seconds: duration, exercises: items };
    finishBtn.disabled = true; finishBtn.textContent = 'Saving…';
    try {
      const resp = await fetch('/api/complete_workout', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error('server');
      const data = await resp.json();
      localStorage.removeItem(STORE_KEY);
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.healthKit) {
        window.webkit.messageHandlers.healthKit.postMessage({
          type: 'logWorkout',
          durationSeconds: duration,
          kcal: data.kcal || 0,
          startTime: new Date(Date.now() - duration * 1000).toISOString(),
          endTime: new Date().toISOString(),
        });
      }
      showCompletionScreen(duration, data.kcal, data.exercises_completed, dayName);
    } catch (err) {
      finishBtn.disabled = false; finishBtn.textContent = 'Retry finish';
    }
  }

  function showCompletionScreen(durationSecs, kcal, exerciseCount, dName) {
    const wcSection = document.getElementById('workout-complete');
    if (!wcSection) return;
    const mins = Math.floor(durationSecs / 60), secs = durationSecs % 60;
    const timeEl = document.getElementById('wc-time');
    const kcalEl = document.getElementById('wc-kcal');
    const exEl = document.getElementById('wc-exercises');
    const nameEl = document.getElementById('wc-day-name');
    if (timeEl) timeEl.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    if (kcalEl) kcalEl.textContent = kcal ? Math.round(kcal) : '--';
    if (exEl) exEl.textContent = exerciseCount != null ? exerciseCount : '--';
    if (nameEl) nameEl.textContent = dName || 'Workout';
    document.getElementById('workout-body').style.display = 'none';
    wcSection.classList.remove('hidden');

    const exercisesForInsight = exs
      .filter((e) => stOf(e).logged.length > 0)
      .map((e) => ({ name: e.name, sets: stOf(e).logged }));
    fetch('/api/post-workout-insight', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exercises: exercisesForInsight }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
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
            list.innerHTML = data.overload.map((s) =>
              `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:14px;">
                <span>${s.exercise_name}</span>
                <span style="color:var(--accent);font-weight:600;">${s.current_weight} → ${s.suggested_weight} lbs</span>
              </div>`).join('');
            card.classList.remove('hidden');
          }
        }
      })
      .catch(() => {
        const body = document.getElementById('apex-summary-body');
        if (body) body.innerHTML = `<p style="color:var(--subtle);font-size:14px;margin:0;">Analysis unavailable.</p>`;
      });
  }

  render();
};
