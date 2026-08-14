/* BuiltHere equipment-aware weight steppers, shared by the garage panel and the
   phone flow. workW (the value logged and saved in drafts) is always the REAL
   lifted weight:
     - dumbbells: 5 lb bells, 5 lb steps
     - barbell:   total incl. the 45 lb bar, plates added in pairs (5 lb/side)
     - landmine:  total incl. the 45 lb bar, plates on ONE side (5 lb steps)
     - cable:     effective weight = stack / 2 (GarveeLife 2:1 pulley; stack is
                  11 lb plates up to 220, so logged weight moves in 5.5s) */
window.WeightMode = (function () {
  var BAR = 45, SIDE = 5, CABLE = 5.5, CABLE_MAX = 110; // 220 lb stack / 2

  function modeOf(e) {
    var id = String(e.id || '').toLowerCase();
    if (id.indexOf('landmine') === 0) return 'landmine';
    var eq = e.equipment;
    if (Array.isArray(eq)) eq = eq.length ? eq[0] : '';
    eq = String(eq || '').toLowerCase().replace(/_/g, ' ');
    if (eq.indexOf('cage') !== -1 || eq.indexOf('cable') !== -1) {
      // The cage tag covers three load types: cable-stack attachments,
      // bodyweight bars, and barbell lifts racked in the cage.
      if (/cable|pulldown|pushdown|face.?pull/.test(id)) return 'cable';
      if (/pullup|pull.?up|dip/.test(id)) return 'flat';
      return 'barbell';
    }
    if (eq.indexOf('barbell') !== -1) return 'barbell';
    if (eq.indexOf('dumbbell') !== -1) return 'dumbbell';
    return 'flat';
  }

  // Pull any weight onto the equipment's grid (old drafts / history may be off it).
  function snap(mode, w) {
    w = +w || 0;
    if (mode === 'cable') return Math.max(1, Math.min(20, Math.round(w / CABLE))) * CABLE;
    if (mode === 'barbell') return w <= BAR ? BAR : BAR + Math.round((w - BAR) / (2 * SIDE)) * 2 * SIDE;
    if (mode === 'landmine') return w <= BAR ? BAR : BAR + Math.round((w - BAR) / SIDE) * SIDE;
    if (mode === 'dumbbell') return Math.max(5, Math.round(w / 5) * 5);
    return Math.max(0, w);
  }

  function step(mode, w, dir) {
    var inc = mode === 'cable' ? CABLE : mode === 'barbell' ? 2 * SIDE : 5;
    var next = snap(mode, w) + dir * inc;
    return snap(mode, next);
  }

  // Big number + helper line for the stepper. Cable shows "stack / logged" so
  // you can read the pin plate straight off the display.
  function display(mode, w) {
    if (mode === 'cable') {
      return { main: fmt(w * 2) + ' / ' + fmt(w), sub: 'pin ' + fmt(w * 2) + ' lb stack · logs ' + fmt(w) + ' lb' };
    }
    if (mode === 'barbell') {
      var side = (w - BAR) / 2;
      return { main: fmt(w), sub: side > 0 ? '45 bar + ' + fmt(side) + ' lb/side' : 'bar only' };
    }
    if (mode === 'landmine') {
      var one = w - BAR;
      return { main: fmt(w), sub: one > 0 ? '45 bar + ' + fmt(one) + ' lb one side' : 'bar only' };
    }
    return { main: fmt(w), sub: '' };
  }

  function fmt(n) { return String(Math.round(n * 2) / 2); }

  return { modeOf: modeOf, snap: snap, step: step, display: display };
})();
