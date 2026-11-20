"use strict";

/* The reader. Large screens show the journal open, two pages at a time, and turn
   one leaf at a time: the leaf carries the page you are leaving on its front and
   the page you are going to on its back, as paper does. Small screens show one
   page at a time and skip the blank backs of pages. */

(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const body = document.body;
  const stage = $("#stage");
  const book = $("#book");
  const slotL = $(".slot.l img", book);
  const slotR = $(".slot.r img", book);
  const castL = $(".cast.l", book);
  const castR = $(".cast.r", book);
  const leaf = $(".leaf", book);
  const front = $(".front", leaf);
  const back = $(".back", leaf);
  const folioL = $(".folio.l", book);
  const folioR = $(".folio.r", book);
  const opening = $(".opening", book);
  const closing = $(".closing", book);
  const scrub = $("#scrub");
  const ticks = $("#ticks");
  const peek = $("#peek");
  const where = $("#where");
  const loupe = $("#loupe");
  const textPanel = $("#text");
  const textBody = $("#text-body");
  const srText = $("#sr-text");
  const contents = $("#contents");
  const entriesEl = $("#entries");
  const btn = {
    contents: $("#btn-contents"), contentsClose: $("#btn-contents-close"),
    text: $("#btn-text"), magnify: $("#btn-magnify"),
    prev: $("#btn-prev"), next: $("#btn-next"), open: $("#btn-open"), restart: $("#btn-restart"),
  };

  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const fine = matchMedia("(hover: hover) and (pointer: fine)");
  const LOUPE = 130, ZOOM = 2.4;

  let J, pages, spreads, stops, spreadOf;
  let mode = "spread";
  let pos = 0;
  let pw = 0, ph = 0, imgH = 1200;
  let turn = null, queued = 0;
  let magnifying = false;
  let entries = [];

  /* ---------- the journal's structure ---------- */

  // One page at a time ("single") is a window one page wide onto the open book. The
  // whole spread is always there; the book slides to bring its left or right page
  // into the window. stops[k] = { s: spread, side: 0 left | 1 right }: every page,
  // in reading order, the left-hand ones with their show-through included.
  const count = () => (mode === "spread" ? spreads.length : stops.length);
  const spreadAt = (k = pos) => (mode === "spread" ? k : stops[k].s);
  const view = (k = pos) => spreads[spreadAt(k)];
  const anchor = (k = pos) => {
    if (mode === "single") return spreads[stops[k].s][stops[k].side];
    const [l, r] = view(k);
    return r ?? l;
  };
  // Where the book sits, in page widths: 0 centres the spread; in the window, -0.5
  // shows its right page and +0.5 its left.
  const camOf = (k = pos) => (mode === "single" ? (stops[k].side ? -0.5 : 0.5) : 0);
  const isCover = (pi) => pi != null && pages[pi].role === "cover";
  const isBack = (pi) => pi != null && pages[pi].role === "back-cover";
  const atStart = (k = pos) => (mode === "spread" ? k === 0 && spreads[0][0] == null && isCover(spreads[0][1]) : k === 0 && isCover(anchor(0)));
  const atEnd = (k = pos) => k === count() - 1 && (mode === "spread" ? spreads[k][1] == null && isBack(spreads[k][0]) : isBack(anchor(k)));

  function posOf(pi, m = mode) {
    if (m === "spread") return spreadOf[pi];
    const k = stops.findIndex((st) => spreads[st.s][st.side] >= pi);
    return k < 0 ? stops.length - 1 : k;
  }

  const setCam = (c) => book.style.setProperty("--cam", c.toFixed(4));

  // Adam's page number for any page: a recto's own, or the recto it backs onto.
  function numberOf(pi) {
    for (let i = pi; i >= 0; i--) {
      if (pages[i].n != null) return pages[i].n;
      if (pages[i].back_of != null) return pages[i].back_of;
    }
    return 0;
  }

  function placeAt(pi) {
    const n = numberOf(pi);
    let place = "";
    for (const stop of J.route || []) if (stop.page <= n) place = stop.place;
    return n ? place : "";
  }

  function dateAt(pi) {
    for (let i = pi; i >= 0; i--) if (pages[i].dates.length) return pages[i].dates[pages[i].dates.length - 1];
    return "";
  }

  /* ---------- images ---------- */

  const src = (pi, h = imgH) => (pi == null ? "" : `img/${h}/${pages[pi].id}.webp`);
  const low = (pi) => src(pi, J.heights[0]); // 600 px, about 26 KB: the stand-in

  // Until the sharp page has arrived, the small one shows behind it (as the
  // background of its slot), so a slow connection gets a soft page, never a gap.
  function setImg(img, pi) {
    const s = src(pi), holder = img.parentElement;
    if (!s) { img.removeAttribute("src"); img.alt = ""; holder.style.backgroundImage = ""; return; }
    if (img.getAttribute("src") !== s) {
      img.src = s;
      if (img.complete && img.naturalWidth) holder.style.backgroundImage = "";
      else {
        holder.style.backgroundImage = `url("${low(pi)}")`;
        const sharp = () => { if (img.getAttribute("src") === s) holder.style.backgroundImage = ""; };
        img.decode().then(sharp, sharp);
      }
    }
    if (!img.closest(".leaf")) img.alt = pages[pi].label + (pages[pi].text ? ", handwritten" : "");
  }

  function setFace(face, pi) {
    setImg($("img", face), pi);
    face.classList.toggle("blank", pi == null);
  }

  function maskTo(cast, img) {
    const s = img.getAttribute("src");
    const m = s ? `url("${s}")` : "none";
    cast.style.webkitMaskImage = m;
    cast.style.maskImage = m;
  }

  // Reading ahead, nearest first and two at a time, at low priority so it never
  // holds up the page on screen: the next page's stand-in and then its sharp copy,
  // then the page before, then the ones two away.
  const cache = new Map();
  let queue = [];
  const ORDER = [[1, "low"], [1, "sharp"], [-1, "low"], [2, "low"], [-1, "sharp"], [-2, "low"], [2, "sharp"], [-2, "sharp"]];

  // Nothing is read ahead until the pages on screen have arrived: on a slow line
  // they would otherwise share it with everything queued behind them.
  let preloadFor = 0;
  function preload() {
    const ticket = ++preloadFor;
    queue = [];
    const onScreen = [slotL, slotR].filter((i) => i.getAttribute("src"));
    Promise.all(onScreen.map((i) => i.decode().catch(() => {}))).then(() => { if (ticket === preloadFor) readAhead(); });
  }

  function readAhead() {
    queue = [];
    for (const [d, kind] of ORDER) {
      const k = pos + d;
      if (k < 0 || k >= count()) continue;
      for (const pi of view(k).filter((pi) => pi != null)) queue.push(kind === "low" ? low(pi) : src(pi));
    }
    queue = queue.filter((s) => !cache.has(s));
    pump();
  }

  const loading = new Map(); // read-ahead downloads in progress: src -> Image
  function pump() {
    while (loading.size < 2 && queue.length) {
      const s = queue.shift();
      if (cache.has(s)) continue;
      const im = new Image();
      im.decoding = "async";
      im.fetchPriority = "low";
      im.onload = im.onerror = () => { loading.delete(s); pump(); };
      cache.set(s, im);
      loading.set(s, im);
      im.src = s;
    }
    while (cache.size > 80) cache.delete(cache.keys().next().value);
  }

  // Leaving for somewhere else entirely: stop reading ahead from here.
  function stopReadAhead() {
    queue = [];
    preloadFor++;
    for (const [s, im] of loading) { im.onload = im.onerror = null; im.src = ""; cache.delete(s); }
    loading.clear();
  }

  function decoded(imgs, wait = 350) {
    const all = imgs.filter((i) => i.getAttribute("src")).map((i) => i.decode().catch(() => {}));
    return Promise.race([Promise.all(all), new Promise((r) => setTimeout(r, wait))]);
  }

  /* ---------- drawing the current view ---------- */

  function render() {
    const [l, r] = view();
    setImg(slotL, l);
    setImg(slotR, r);
    setCam(camOf());
    leaf.hidden = true;
    castL.style.opacity = castR.style.opacity = 0;
    opening.style.removeProperty("--fade");
    closing.style.removeProperty("--fade");
    folioL.textContent = l != null ? pages[l].label : "";
    folioR.textContent = r != null ? pages[r].label : "";

    const wasEnds = body.classList.contains("at-start") || body.classList.contains("at-end");
    body.classList.toggle("at-start", atStart());
    body.classList.toggle("at-end", atEnd());
    if (mode === "single" && wasEnds !== (atStart() || atEnd())) layout();

    scrub.max = String(count() - 1);
    scrub.value = String(pos);
    scrub.setAttribute("aria-valuetext", describe());
    btn.prev.disabled = pos === 0;
    btn.next.disabled = pos === count() - 1;

    const pi = anchor();
    const place = placeAt(pi), date = dateAt(pi);
    where.textContent = atStart() || atEnd() ? "" : [place, date].filter(Boolean).join(", ");

    // The address names a page in view; a link to either page of a spread is left as it is.
    const named = pages.findIndex((p) => p.slug === decodeURIComponent(location.hash.slice(1)));
    if (!shownPages().includes(named)) history.replaceState(null, "", "#" + pages[pi].slug);

    showText();
    markEntry();
    preload();
    updateLoupe();
  }

  // The pages in view: both of a spread, or the one in the window.
  const shownPages = (k = pos) => (mode === "single" ? [anchor(k)] : view(k).filter((x) => x != null));

  function describe(k = pos) {
    return shownPages(k).map((x) => pages[x].label).join(" and ");
  }

  // Going straight to a page (scrubber, contents, a link): the current page stays
  // until the new one has at least its stand-in, so a slow line never shows a gap.
  // While the scrubber is dragged, only the last position asked for is shown.
  let jumpTo = null;
  async function jump(k) {
    if (turn || k < 0 || k >= count()) return;
    jumpTo = k;
    if (k === pos) return;
    if (Math.abs(k - pos) > 2) stopReadAhead(); // nothing queued near here is of use there
    const image = (s) => { const im = new Image(); im.src = s; return im; };
    await decoded(view(k).filter((pi) => pi != null).map((pi) => image(low(pi))), 1500);
    if (jumpTo !== k || turn || k === pos) return;
    pos = k;
    render();
  }

  /* ---------- turning a leaf ---------- */

  const easeInOut = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
  const easeOut = (k) => 1 - Math.pow(1 - k, 3);
  const canGo = (dir) => pos + dir >= 0 && pos + dir < count();

  // A turn happens in two steps. planTurn() loads and decodes every image the turn
  // needs while nothing on screen changes; commitTurn() then swaps them all in at
  // once. Showing the leaf before its pictures were ready let the next page flash
  // through it for a frame on slower devices.
  function planTurn(dir) {
    const t = { dir, from: pos, to: pos + dir, p: 0, speed: 1, live: false, cam0: camOf(pos), cam1: camOf(pos + dir) };
    const A = view(t.from), B = view(t.to);
    if (A === B) {
      // One page at a time, left page to right page of the same spread (or back):
      // no leaf to turn, the book just slides across under the window.
      t.slide = true;
      t.ready = t.swapping = Promise.resolve();
      return t;
    }
    // A leaf turns over. One page at a time, the book slides as it turns, so the
    // window follows the leaf onto the left page, or back off it.
    if (dir > 0) { t.l = A[0]; t.r = B[1]; t.front = A[1]; t.back = B[0]; t.revealed = castR; t.covered = castL; }
    else { t.l = B[0]; t.r = A[1]; t.front = B[1]; t.back = A[0]; t.revealed = castL; t.covered = castR; }
    setFace(front, t.front); // the leaf is hidden, so this shows nothing yet
    setFace(back, t.back);
    // Ready when every page in the turn has at least its stand-in, and the sharp
    // copies have arrived or had a moment to: on a slow line the turn goes ahead
    // with soft pages that sharpen as they land, rather than keeping you waiting.
    const image = (s) => { const im = new Image(); im.src = s; return im; };
    const all = [t.front, t.back, t.l, t.r].filter((pi) => pi != null);
    const under = [t.l, t.r].filter((pi) => pi != null).map((pi) => image(src(pi)));
    t.ready = Promise.all([decoded(all.map((pi) => image(low(pi))), 5000), decoded([$("img", front), $("img", back), ...under], 450)]);
    return t;
  }

  const frames = (n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });

  function commitTurn(t) {
    if (t.slide) {
      body.classList.add("turning");
      loupe.hidden = true;
      t.live = t.swapped = true;
      setProgress(t, t.p);
      if (!t.dragging) showText(t.to);
      return;
    }
    // The leaf goes up first, showing the same page as the one beneath it. Only once
    // it has had a couple of frames to be drawn are the pages underneath changed,
    // so a leaf that is slow to appear never leaves a gap where a page should be.
    t.swapping = frames(2).then(() => {
      if (t.ended) return;
      setImg(slotL, t.l);
      setImg(slotR, t.r);
      maskTo(castL, slotL);
      maskTo(castR, slotR);
      t.swapped = true;
      setProgress(t, t.p);
      // The typed copy fades over to the new page as the leaf lifts. Only a full
      // turn does this; a drag that might be let go waits to see where it lands.
      if (!t.dragging) showText(t.to);
    });

    t.opening = (t.dir > 0 && atStart(t.from)) || (t.dir < 0 && atStart(t.to)) ? opening : null;
    t.closing = (t.dir < 0 && atEnd(t.from)) || (t.dir > 0 && atEnd(t.to)) ? closing : null;
    if (atStart(t.to)) body.classList.add("at-start");
    if (atEnd(t.to)) body.classList.add("at-end");
    body.classList.add("turning");
    loupe.hidden = true; // back over the new page once it has landed (render)
    t.live = true;
    setProgress(t, t.p);
    leaf.hidden = false;
  }

  function setProgress(t, want) {
    t.p = want;
    if (!t.live) return;
    const p = t.swapped ? want : 0; // the leaf stays flat until the pages beneath are swapped
    setCam(t.cam0 + (t.cam1 - t.cam0) * p);
    if (t.slide) return;
    const angle = t.dir > 0 ? -180 * p : -180 * (1 - p);
    leaf.style.transform = `rotateY(${angle.toFixed(2)}deg)`;
    leaf.style.setProperty("--lift", Math.sin((Math.PI * -angle) / 180).toFixed(3));
    // The leaf's shadow on the pages waits until they carry the right masks.
    t.revealed.style.opacity = t.swapped ? (0.85 * (1 - p)).toFixed(3) : "0";
    if (t.covered) t.covered.style.opacity = (0.6 * p).toFixed(3);
    // The title fades as the cover comes over it, and returns as the cover leaves.
    const leaving = (el, from) => el && el.style.setProperty("--fade", (from ? Math.max(0, 1 - 2.2 * p) : Math.max(0, 2.2 * p - 1.2)).toFixed(3));
    leaving(t.opening, t.dir > 0);
    leaving(t.closing, t.dir < 0);
  }

  async function endTurn(t, done) {
    t.ended = true;
    if (done) pos = t.to;
    // Keep the leaf in place until the page it lands on has been drawn beneath it.
    const [l, r] = view();
    setImg(slotL, l);
    setImg(slotR, r);
    await decoded([slotL, slotR]);
    await frames(2);
    body.classList.remove("turning");
    turn = null;
    render();
  }

  function animate(t, target, ease) {
    return new Promise((resolve) => {
      const p0 = t.p, dist = Math.abs(target - p0);
      if (dist < 0.001) return resolve();
      const base = (t.slide ? 520 : mode === "spread" ? 950 : 820) * Math.max(0.35, dist);
      let k = 0, last = performance.now();
      const step = (now) => {
        k = Math.min(1, k + ((now - last) * t.speed) / base);
        last = now;
        setProgress(t, p0 + (target - p0) * ease(k));
        if (k < 1) requestAnimationFrame(step); else resolve();
      };
      requestAnimationFrame(step);
    });
  }

  async function go(dir) {
    if (turn) {
      if (!turn.dragging) { queued = dir; turn.speed = 2.8; }
      return;
    }
    if (!canGo(dir)) return;
    if (typingFull()) {
      // The journal is out of sight: just bring the next page's text in (showText
      // fades it), passing over left-hand pages with nothing written on them.
      let k = pos + dir;
      while (mode === "single" && k + dir >= 0 && k + dir < count() && stops[k].side === 0 && !pages[anchor(k)].text) k += dir;
      pos = k;
      render();
      return;
    }
    if (reduced.matches) {
      // No turning leaf: wait for the next pages to be ready, then fade them in.
      turn = { dragging: false };
      const next = view(pos + dir).filter((pi) => pi != null);
      const image = (s) => { const im = new Image(); im.src = s; return im; };
      await Promise.all([decoded(next.map((pi) => image(low(pi))), 5000), decoded(next.map((pi) => image(src(pi))), 350)]);
      turn = null;
      pos += dir;
      render();
      book.animate?.([{ opacity: 0.6 }, { opacity: 1 }], { duration: 160, easing: "ease-out" });
      return;
    }
    if (mode === "single" && atStart() && dir > 0) { body.classList.remove("at-start"); layout(); }
    const t = (turn = planTurn(dir));
    await t.ready;
    commitTurn(t);
    await t.swapping;
    await animate(t, 1, easeInOut);
    await endTurn(t, true);
    if (queued) { const d = queued; queued = 0; go(d); }
  }

  /* ---------- pointer: tap to turn, drag to turn by hand ---------- */

  let drag = null;

  book.addEventListener("pointerdown", (e) => {
    // While magnifying, a click still turns the page (at the centre of the lens, which
    // is the pointer), but dragging does nothing: the lens follows the pointer instead.
    if (e.button !== 0 || e.target.closest("button, a")) return;
    drag = { id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(), lx: e.clientX, lt: performance.now(), vx: 0, t: null };
  });

  addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0, now = performance.now();
    drag.vx = (e.clientX - drag.lx) / Math.max(1, now - drag.lt);
    drag.lx = e.clientX;
    drag.lt = now;
    if (!drag.t) {
      if (Math.abs(dx) < 10 || Math.abs(dx) < Math.abs(dy) * 1.2 || turn || reduced.matches || magnifying) return;
      const dir = dx < 0 ? 1 : -1;
      if (!canGo(dir)) return;
      try { book.setPointerCapture(drag.id); } catch { /* the pointer may already be gone */ }
      if (mode === "single" && atStart() && dir > 0) { body.classList.remove("at-start"); layout(); }
      const t = (drag.t = turn = planTurn(dir));
      t.dragging = true;
      t.ready.then(() => commitTurn(t)); // follows the finger from then on
    }
    const span = mode === "spread" ? pw * 1.5 : drag.t.slide ? pw : pw * 0.9; // a slide follows the finger exactly
    setProgress(drag.t, Math.max(0, Math.min(1, (-dx * drag.t.dir) / span)));
  });

  async function release(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    if (!d.t) {
      const moved = Math.hypot(e.clientX - d.x0, e.clientY - d.y0);
      if (e.type === "pointerup" && moved < 10 && performance.now() - d.t0 < 700) tap(e.clientX);
      return;
    }
    const t = d.t;
    t.dragging = false;
    await t.ready;
    await t.swapping;
    const flick = -d.vx * t.dir > 0.4 && t.p > 0.04;
    const done = t.p > 0.4 || flick;
    await animate(t, done ? 1 : 0, easeOut);
    await endTurn(t, done);
    if (queued) { const q = queued; queued = 0; go(q); }
  }
  addEventListener("pointerup", release);
  addEventListener("pointercancel", release);

  function tap(x) {
    if (atStart()) return go(1);
    if (mode === "spread") {
      const r = book.getBoundingClientRect();
      return go(x < r.left + r.width / 2 ? -1 : 1);
    }
    // One page at a time: the window is the page width in the middle of the stage.
    const st = stage.getBoundingClientRect();
    go(x < st.left + st.width / 2 - pw / 2 + pw * 0.33 ? -1 : 1);
  }

  /* ---------- keyboard ---------- */

  addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || contents.open || !J) return;
    const onScrub = e.target === scrub;
    const free = e.target === document.body || onScrub || e.target === stage;
    switch (e.key) {
      case "ArrowRight": case "PageDown":
        if (!free && e.target.tagName === "INPUT") return;
        e.preventDefault(); go(1); break;
      case "ArrowLeft": case "PageUp":
        if (!free && e.target.tagName === "INPUT") return;
        e.preventDefault(); go(-1); break;
      case " ":
        if (e.target !== document.body) return;
        e.preventDefault(); go(e.shiftKey ? -1 : 1); break;
      case "Home": if (free) { e.preventDefault(); jump(0); } break;
      case "End": if (free) { e.preventDefault(); jump(count() - 1); } break;
      case "m": case "M": if (fine.matches) setMagnify(!magnifying); break;
      case "Escape":
        if (magnifying) setMagnify(false);
        else if (!textPanel.hidden) setText(false);
        break;
    }
  });

  /* ---------- the magnifier ---------- */

  function setMagnify(on) {
    magnifying = on && fine.matches;
    body.classList.toggle("magnifying", magnifying);
    btn.magnify.setAttribute("aria-pressed", String(magnifying));
    if (!magnifying) loupe.hidden = true;
  }

  let loupeSrc = "", pointer = null;
  function updateLoupe() {
    if (!magnifying || turn || !pointer) { loupe.hidden = true; return; }
    const { x, y } = pointer;
    let hit = null;
    for (const img of [slotL, slotR]) {
      if (!img.getAttribute("src")) continue;
      const r = img.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) hit = { img, r };
    }
    if (!hit) { loupe.hidden = true; return; }
    const pi = hit.img === slotL ? view()[0] : view()[1];
    const s = src(pi, J.heights[J.heights.length - 1]);
    if (s !== loupeSrc) { loupe.style.backgroundImage = `url("${s}")`; loupeSrc = s; }
    const u = x - hit.r.left, v = y - hit.r.top;
    loupe.hidden = false;
    loupe.style.transform = `translate(${x}px, ${y}px)`;
    loupe.style.backgroundSize = `${hit.r.width * ZOOM}px ${hit.r.height * ZOOM}px`;
    loupe.style.backgroundPosition = `${LOUPE - u * ZOOM}px ${LOUPE - v * ZOOM}px`;
  }
  stage.addEventListener("pointermove", (e) => { pointer = { x: e.clientX, y: e.clientY }; updateLoupe(); });
  stage.addEventListener("pointerleave", () => { pointer = null; loupe.hidden = true; });

  /* ---------- typed text ---------- */

  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const inline = (s) => esc(s)
    .replace(/~~(.+?)~~/g, "<s>$1</s>")
    .replace(/\[illegible\]/g, '<span class="unsure">[illegible]</span>')
    .replace(/\[([^\]]+?)\?\]/g, '<span class="unsure" title="Best guess">$1</span>');

  function textHTML(pi) {
    const p = pages[pi];
    let html = `<h3>${esc(p.label)}</h3>`;
    if (!p.text) return html + '<p class="empty">Nothing written on this page.</p>';
    if (p.lines && p.lines.length) {
      // One typed line per handwritten line. A crossing-out can run over a line
      // break, so carry it into the next line.
      let striking = false;
      for (const raw of p.lines) {
        const indent = raw.startsWith("\t");
        let line = raw.replace(/^\t/, "").trim();
        if (striking) line = "~~" + line;
        if ((line.match(/~~/g) || []).length % 2) { line += "~~"; striking = !striking; }
        const date = p.dates.includes(line);
        html += `<p class="ln${indent ? " in" : ""}${date ? " date" : ""}">${inline(line)}</p>`;
      }
      return html;
    }
    for (const para of p.text.split(/\n\s*\n/)) {
      let run = [];
      const flush = () => { if (run.length) html += `<p>${inline(run.join(" "))}</p>`; run = []; };
      for (const line of para.split("\n").map((s) => s.trim()).filter(Boolean)) {
        if (p.dates.includes(line)) { flush(); html += `<p class="date">${esc(line)}</p>`; }
        else run.push(line);
      }
      flush();
    }
    return html;
  }

  // The typed copy on screen, so a page is only set (and faded in) when it changes.
  let typedKey = "";

  // k: the view to show, which during a turn is the one being turned to.
  // fresh: draw it at once, without fading (opening the panel, changing layout).
  function showText(k = pos, fresh = false) {
    // A spread's typed copy: the pages with writing, and a numbered right-hand page
    // even if blank. Not the covers' blank insides.
    const shown = mode === "single" ? shownPages(k) : view(k).filter((pi) => pi != null && (pages[pi].text || (pages[pi].side === "R" && pages[pi].n != null)));
    if (k === pos) srText.textContent = shown.map((pi) => `${pages[pi].label}. ${pages[pi].text.replace(/~~.+?~~|\[|\]|\?/g, "")}`).join(" ");
    if (textPanel.hidden) { typedKey = ""; return; }
    const key = mode + shown.join();
    if (key === typedKey && !fresh) return;
    const fade = !!typedKey && !fresh;
    typedKey = key;
    sizeSheet();
    const html = shown.length ? shown.map(textHTML).join("") : '<p class="empty">Nothing written on these pages.</p>';
    // A plain copy for screen readers, and the typewritten one for the eye, sized
    // for its longest line before it is drawn, so it never changes size on screen.
    textBody.innerHTML = `<div class="sr-only">${html}</div><div class="typed" aria-hidden="true">${html}</div>`;
    const typed = $(".typed", textBody);
    typewrite(typed, shown.map((pi) => pages[pi].id).join());
    fit(typed);
    textBody.scrollTop = 0;
    if (fade) typed.animate?.([{ opacity: 0 }, { opacity: 1 }], { duration: reduced.matches ? 140 : 320, easing: "ease-out" });
  }

  /* ---------- the typewriter ---------- */

  function rng(seed) { // mulberry32
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.codePointAt(0), 16777619); return h >>> 0; };

  // Each key on a manual typewriter has its own small, consistent faults: a slug
  // that sits a little high, one that prints tilted, one that never quite inks.
  const slugs = new Map();
  function slug(ch) {
    if (!slugs.has(ch)) {
      const r = rng(hash("slug" + ch));
      const punct = /[.,;:'"!?\-]/.test(ch);
      slugs.set(ch, {
        y: (r() < 0.35 ? (r() - 0.5) * 1.6 : 0) + (/[A-Z]/.test(ch) ? -0.45 : 0), // capitals ride high on the shift
        r: r() < 0.3 ? (r() - 0.5) * 2.6 : 0,
        x: (r() - 0.5) * 0.7,
        ink: punct ? 1 : 0.8 + r() * 0.2,
      });
    }
    return slugs.get(ch);
  }

  // How worn the machine is. 1 was the first version; the owner asked for half that.
  const WEAR = 0.5;

  // And each strike differs: how hard the key was hit, a hair of play in the carriage.
  function typewrite(root, seedText) {
    const r = rng(hash(seedText));
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const frag = document.createDocumentFragment();
      for (const part of node.data.split(/(\s+)/)) {
        if (!part) continue;
        if (/^\s+$/.test(part)) { frag.append(part); continue; }
        const word = document.createElement("span");
        word.className = "w";
        for (const ch of part) {
          const s = slug(ch), k = document.createElement("span");
          const roll = r();
          let ink = s.ink + (r() - 0.5) * 0.3;
          if (roll < 0.06 * WEAR) ink = 0.48 + r() * 0.15; // a light touch
          else if (roll > 1 - 0.05 * WEAR) { ink = 1; k.className = "k ghost"; } // a hard one that bounced
          ink = 1 - (1 - Math.min(1, Math.max(0.42, ink))) * WEAR;
          const v = (n) => (n * WEAR).toFixed(2);
          k.className ||= "k";
          k.textContent = ch;
          k.style.cssText = `--i:${ink.toFixed(2)};`
            + `--x:${v(s.x + (r() - 0.5) * 0.5)}px;--y:${v(s.y + (r() - 0.5) * 0.6)}px;`
            + `--r:${v(s.r + (r() - 0.5) * 0.9)}deg;--sx:${v((r() - 0.5) * 1.2)}px;--sy:${v((r() - 0.5) * 1.2)}px`;
          word.append(k);
        }
        frag.append(word);
      }
      node.replaceWith(frag);
    }
  }

  // Size the type so the longest handwritten line fits on one typed line, within
  // reason; a line that still doesn't fit wraps with a hanging indent.
  const TYPE_MAX = 16, TYPE_MIN = 10.5;
  const SCROLLBAR = 16; // room kept for a scrollbar, so the size doesn't depend on one

  // Measure the typed lines at full size, unbroken, and scale the type so the widest
  // fits. This runs before the browser draws, so the page never shows at another size.
  function fit(typed, sheet = textBody) {
    if (!typed || !typed.querySelector(".ln")) return;
    typed.style.fontSize = TYPE_MAX + "px";
    typed.classList.add("measuring");
    let widest = 0;
    for (const ln of typed.querySelectorAll(".ln")) widest = Math.max(widest, ln.getBoundingClientRect().width);
    typed.classList.remove("measuring");
    const cs = getComputedStyle(sheet);
    const room = sheet.offsetWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - SCROLLBAR;
    typed.style.fontSize = Math.max(TYPE_MIN, Math.min(TYPE_MAX, (TYPE_MAX * room) / widest)).toFixed(2) + "px";
  }

  // The sheet is as tall as the tallest page needs at this width (or the space there
  // is, if less), so it keeps one size from page to page and sits centred, instead of
  // running the full height of a tall screen with blank paper below the text.
  let sheetFor = "";
  function sizeSheet() {
    if (textPanel.hidden) return;
    const width = textBody.offsetWidth;
    const key = `${width}|${typingFull()}`;
    if (!width || key === sheetFor) return;
    sheetFor = key;
    const probe = document.createElement("div");
    probe.className = "sheet-body";
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = `position:absolute;left:-10000px;top:0;visibility:hidden;width:${width}px;height:auto;overflow:visible;flex:none`;
    textBody.parentElement.append(probe);
    let tallest = 0;
    pages.forEach((p, pi) => {
      if (!p.text) return;
      probe.innerHTML = `<div class="typed">${textHTML(pi)}</div>`;
      fit(probe.firstChild, probe);
      tallest = Math.max(tallest, probe.offsetHeight);
    });
    probe.remove();
    if (tallest) textPanel.style.setProperty("--sheet-h", `${Math.ceil(tallest)}px`);
  }

  // The sheet changed width (window resized), or the typewriter font just arrived:
  // refit what's on it, without a fade.
  function fitLines() { sizeSheet(); fit($(".typed", textBody)); }
  document.fonts?.load(`${TYPE_MAX}px "Special Elite"`).then(() => { sheetFor = ""; fitLines(); });

  // Below this width the typed copy and the journal can't sit side by side, so the
  // typed sheet replaces the journal instead.
  const DOCK_MIN = 1100;
  const typingFull = () => body.classList.contains("typing-full");

  function textMode() {
    body.classList.toggle("typing-full", !textPanel.hidden && innerWidth < DOCK_MIN);
  }

  function setText(open) {
    textPanel.hidden = !open;
    btn.text.setAttribute("aria-pressed", String(open));
    textMode();
    showText(pos, true);
    layout();
    if (open && typingFull()) textBody.focus({ preventScroll: true });
  }

  // In the typed view a sideways swipe turns the page; up and down still scroll.
  let swipe = null;
  textBody.addEventListener("pointerdown", (e) => {
    if (typingFull() && e.pointerType !== "mouse") swipe = { id: e.pointerId, x: e.clientX, y: e.clientY, t: performance.now() };
  });
  textBody.addEventListener("pointerup", (e) => {
    if (!swipe || e.pointerId !== swipe.id) return;
    const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y, quick = performance.now() - swipe.t < 800;
    swipe = null;
    if (quick && Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? 1 : -1);
  });
  textBody.addEventListener("pointercancel", () => { swipe = null; });

  // A click on the right half of the typed page goes forward, on the left half back,
  // unless it was the end of selecting some text, or the second click of a double.
  textBody.addEventListener("click", (e) => {
    if (e.button !== 0 || e.detail > 1 || !window.getSelection().isCollapsed) return;
    const r = textBody.getBoundingClientRect();
    go(e.clientX < r.left + r.width / 2 ? -1 : 1);
  });

  /* ---------- contents ---------- */

  function words(text, date, dates) {
    let t = text;
    const i = t.indexOf(date);
    if (i >= 0) t = t.slice(i + date.length);
    for (const other of dates) {
      const j = other === date ? -1 : t.indexOf("\n" + other);
      if (j >= 0) t = t.slice(0, j);
    }
    t = t.replace(/~~.*?~~/g, "").replace(/\[illegible\]/g, "…").replace(/\[([^\]]*?)\??\]/g, "$1").replace(/\s+/g, " ").trim();
    const w = t.split(" ");
    return w.slice(0, 16).join(" ") + (w.length > 16 ? "…" : "");
  }

  function buildEntries() {
    entries = [];
    pages.forEach((p, pi) => p.dates.forEach((date) => entries.push({ date, pi, words: words(p.text, date, p.dates), place: placeAt(pi) })));
    let place = null, html = "";
    entries.forEach((en, i) => {
      if (en.place && en.place !== place) { html += `<li class="place" aria-hidden="true">${esc(en.place)}</li>`; place = en.place; }
      const p = pages[en.pi];
      html += `<li><a href="#${esc(p.slug)}" data-i="${i}"><span class="d">${esc(en.date)}</span>`
        + `<span class="w">${esc(en.words)}</span><span class="n">${p.n != null ? p.n : ""}</span></a></li>`;
    });
    entriesEl.innerHTML = html || '<li class="place">No dated entries yet.</li>';
  }

  function markEntry() {
    const pi = anchor();
    let cur = -1;
    entries.forEach((en, i) => { if (en.pi <= pi) cur = i; });
    entriesEl.querySelectorAll("a").forEach((a) => a.setAttribute("aria-current", String(+a.dataset.i === cur)));
  }

  /* ---------- the rail ---------- */

  function buildTicks() {
    const n = count() - 1;
    if (n <= 0) { ticks.innerHTML = ""; return; }
    const arrive = new Set((J.route || []).map((s) => s.page));
    const marks = new Map();
    for (const en of entries) {
      const k = posOf(en.pi);
      const num = pages[en.pi].n;
      marks.set(k, marks.get(k) || (num != null && arrive.has(num) && placeAt(en.pi) !== placeAt(en.pi - 1)));
    }
    let html = "";
    for (const [k, big] of marks) html += `<i class="${big ? "arrive" : ""}" style="left:calc(3px + ${k / n} * (100% - 6px))"></i>`;
    ticks.innerHTML = html;
  }

  function scrubIndexAt(x) {
    const r = scrub.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (x - r.left - 3) / (r.width - 6)));
    return { k: Math.round(f * (count() - 1)), left: 3 + f * (r.width - 6) };
  }

  function showPeek(k, left) {
    const pi = anchor(k);
    $("img", peek).src = src(pi, J.heights[0]);
    const date = dateAt(pi);
    $("span", peek).textContent = pages[pi].label + (date && !atStart(k) ? `, ${date}` : "");
    peek.style.left = `${left}px`;
    peek.hidden = false;
  }

  scrub.addEventListener("pointermove", (e) => { if (fine.matches) { const h = scrubIndexAt(e.clientX); showPeek(h.k, h.left); } });
  scrub.addEventListener("pointerleave", () => { peek.hidden = true; });
  scrub.addEventListener("input", () => {
    const k = +scrub.value;
    const n = Math.max(1, count() - 1);
    showPeek(k, 3 + (k / n) * (scrub.getBoundingClientRect().width - 6));
    jump(k);
  });
  scrub.addEventListener("change", () => setTimeout(() => { if (!scrub.matches(":hover")) peek.hidden = true; }, 600));

  /* ---------- layout ---------- */

  function layout() {
    if (!J) return;
    const st = stage.getBoundingClientRect();
    const a = J.page.width / J.page.height;
    const short = innerHeight < 560;
    const padX = st.width < 600 ? 12 : 48;
    const folio = short ? 6 : 42;
    const availW = st.width - 2 * padX, availH = st.height - 10 - folio;
    const hSpread = Math.min(availH, availW / (2 * a));
    const hSingle = Math.min(availH, availW / a);
    // Two pages unless that would shrink them much below one page alone. No minimum
    // height: on a short screen (a phone on its side) one page is no taller than two.
    const next = hSpread >= 0.72 * hSingle ? "spread" : "single";
    const switched = next !== mode;
    if (switched) {
      const pi = anchor();
      mode = next;
      body.classList.toggle("single", mode === "single");
      pos = posOf(pi);
      buildTicks();
    }
    let h = mode === "spread" ? hSpread : hSingle;
    let below = folio;
    // One page at a time, the opening title and the closing note go below the cover:
    // make room for them there.
    if (mode === "single" && (body.classList.contains("at-start") || body.classList.contains("at-end"))) {
      const room = Math.min(st.height * 0.34, 210);
      h = Math.min(h, availH - room);
      below = room;
    }
    ph = Math.max(120, Math.floor(h));
    pw = Math.floor(ph * a);
    const root = document.documentElement.style;
    root.setProperty("--pw", pw + "px");
    root.setProperty("--ph", ph + "px");
    root.setProperty("--half", Math.floor(st.width / 2) + "px"); // binding to the stage's edge
    book.style.marginBottom = `${below}px`; // only the bottom: one page at a time sets the left (CSS)

    const need = ph * (devicePixelRatio || 1);
    const h2 = J.heights.find((x) => x >= need * 0.92) ?? J.heights[J.heights.length - 1];
    const mid = J.heights.filter((x) => x > J.heights[0]);
    const nextH = Math.max(h2, mid[0] ?? h2);
    if (switched || nextH !== imgH || !slotR.getAttribute("src")) { imgH = nextH; if (!turn) render(); }
    else if (!turn) { scrub.max = String(count() - 1); }
  }

  /* ---------- wiring ---------- */

  btn.prev.addEventListener("click", () => go(-1));
  btn.next.addEventListener("click", () => go(1));
  btn.open.addEventListener("click", () => go(1));
  btn.restart.addEventListener("click", () => { const k = posOf(pages.findIndex((p) => p.n != null)); jump(Math.max(0, k)); });
  btn.text.addEventListener("click", () => setText(textPanel.hidden));
  btn.magnify.addEventListener("click", () => setMagnify(!magnifying));
  btn.contents.addEventListener("click", () => {
    markEntry();
    contents.showModal();
    const cur = $('a[aria-current="true"]', entriesEl);
    if (cur) { cur.scrollIntoView({ block: "center" }); cur.focus(); }
  });
  btn.contentsClose.addEventListener("click", () => contents.close());
  contents.addEventListener("click", (e) => {
    if (e.target === contents) contents.close();
    if (e.target.closest("a")) contents.close();
  });
  fine.addEventListener?.("change", () => { btn.magnify.hidden = !fine.matches; if (!fine.matches) setMagnify(false); });

  function fromHash() {
    const slug = decodeURIComponent(location.hash.slice(1));
    const pi = pages.findIndex((p) => p.slug === slug);
    return pi < 0 ? null : posOf(pi);
  }
  addEventListener("hashchange", () => { const k = fromHash(); if (k != null) jump(k); });

  new ResizeObserver(() => layout()).observe(stage);
  addEventListener("resize", () => {
    const was = typingFull();
    textMode();
    if (was !== typingFull()) showText(pos, true);
    layout();
    fitLines();
  });

  fetch("journal.json", { cache: "no-cache" }) // always check for a newer build
    .then((r) => r.json())
    .then((data) => {
      J = data;
      pages = J.pages;
      spreads = J.spreads;
      spreadOf = [];
      spreads.forEach((row, k) => row.forEach((pi) => { if (pi != null) spreadOf[pi] = k; }));
      // One page at a time stops on every right-hand page, and on a left-hand page
      // only when something is written on it (page 97); a blank one is passed over,
      // its leaf turning away out of the window. (To stop on every left page too,
      // drop the writing test.)
      stops = [];
      spreads.forEach((row, s) => row.forEach((pi, side) => {
        if (pi != null && (side === 1 || pages[pi].writing)) stops.push({ s, side });
      }));
      btn.magnify.hidden = !fine.matches;
      buildEntries();
      const st = stage.getBoundingClientRect();
      const a = J.page.width / J.page.height;
      mode = Math.min(st.height, st.width / (2 * a)) >= 0.72 * Math.min(st.height, st.width / a) ? "spread" : "single";
      body.classList.toggle("single", mode === "single");
      pos = fromHash() ?? 0;
      body.classList.toggle("at-start", atStart());
      buildTicks();
      layout();
      // layout() may have settled on the other layout; open on the page the link names.
      pos = fromHash() ?? pos;
      body.classList.toggle("at-start", atStart());
      render();
    })
    .catch(() => { where.textContent = "The journal could not be loaded. Reload the page to try again."; body.classList.remove("at-start"); });
})();
