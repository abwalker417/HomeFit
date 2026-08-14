/* Lock the garage UI to a 1920x440 stage and scale-to-fit any display/window. */
(function () {
  function fit() {
    // tablet/phone mode (set by garage-mode.js): stage is fluid, no scaling
    if (document.documentElement.classList.contains("g-tablet")) return;
    var s = document.querySelector(".g-stage");
    if (!s) return;
    s.style.transform = "scale(" + Math.min(window.innerWidth / 1920, window.innerHeight / 440) + ")";
  }
  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", fit);
  fit();
})();

/* Dim-when-dark: deep-dim the panel when the garage light is off; tap to wake
   briefly, then it re-dims. Driven by /api/garage/light (HA via BuiltHere). */
(function () {
  var DIM = 0.93, WAKE_MS = 10000, POLL_MS = 12000;
  var veil = document.createElement("div");
  veil.style.cssText =
    "position:fixed;inset:0;z-index:9999;background:#000;opacity:0;" +
    "transition:opacity .8s ease;pointer-events:none;";
  document.body.appendChild(veil);

  var lightOff = false, awakeUntil = 0;
  function apply() {
    var dim = lightOff && Date.now() >= awakeUntil;
    veil.style.opacity = dim ? String(DIM) : "0";
    veil.style.pointerEvents = dim ? "auto" : "none";
  }
  veil.addEventListener("pointerdown", function () { awakeUntil = Date.now() + WAKE_MS; apply(); });

  function poll() {
    fetch("/api/garage/light").then(function (r) { return r.json(); }).then(function (d) {
      // only dim when HA explicitly reports the light off; never get stuck dark
      lightOff = !!(d && d.available && d.on === false);
      apply();
    }).catch(function () { lightOff = false; apply(); });
  }
  poll();
  setInterval(poll, POLL_MS);
  setInterval(apply, 1000);  // re-dim once the wake window expires
})();
