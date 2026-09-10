/* How a calendar cell shows its detail — shared by both surfaces.
 *
 * Loaded twice: inlined into the read-only `components.html` iframe by
 * `parkrun_calendar._embed`, and as a plain <script> by the clickable
 * component's index.html. One copy, because a phone that behaved differently
 * on two tabs of the same app would read as a bug rather than as two features.
 *
 * Two presentations, chosen by the device rather than by a setting:
 *   - a floating label that follows the pointer, armed only where a pointer
 *     can hover;
 *   - a sheet that rises from the bottom of the page on a tap or a click.
 *
 * Config comes from `window.CAL_DETAIL = {bg, fg}` set before this loads.
 * `window.calCellClick`, if set, is called with the clicked element — that is
 * how the component tells Python which square was hit.
 */
(function () {
  var cfg = window.CAL_DETAIL || {};
  var BG = cfg.bg || '#24292f';
  var FG = cfg.fg || '#ffffff';

  // The sheet is built in the PARENT document. A `position: fixed` element
  // inside the frame is fixed to the frame, and the frame is only as tall as
  // the drawing, so a sheet built here would rise from the middle of the page.
  // Both surfaces are served from the app's own origin, so the parent is
  // reachable; if that ever stops being true, fall back to this document and
  // accept the worse position rather than losing the detail altogether.
  var pd, vv;
  try {
    pd = window.parent.document;
    vv = window.parent.visualViewport;
    if (!pd.body) { throw new Error('no parent body'); }
  } catch (e) {
    pd = document;
    vv = window.visualViewport;
  }

  var HID = 'translateY(0)', SHOWN = 'translateY(-100%)';

  // Pinch-zoom is the reason for the visualViewport arithmetic. A fixed
  // element is positioned against the LAYOUT viewport, which neither moves nor
  // shrinks when the page is zoomed, so a plain bottom:0 sheet ends up at the
  // bottom of the whole magnified page — usually off-screen, and drawn at the
  // zoom factor if you find it. visualViewport is what is actually being
  // looked at: the sheet sits on its bottom edge, and its type is divided by
  // the zoom so it appears the same physical size however far you have zoomed.
  function fit(el) {
    if (!vv) { return; }
    var k = 1 / (vv.scale || 1);
    el.style.left = vv.offsetLeft + 'px';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    el.style.width = vv.width + 'px';
    el.style.top = (vv.offsetTop + vv.height) + 'px';
    el.style.fontSize = (13 * k) + 'px';
    el.style.padding = (14 * k) + 'px ' + (16 * k) + 'px calc(' +
      (16 * k) + 'px + env(safe-area-inset-bottom))';
    el.style.borderRadius = (14 * k) + 'px ' + (14 * k) + 'px 0 0';
    el.style.maxHeight = (vv.height * 0.5) + 'px';
    var bar = el.firstChild;
    bar.style.width = (38 * k) + 'px';
    bar.style.height = (4 * k) + 'px';
    bar.style.margin = (-6 * k) + 'px auto ' + (10 * k) + 'px';
  }

  function sheet() {
    var el = pd.getElementById('cal-sheet');
    if (el) { return el; }
    el = pd.createElement('div');
    el.id = 'cal-sheet';
    el.style.cssText =
      'position:fixed;left:0;right:0;bottom:0;z-index:1000;box-sizing:border-box;' +
      'background:' + BG + ';color:' + FG + ';' +
      'padding:14px 16px calc(16px + env(safe-area-inset-bottom));' +
      'border-radius:14px 14px 0 0;box-shadow:0 -6px 24px rgba(0,0,0,.3);' +
      'font:13px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;' +
      'white-space:pre-wrap;transform:' + (vv ? HID : 'translateY(110%)') + ';' +
      'transition:transform .22s ease-out;max-height:50vh;overflow:auto';
    var bar = pd.createElement('div');
    bar.style.cssText =
      'width:38px;height:4px;border-radius:2px;opacity:.45;margin:-6px auto 10px;' +
      'background:' + FG;
    var body = pd.createElement('div');
    body.id = 'cal-sheet-body';
    el.appendChild(bar);
    el.appendChild(body);
    // Tapping the sheet dismisses it; so does tapping anywhere else on the
    // page, which is what a native sheet does and what a thumb expects.
    el.addEventListener('click', hide);
    pd.addEventListener('click', function (ev) {
      if (!el.contains(ev.target)) { hide(); }
    });
    pd.body.appendChild(el);
    fit(el);
    return el;
  }

  function hide() {
    var el = pd.getElementById('cal-sheet');
    if (el) { el.style.transform = vv ? HID : 'translateY(110%)'; }
  }

  function showSheet(text) {
    var sh = sheet();
    fit(sh);
    // Plain text, never markup: `data-t` is HTML-escaped on the way in, so
    // both calendars hand over the same kind of thing and neither can inject.
    pd.getElementById('cal-sheet-body').textContent = text;
    sh.style.transform = vv ? SHOWN : 'translateY(0)';
  }

  if (vv) {
    // Zooming or panning while the sheet is open moves the edge it sits on.
    var track = function () {
      var el = pd.getElementById('cal-sheet');
      if (el) { fit(el); }
    };
    vv.addEventListener('resize', track);
    vv.addEventListener('scroll', track);
  }

  // ----- the floating label, for a pointer that can hover ------------------ #
  var tt = document.getElementById('tt');
  var canHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;

  function place(x, y) {
    var pad = 14, w = tt.offsetWidth, h = tt.offsetHeight;
    var nx = x + pad, ny = y + pad;
    if (nx + w > window.innerWidth - 4) { nx = x - w - pad; }
    if (ny + h > window.innerHeight - 4) { ny = y - h - pad; }
    if (nx < 2) { nx = 2; }
    if (ny < 2) { ny = 2; }
    tt.style.left = nx + 'px';
    tt.style.top = ny + 'px';
  }

  function hideLabel() { if (tt) { tt.style.display = 'none'; } }

  if (tt && canHover) {
    document.addEventListener('mouseover', function (e) {
      var el = e.target.closest && e.target.closest('[data-t]');
      if (!el) { return; }
      tt.textContent = el.getAttribute('data-t');
      tt.style.display = 'block';
      place(e.clientX, e.clientY);
    });
    document.addEventListener('mouseout', function (e) {
      if (e.target.closest && e.target.closest('[data-t]')) { hideLabel(); }
    });
    document.addEventListener('mousemove', function (e) {
      if (tt.style.display === 'block') { place(e.clientX, e.clientY); }
    });
    window.addEventListener('blur', hideLabel);
  }

  // ----- the tap ----------------------------------------------------------- #
  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-t]');
    if (!el) { return; }
    // The sheet is for a thumb. Where a pointer can hover the label has
    // already said the same thing beside the cursor, and a sheet rising from
    // the bottom of the page as well is noise sitting over the rest of the tab.
    if (!canHover) { showSheet(el.getAttribute('data-t')); }
    if (typeof window.calCellClick === 'function') { window.calCellClick(el); }
    // Stop the parent's own dismiss-on-click handler seeing this click and
    // closing the sheet in the same gesture that opened it.
    e.stopPropagation();
  });
})();
