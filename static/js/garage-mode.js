/* Strip vs tablet decision, made in <head> BEFORE first paint.
   The wall strip is 1920x440 (4.36:1) and keeps the scaled fixed stage;
   any normally-shaped screen (tablet/phone, aspect < 2.6) gets the fluid
   g-tablet layout from garage.css instead. */
(function () {
  // viewport aspect, not screen.*: kiosk browsers are fullscreen so they
  // match, and the ratio is invariant to the width=1920 viewport scaling
  var w = window.innerWidth || screen.width, h = window.innerHeight || screen.height;
  if (Math.max(w, h) / Math.min(w, h) < 2.6) {
    document.documentElement.className += " g-tablet";
    var m = document.querySelector('meta[name="viewport"]');
    if (m) m.setAttribute("content",
      "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");
  }
})();
