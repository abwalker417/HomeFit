/* HomeFit garage kiosk — workout logger. One exercise at a time, big buttons,
   logs every set on the panel, auto rest timer, posts at Finish. */
(function () {
  const W = window.GARAGE;
  if (!W || !W.exercises || !W.exercises.length) return;
  const exs = W.exercises;
  const WM = window.WeightMode;
  exs.forEach((e) => {
    e.logged = [];
    e.mode = WM.modeOf(e);
    e.workW = WM.snap(e.mode, e.last_weight != null ? e.last_weight : 0);
    e.workR = e.reps;
  });
  let cur = 0;
  let started = Date.now();
  let offset = 0;
  let restTimer = null;

  // Resume a session in progress (shared v2 draft — may have been started on the
  // phone and handed off to the panel, or vice-versa). Gate on recency + exercise
  // overlap rather than the day label, which differs between phone and panel.
  const draft = window.GARAGE_DRAFT;
  (function resume() {
    if (!draft || draft.v !== 2 || !draft.ex) return;
    if (!draft.t || (Date.now() - draft.t) > 8 * 3600 * 1000) return;
    const ids = exs.map((e) => String(e.id));
    const overlap = Object.keys(draft.ex).filter((k) => ids.includes(String(k))).length;
    if (overlap < Math.max(1, Math.ceil(exs.length / 2))) return;
    if (draft.started) started = draft.started;
    offset = draft.offset || 0;
    cur = Math.min(Math.max(0, draft.cur || 0), exs.length - 1);
    exs.forEach((e) => {
      const s = draft.ex[e.id];
      if (s) {
        e.logged = Array.isArray(s.logged) ? s.logged : [];
        if (s.workW != null) e.workW = WM.snap(e.mode, s.workW);
        if (s.workR != null) e.workR = s.workR;
      }
    });
  })();

  // Mirror progress to the shared server draft (debounced) so the phone can pick
  // up where the panel left off — and a crash loses nothing.
  let draftTimer = null;
  function saveDraft() {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      const ex = {};
      exs.forEach((e) => { ex[e.id] = { logged: e.logged, workW: e.workW, workR: e.workR }; });
      const state = { v: 2, dayName: W.name, started, offset, cur, ex, t: Date.now() };
      fetch("/api/garage/autosave", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ day_name: W.name, state }),
      }).catch(() => {});
    }, 1000);
  }
  const $ = (id) => document.getElementById(id);
  const ex = () => exs[cur];
  const fmtT = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  // Total workout time — ticks up from the moment the logger opened.
  function tickElapsed() {
    const el = $("g-elapsed");
    if (el) el.textContent = fmtT(Math.round((Date.now() - started) / 1000) + offset);
  }
  tickElapsed();
  setInterval(tickElapsed, 1000);

  function render() {
    const e = ex();
    const done = e.logged.length >= e.sets;
    $("g-ex-name").textContent = e.name;
    $("g-progress").textContent = `${cur + 1}/${exs.length}`;
    $("g-set-line").textContent = done
      ? `✓ All ${e.sets} sets done`
      : `Set ${e.logged.length + 1} of ${e.sets} · target ${e.reps}${e.unit === "reps" ? " reps" : ""}`;
    const wd = WM.display(e.mode, e.workW);
    $("g-weight").textContent = wd.main;
    const wsub = $("g-weight-sub");
    if (wsub) { wsub.textContent = wd.sub; wsub.style.display = wd.sub ? "" : "none"; }
    $("g-reps").textContent = e.workR;
    let dots = "";
    for (let i = 0; i < e.sets; i++) {
      const cls = i < e.logged.length ? "done" : (i === e.logged.length && !done ? "current" : "");
      dots += `<div class="g-dot ${cls}"></div>`;
    }
    $("g-dots").innerHTML = dots;
    $("g-log").textContent = done ? "✓ Done" : "✓ Log Set";
    $("g-log").disabled = done;
    $("g-log").style.opacity = done ? ".5" : "1";
    $("g-prev").disabled = cur === 0;
    $("g-next").disabled = cur === exs.length - 1;
  }

  document.querySelectorAll(".g-step-btn").forEach((b) => b.addEventListener("click", () => {
    const e = ex(), a = b.dataset.act;
    if (a === "w+") e.workW = WM.step(e.mode, e.workW, +1);
    else if (a === "w-") e.workW = WM.step(e.mode, e.workW, -1);
    else if (a === "r+") e.workR += 1;
    else if (a === "r-") e.workR = Math.max(0, e.workR - 1);
    render(); saveDraft();
  }));

  $("g-log").addEventListener("click", () => {
    const e = ex();
    if (e.logged.length >= e.sets) return;
    e.logged.push({ weight: e.workW, reps: e.workR });
    startRest(e.rest || 60);
    if (e.logged.length >= e.sets) {
      const next = exs.findIndex((x, i) => i > cur && x.logged.length < x.sets);
      if (next !== -1) cur = next;
    }
    render(); saveDraft();
  });

  $("g-prev").addEventListener("click", () => { if (cur > 0) { cur--; render(); saveDraft(); } });
  $("g-next").addEventListener("click", () => { if (cur < exs.length - 1) { cur++; render(); saveDraft(); } });

  // ---- exercise how-to demo (no phone needed) ----
  $("g-demo-btn").addEventListener("click", () => {
    const e = ex();
    $("g-demo-name").textContent = e.name;
    const m = $("g-demo-media");
    if (e.anim && e.anim.length >= 2) {
      m.innerHTML = `<div class="g-demo-anim"><img src="${e.anim[0]}"><img class="g-demo-f2" src="${e.anim[1]}"></div>`;
      m.style.display = "";
    } else {
      m.style.display = "none";
    }
    $("g-demo-text").textContent = e.instructions || "No instructions for this move yet.";
    $("g-demo").classList.remove("hidden");
  });
  $("g-demo").addEventListener("click", () => $("g-demo").classList.add("hidden"));

  function startRest(sec) {
    clearInterval(restTimer);
    const r = $("g-rest");
    r.classList.add("active"); r.classList.remove("done");
    let left = sec;
    const tick = () => {
      if (left < 0) {
        clearInterval(restTimer);
        r.classList.remove("active"); r.classList.add("done");
        $("g-rest-time").textContent = "GO";
        return;
      }
      $("g-rest-time").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      left--;
    };
    tick(); restTimer = setInterval(tick, 1000);
  }
  $("g-rest-skip").addEventListener("click", () => {
    clearInterval(restTimer);
    $("g-rest").classList.remove("active", "done");
    $("g-rest-time").textContent = "0:00";
  });

  const goHome = () => { location.href = "/garage"; };
  const doneBtn = $("g-done-btn");
  if (doneBtn) doneBtn.addEventListener("click", goHome);

  $("g-finish").addEventListener("click", async () => {
    const dur = Math.round((Date.now() - started) / 1000) + offset;
    const n = exs.filter((e) => e.logged.length > 0).length;
    const payload = {
      day_name: W.name,
      duration_seconds: dur,
      exercises: exs.map((e) => ({ id: e.id, name: e.name,
        completed: e.logged.length > 0, sets: e.logged })),
    };
    // Show the completion card immediately with what we know; fill kcal + APEX
    // feedback once the server responds.
    $("g-done-title").textContent = W.name;
    $("g-done-time").textContent = fmtT(dur);
    $("g-done-ex").textContent = n;
    $("g-done-kcal").textContent = "--";
    $("g-done-insight").textContent = "Analyzing your session…";
    $("g-done-overload").innerHTML = "";
    $("g-done").classList.remove("hidden");

    let data = {};
    try {
      const r = await fetch("/api/garage/complete", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      data = await r.json();
    } catch (_) {}

    if (data.kcal != null) $("g-done-kcal").textContent = Math.round(data.kcal);
    $("g-done-insight").textContent = data.insight
      || `Nice work — ${W.name} logged. ${n} exercise${n === 1 ? "" : "s"} in ${fmtT(dur)}.`;
    if (Array.isArray(data.overload) && data.overload.length) {
      $("g-done-overload").innerHTML = data.overload.slice(0, 4).map((s) =>
        `<div class="g-done-ov-row"><span>${s.exercise_name}</span>` +
        `<span>${s.current_weight} → ${s.suggested_weight} lb</span></div>`).join("");
    }
    // Kiosk reset so it's ready for the next person, but leave time to read.
    setTimeout(goHome, 60000);
  });

  // ---- now playing (garage media player) ----
  if (window.G_MEDIA) {
    let lastArt = "";
    async function loadNP() {
      let d;
      try { d = await (await fetch("/api/garage/media")).json(); } catch (_) { return; }
      if (!d || !d.available) return;
      $("g-np-title").textContent = d.title || "Nothing playing";
      $("g-np-artist").textContent = d.artist || "";
      const art = $("g-np-art");
      if (d.art && d.art.startsWith("/")) {
        const url = "/api/garage/media/art?path=" + encodeURIComponent(d.art);
        if (url !== lastArt) { art.innerHTML = '<img src="' + url + '" alt="">'; art.classList.add("has-art"); lastArt = url; }
      } else if (lastArt) { art.innerHTML = ""; art.classList.remove("has-art"); lastArt = ""; }
      const pb = $("g-np-play");
      if (pb) pb.querySelector("svg").innerHTML = d.state === "playing"
        ? '<path d="M14,19H18V5H14M6,19H10V5H6V19Z"/>' : '<path d="M8,5.14V19.14L19,12.14L8,5.14Z"/>';
    }
    document.querySelectorAll(".g-np-btn").forEach((b) => b.addEventListener("click", async () => {
      try { await fetch("/api/garage/media/" + b.dataset.m, { method: "POST" }); } catch (_) {}
      setTimeout(loadNP, 350);
    }));
    loadNP(); setInterval(loadNP, 4000);
  }

  render();
})();
