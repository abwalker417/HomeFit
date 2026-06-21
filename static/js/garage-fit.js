/* Lock the garage UI to a 1920x480 stage and scale-to-fit any display/window. */
(function () {
  function fit() {
    var s = document.querySelector(".g-stage");
    if (!s) return;
    s.style.transform = "scale(" + Math.min(window.innerWidth / 1920, window.innerHeight / 480) + ")";
  }
  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", fit);
  fit();
})();
