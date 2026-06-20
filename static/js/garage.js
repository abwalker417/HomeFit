/* HomeFit garage kiosk — workout logger. One exercise at a time, big buttons,
   logs every set on the panel, auto rest timer, posts at Finish. */
(function () {
  const W = window.GARAGE;
  if (!W || !W.exercises || !W.exercises.length) return;
  const exs = W.exercises;
  exs.forEach((e) => {
    e.logged = [];
    e.workW = e.last_weight != null ? e.last_weight : 0;
    e.workR = e.reps;
  });
  let cur = 0;
  const started = Date.now();
  let restTimer = null;
  const $ = (id) => document.getElementById(id);
  const ex = () => exs[cur];

  function render() {
    const e = ex();
    const done = e.logged.length >= e.sets;
    $("g-ex-name").textContent = e.name;
    $("g-progress").textContent = `${cur + 1}/${exs.length}`;
    $("g-set-line").textContent = done
      ? `✓ All ${e.sets} sets done`
      : `Set ${e.logged.length + 1} of ${e.sets} · target ${e.reps}${e.unit === "reps" ? " reps" : ""}`;
    $("g-weight").textContent = e.workW;
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
    if (a === "w+") e.workW += 5;
    else if (a === "w-") e.workW = Math.max(0, e.workW - 5);
    else if (a === "r+") e.workR += 1;
    else if (a === "r-") e.workR = Math.max(0, e.workR - 1);
    render();
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
    render();
  });

  $("g-prev").addEventListener("click", () => { if (cur > 0) { cur--; render(); } });
  $("g-next").addEventListener("click", () => { if (cur < exs.length - 1) { cur++; render(); } });

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

  $("g-finish").addEventListener("click", async () => {
    const payload = {
      day_name: W.name,
      duration_seconds: Math.round((Date.now() - started) / 1000),
      exercises: exs.map((e) => ({ id: e.id, name: e.name,
        completed: e.logged.length > 0, sets: e.logged })),
    };
    try {
      await fetch("/api/garage/complete", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    } catch (_) {}
    const n = exs.filter((e) => e.logged.length > 0).length;
    $("g-done-msg").textContent = `${W.name} logged — ${n} exercise${n === 1 ? "" : "s"}`;
    $("g-done").classList.remove("hidden");
    setTimeout(() => { location.href = "/garage"; }, 3500);
  });

  render();
})();
