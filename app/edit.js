/* MiniMax H3 Vision: the Edit tab. A simple one-track editor for the videos made in the Video tab.

   It uses app.js's globals (state, OPT, $, esc, api, say, fileUrl, clipUrl, kindOf, render).
   The edit is one track of pieces, each a part of a finished video: {id, clip, in, out} in seconds,
   played one after another with no gaps. It is saved on the server (PUT /mmflow/api/edit) a moment
   after each change. The server makes each clip's filmstrip and waveform (GET strips/<id>) and the
   export (POST export, progress as mmvision_export events). */

const EDIT = (() => {
  const FPS = 24, MIN_PIECE = 0.5, X0 = 12;
  const RATIOS = { "16:9": [16, 9], "9:16": [9, 16], "1:1": [1, 1] };
  const E = {
    name: "Untitled edit", pieces: [], ratio: "16:9", muted: false,
    selected: null, playhead: 0, playing: false, pxPerSec: 40,
    q: "", filter: "all", hideAdded: false, perRow: 3,
    past: [], future: [],
    preview: null, previewTime: 0,   // a video shown on its own in the player (from the media panel), or null for the edit
    strips: {},                      // clip id -> filmstrip info, or "loading"
    built: false, visible: false, exportJob: null,
  };
  let pid = 0;
  const ed = (id) => document.getElementById("ed" + id);

  /* ---------------- the videos (from state.clips) ---------------- */
  const h3Frames = (seconds) => 5 + 17 * Math.max(1, Math.round((seconds * FPS - 5) / 17));
  const media = () => state.clips.filter((c) => kindOf(c) === "video" && c.status === "done" && c.file);
  const mediaById = (id) => media().find((c) => c.id === id);
  function durOf(c) {
    const s = E.strips[c.id];
    if (s && s.duration) return s.duration;
    return c.len ? h3Frames(c.len) / FPS : 5;
  }
  const shapeOf = (c) => (c.width && c.height ? (Math.abs(c.width / c.height - 1) < 0.05 ? "1:1" : c.width < c.height ? "9:16" : "16:9") : c.aspect === "9:16" ? "9:16" : "16:9");

  /* ---------------- the edit ---------------- */
  const len = (p) => p.out - p.in;
  const total = () => E.pieces.reduce((s, p) => s + len(p), 0);
  const startOf = (i) => E.pieces.slice(0, i).reduce((s, p) => s + len(p), 0);
  const newPiece = (clip, a, b) => ({ id: "p" + Date.now().toString(36) + (pid++), clip, in: a, out: b });
  function at(t) {
    let s = 0;
    for (let i = 0; i < E.pieces.length; i++) {
      const p = E.pieces[i];
      if (t < s + len(p) - 1e-6 || i === E.pieces.length - 1) return { i, p, local: p.in + Math.min(len(p), Math.max(0, t - s)), start: s };
      s += len(p);
    }
    return null;
  }
  function remember() { E.past.push(JSON.stringify(E.pieces)); if (E.past.length > 100) E.past.shift(); E.future = []; }
  function undo() { if (!E.past.length) return; E.future.push(JSON.stringify(E.pieces)); E.pieces = JSON.parse(E.past.pop()); E.selected = null; changed(); }
  function redo() { if (!E.future.length) return; E.past.push(JSON.stringify(E.pieces)); E.pieces = JSON.parse(E.future.pop()); E.selected = null; changed(); }
  const clampHead = () => { E.playhead = Math.max(0, Math.min(E.playhead, total())); };
  function changed() { clampHead(); save(); draw(); }

  let saveTimer;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => api("PUT", "edit", { name: E.name, pieces: E.pieces, ratio: E.ratio, muted: E.muted }).catch(() => say("Couldn't save the edit.")), 500);
  }

  // Pieces of videos that were deleted are dropped.
  function prune() {
    const ids = new Set(media().map((c) => c.id));
    const before = E.pieces.length;
    E.pieces = E.pieces.filter((p) => ids.has(p.clip));
    if (E.pieces.length !== before) { clampHead(); save(); }
  }

  /* ---------------- formatting ---------------- */
  const two = (n) => String(n).padStart(2, "0");
  const fmt = (t) => { const f = Math.round(t * FPS), s = Math.floor(f / FPS); return two(Math.floor(s / 3600)) + ":" + two(Math.floor(s / 60) % 60) + ":" + two(s % 60) + ":" + two(f % FPS); };
  const clock = (t) => two(Math.floor(t / 60)) + ":" + two(Math.floor(t % 60));
  const short = (t) => (t >= 60 ? Math.floor(t / 60) + ":" + two(Math.floor(t % 60)) : t.toFixed(t < 10 ? 1 : 0) + " s");

  /* ---------------- building the tab ---------------- */
  const SVG = {
    search: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    plus: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M12 5v14M5 12h14"/></svg>',
    play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5v14l12-7z"/></svg>',
    pause: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
    full: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
    undo: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
    redo: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/></svg>',
    split: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v18"/><path d="M8 6H4v12h4M16 6h4v12h-4"/></svg>',
    del: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    fit: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h18M7 8l-4 4 4 4M17 8l4 4-4 4"/></svg>',
    zoomOut: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M20 20l-3.5-3.5"/></svg>',
    zoomIn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M11 8v6M20 20l-3.5-3.5"/></svg>',
    sound: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>',
    mute: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="m17 9 5 6M22 9l-5 6"/></svg>',
    caret: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m6 9 6 6 6-6"/></svg>',
    zip: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M12 12v6M9 15l3 3 3-3"/></svg>',
    open: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 16v-5M9.5 13.5 12 11l2.5 2.5"/></svg>',
    exp: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3v12M7 8l5-5 5 5M5 21h14"/></svg>',
  };

  function build() {
    if (E.built) return;
    E.built = true;
    $("editView").innerHTML = `
      <section class="ed-upper">
        <aside class="ed-media">
          <div class="ed-media-head">
            <span class="ed-title">Your videos</span>
            <label class="ed-search">${SVG.search}<input id="edQ" placeholder="Search prompts"></label>
            <select class="ed-sel" id="edFilter" aria-label="Show"><option value="all">All</option><option value="starred">Starred</option></select>
            <button class="ed-sel ed-toggle" id="edHide" aria-pressed="false">Hide added</button>
          </div>
          <div class="ed-media-sub"><span id="edCount"></span>
            <label class="ed-per-row">Clips per row <input type="range" id="edPerRow" min="2" max="6" value="3"><b id="edPerRowN">3</b></label></div>
          <div class="ed-media-grid" id="edMedia"></div>
        </aside>
        <section class="ed-preview">
          <div class="ed-stage" id="edStage"><div class="ed-frame" id="edFrame">
            <video id="edVa" playsinline preload="auto"></video><video id="edVb" playsinline preload="auto" hidden></video>
            <div class="ed-empty-frame" id="edEmptyFrame">Add videos to the timeline</div>
          </div></div>
          <div class="ed-pbar">
            <span class="ed-tc" id="edTc"></span>
            <button class="ed-play" id="edPlay" aria-label="Play"></button>
            <div class="ed-pright">
              <select class="ed-sel" id="edRatio" aria-label="Frame" title="The shape of the edit. Videos of another shape get black bars."><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option></select>
              <button class="ed-icon" id="edFull" title="Full screen" aria-label="Full screen">${SVG.full}</button>
            </div>
          </div>
        </section>
      </section>
      <section class="ed-timeline" id="edTimeline">
        <div class="ed-split" id="edSplitV" title="Drag to resize"></div>
        <div class="ed-tools">
          <button class="ed-icon" id="edUndo" title="Undo (⌘Z)">${SVG.undo}</button>
          <button class="ed-icon" id="edRedo" title="Redo (⇧⌘Z)">${SVG.redo}</button>
          <span class="ed-gap"></span>
          <button class="ed-icon" id="edCut" title="Split at the playhead (S)">${SVG.split}</button>
          <button class="ed-icon" id="edDel" title="Delete the selected clip (⌫)">${SVG.del}</button>
          <span class="ed-grow"></span>
          <button class="ed-icon" id="edFit" title="Fit the edit in view">${SVG.fit}</button>
          <button class="ed-icon" id="edZoomOut" title="Zoom out">${SVG.zoomOut}</button>
          <input type="range" class="ed-zoom" id="edZoom" min="0" max="100" value="45" aria-label="Zoom">
          <button class="ed-icon" id="edZoomIn" title="Zoom in">${SVG.zoomIn}</button>
        </div>
        <div class="ed-body">
          <div class="ed-heads"><div class="ed-head"><button class="ed-icon" id="edMute"></button><span>Video</span></div></div>
          <div class="ed-lane" id="edLane"><div class="ed-lane-inner" id="edInner">
            <div class="ed-ruler" id="edRuler"></div>
            <div class="ed-track" id="edTrack"></div>
            <div class="ed-playhead" id="edHead"></div>
          </div></div>
        </div>
      </section>
      <div class="ed-scrim" id="edExport" hidden>
        <div class="ed-dialog" role="dialog" aria-label="Export">
          <div id="edAsk" hidden>
            <h2 id="edAskTitle"></h2>
            <div class="ed-summary" id="edAskText"></div>
            <div class="ed-row"><button class="ed-btn" id="edAskNo">Cancel</button><button class="ed-btn primary" id="edAskYes"></button></div>
          </div>
          <div id="edExForm">
            <h2>Export</h2>
            <div class="ed-field"><label for="edExName">File name</label><input type="text" id="edExName" spellcheck="false"></div>
            <div class="ed-field"><label for="edExSize">Size</label><select id="edExSize"></select></div>
            <label class="ed-check"><input type="checkbox" id="edExSound" checked> Include sound</label>
            <div class="ed-summary" id="edExSummary"></div>
            <div class="ed-row"><button class="ed-btn" data-edclose>Cancel</button><button class="ed-btn primary" id="edExGo">Export</button></div>
          </div>
          <div id="edExRun" hidden>
            <h2>Exporting…</h2>
            <div class="ed-bar"><i id="edExBar"></i></div>
            <div class="ed-summary" id="edExStep"></div>
            <div class="ed-row"><button class="ed-btn" id="edExCancel">Cancel</button></div>
          </div>
          <div id="edExDone" hidden>
            <h2>Exported</h2>
            <div class="ed-summary">Downloading now. It's also saved in ComfyUI's output folder:</div>
            <div class="ed-path" id="edExPath"></div>
            <div class="ed-row"><button class="ed-btn" data-edclose>Close</button><a class="ed-btn primary" id="edExDownload" download>Download again</a></div>
          </div>
        </div>
      </div>`;
    wire();
  }

  // The name and Export button live in the header, next to the queue.
  function header() {
    const box = $("editHead");
    box.hidden = !E.visible;
    if (!E.visible) return;
    if (!box.firstChild) {
      box.innerHTML = `<input class="ed-name" id="edName" aria-label="Name of the edit" spellcheck="false">
        <div class="ed-proj">
          <button class="ed-ghost" id="edProjBtn" aria-haspopup="menu" aria-expanded="false">Project ${SVG.caret}</button>
          <div class="ed-menu" id="edProjMenu" role="menu" hidden>
            <button role="menuitem" id="edProjSave">${SVG.zip}<span>Export project<small>The timeline, its videos and their reference images, as a .zip</small></span></button>
            <label role="menuitem">${SVG.open}<span>Import project<small>A project's .zip replaces this timeline. A single video's .zip joins the media panel</small></span>
              <input type="file" accept=".zip,application/zip" id="edProjOpen" hidden></label>
          </div>
        </div>
        <button class="ed-export" id="edExportBtn">${SVG.exp}Export</button>`;
      ed("Name").addEventListener("input", (e) => { E.name = e.target.value; save(); });
      ed("ExportBtn").addEventListener("click", openExport);
      ed("ProjBtn").addEventListener("click", (e) => { e.stopPropagation(); projectMenu(ed("ProjMenu").hidden); });
      ed("ProjSave").addEventListener("click", () => { projectMenu(false); exportProject(); });
      ed("ProjOpen").addEventListener("change", (e) => { projectMenu(false); const f = e.target.files[0]; e.target.value = ""; if (f) importProject(f); });
      document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".ed-proj")) projectMenu(false); });
    }
    if (document.activeElement !== ed("Name")) ed("Name").value = E.name;
    ed("ExportBtn").disabled = !E.pieces.length;
    ed("ProjSave").disabled = !E.pieces.length;
  }
  function projectMenu(open) {
    if (!ed("ProjMenu")) return;
    ed("ProjMenu").hidden = !open;
    ed("ProjBtn").setAttribute("aria-expanded", open);
  }

  /* ---------------- projects: the timeline and its videos as one zip ---------------- */
  function download(url, name) {
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  }
  async function exportProject() {
    if (!E.pieces.length) return;
    pause();
    clearTimeout(saveTimer);
    try { await api("PUT", "edit", { name: E.name, pieces: E.pieces, ratio: E.ratio, muted: E.muted }); } catch {}
    say("Saving the project…");
    try {
      const r = await api("POST", "edit/export-project", {});
      download(fileUrl(r.subfolder, r.file), r.file);
      say(`Project saved: ${r.pieces} clip${r.pieces === 1 ? "" : "s"}, ${r.clips} video${r.clips === 1 ? "" : "s"} and ${r.images || 0} reference image${r.images === 1 ? "" : "s"}, in ${r.subfolder}/${r.file}.`);
    } catch (error) { say(error.message); }
  }
  async function importProject(file) {
    pause();
    say("Opening " + file.name + "…");
    const form = new FormData();
    form.append("file", file, file.name);
    let r;
    try { r = await api("POST", "import-zip", form); } catch (error) { say(error.message); return; }
    // Its videos and reference pictures are added either way.
    importedZip(r);
    if (!r.edit) {
      drawMedia();
      say(`Opened ${file.name}: ` + zipSummary(r) + ". It's in the media panel.");
      return;
    }
    if (E.pieces.length && !(await ask("Use the timeline in " + file.name + "?", "It replaces the timeline you have now. Its videos are in the media panel either way.", "Replace timeline"))) {
      drawMedia(); drawTimeline();
      say(`Kept your timeline. From ${file.name}: ` + zipSummary(r) + ".");
      return;
    }
    E.name = r.edit.name; E.ratio = r.edit.ratio; E.muted = r.edit.muted;
    E.pieces = r.edit.pieces.map((p) => ({ ...p }));
    E.past = []; E.future = []; E.selected = null; E.preview = null; E.playhead = 0;
    ed("Ratio").value = E.ratio;
    vid("a").muted = vid("b").muted = E.muted;
    new Set(E.pieces.map((p) => p.clip)).forEach(loadStrip);
    sizeFrame(); draw(); save();
    say(`Opened ${file.name}: ${E.pieces.length} clip${E.pieces.length === 1 ? "" : "s"}; ` + zipSummary(r) + ".");
  }
  // A yes/no question in the edit's own dialog.
  function ask(title, text, yes) {
    return new Promise((done) => {
      ed("AskTitle").textContent = title; ed("AskText").textContent = text; ed("AskYes").textContent = yes;
      ed("ExForm").hidden = ed("ExRun").hidden = ed("ExDone").hidden = true;
      ed("Ask").hidden = false; ed("Export").hidden = false;
      const finish = (answer) => { ed("Ask").hidden = true; ed("Export").hidden = true; ed("AskYes").onclick = ed("AskNo").onclick = null; done(answer); };
      ed("AskYes").onclick = () => finish(true);
      ed("AskNo").onclick = () => finish(false);
      ed("AskYes").focus();
    });
  }

  /* ---------------- media panel ---------------- */
  function drawMedia() {
    const used = new Set(E.pieces.map((p) => p.clip));
    const all = media();
    let list = all.filter((c) => (c.prompt || "").toLowerCase().includes(E.q.toLowerCase()));
    if (E.filter === "starred") list = list.filter((c) => c.starred);
    if (E.hideAdded) list = list.filter((c) => !used.has(c.id));
    list.sort((a, b) => (b.created || 0) - (a.created || 0));
    const grid = ed("Media");
    grid.style.setProperty("--ed-cols", E.perRow);
    grid.innerHTML = list.length ? list.map((c) => `
      <div class="ed-mtile ${E.preview === c.id ? "on" : ""}" draggable="true" data-clip="${c.id}" title="${esc(c.prompt || "")}">
        <div class="ed-thumb"><video src="${clipUrl(c)}#t=0.5" muted playsinline preload="metadata"></video></div>
        ${used.has(c.id) ? '<span class="ed-added">Added</span>' : ""}
        <span class="ed-dur">${short(durOf(c))}</span>
        <button class="ed-add" data-edadd="${c.id}" title="Add at the playhead (between the clips nearest it)" aria-label="Add to the edit">${SVG.plus}</button>
        <div class="ed-mname">${esc(c.prompt || c.file)}</div>
      </div>`).join("")
      : `<div class="ed-media-empty">${!all.length ? "No videos yet. Make some in the Video tab."
        : E.hideAdded && !E.q && E.filter === "all" ? "Every video is in the edit." : "No videos match."}</div>`;
    ed("Count").textContent = `${list.length} of ${all.length} video${all.length === 1 ? "" : "s"}`;
    ed("Hide").textContent = E.hideAdded ? "Show added" : "Hide added";
    ed("Hide").setAttribute("aria-pressed", E.hideAdded);
    ed("Hide").title = E.hideAdded ? "Show the videos that are already in the edit" : "Hide the videos that are already in the edit";
  }

  // Pick a video in the media pool: it plays on its own in the player. null goes back to the edit.
  function pickMedia(id) {
    pause();
    E.preview = id;
    E.previewTime = 0;
    // The highlight changes in place: redrawing the tiles here would swallow a double-click.
    const grid = ed("Media");
    grid.querySelectorAll(".ed-mtile").forEach((t) => t.classList.toggle("on", t.dataset.clip === id));
    grid.querySelector(".ed-mtile.on")?.scrollIntoView({ block: "nearest" });
    showFrame();
    if (id) play();
  }
  // Arrow keys move through the media pool (up and down by a row); Enter adds the video; Esc lets go.
  function mediaKey(k) {
    const ids = [...ed("Media").querySelectorAll(".ed-mtile")].map((t) => t.dataset.clip);
    const at = ids.indexOf(E.preview);
    if (at < 0) return false;
    const step = { arrowleft: -1, arrowright: 1, arrowup: -E.perRow, arrowdown: E.perRow }[k];
    if (step) { const next = ids[Math.max(0, Math.min(ids.length - 1, at + step))]; if (next !== E.preview) pickMedia(next); return true; }
    if (k === "enter") { const id = E.preview; pickMedia(null); addClip(id, gapAtHead()); return true; }
    if (k === "escape") { pickMedia(null); return true; }
    return false;
  }

  function addClip(id, index) {
    const c = mediaById(id);
    if (!c) return;
    remember();
    const p = newPiece(c.id, 0, durOf(c));
    if (!E.strips[c.id]?.duration) p.toEnd = true;   // its length is a guess until the file's own length is known
    E.pieces.splice(index, 0, p);
    E.selected = p.id;
    E.preview = null;
    if (E.pieces.length === 1) { E.ratio = shapeOf(c); ed("Ratio").value = E.ratio; sizeFrame(); }
    E.playhead = startOf(index);
    loadStrip(c.id);
    changed();
    scrollToHead();
    showFrame();
  }

  /* ---------------- filmstrips and waveforms (from the server) ---------------- */
  function loadStrip(id) {
    if (E.strips[id]) return;
    E.strips[id] = "loading";
    api("GET", "strips/" + id).then((info) => {
      const img = new Image();
      img.onload = () => { info.img = img; E.strips[id] = info; fixLength(id, info.duration); if (E.visible) drawTimeline(); };
      img.onerror = () => { E.strips[id] = info; };
      img.src = info.sprite_url;
    }).catch(() => { E.strips[id] = { failed: true }; });
  }
  // The file's own length is the truth: pieces that reached past it are cut to fit.
  function fixLength(id, duration) {
    let moved = false;
    for (const p of E.pieces) {
      if (p.clip !== id) continue;
      if (p.toEnd) { delete p.toEnd; if (Math.abs(p.out - duration) > 1e-3) { p.out = duration; moved = true; } }
      if (p.out > duration + 1e-3) { p.out = duration; moved = true; }
      if (p.in > p.out - MIN_PIECE) { p.in = Math.max(0, p.out - MIN_PIECE); moved = true; }
    }
    if (moved) { clampHead(); save(); }
  }

  /* ---------------- preview: two video elements, so cuts don't stall ---------------- */
  let active = "a";
  const vid = (which) => ed(which === "a" ? "Va" : "Vb");
  const other = () => (active === "a" ? "b" : "a");
  function load(v, c) {
    const url = clipUrl(c);
    if (v.dataset.clip !== c.id) { v.dataset.clip = c.id; v.src = url; }
    v.muted = E.muted;
  }
  function seek(v, t) {
    const go = () => { if (Math.abs(v.currentTime - t) > 0.02) v.currentTime = t; };
    v.readyState >= 1 ? go() : v.addEventListener("loadedmetadata", go, { once: true });
  }
  function swapTo(which) {
    if (active === which) return;
    vid(active).pause();
    vid(active).hidden = true;
    active = which;
    vid(active).hidden = false;
  }
  // Show the frame at the playhead (paused), and get the next piece ready in the other video.
  function showFrame() {
    const empty = !E.preview && !E.pieces.length;
    ed("EmptyFrame").hidden = !empty;
    vid("a").style.visibility = vid("b").style.visibility = empty ? "hidden" : "";
    if (empty) { drawTc(); return; }
    if (E.preview) {
      const c = mediaById(E.preview), v = vid(active);
      load(v, c); seek(v, E.previewTime);
    } else {
      const hit = at(Math.min(E.playhead, total() - 1e-4)), c = mediaById(hit.p.clip), v = vid(active);
      load(v, c); seek(v, hit.local);
      prepareNext(hit.i);
    }
    drawTc();
  }
  function prepareNext(i) {
    const next = E.pieces[i + 1];
    if (!next) return;
    const v = vid(other());
    load(v, mediaById(next.clip)); seek(v, next.in);
  }
  let raf = 0;
  function play() {
    if (E.preview) {
      const v = vid(active), c = mediaById(E.preview);
      if (E.previewTime >= durOf(c) - 0.05) { E.previewTime = 0; seek(v, 0); }
      E.playing = true; v.play().catch(() => {});
    } else {
      if (!E.pieces.length) return;
      if (E.playhead >= total() - 0.02) E.playhead = 0;
      const hit = at(E.playhead), v = vid(active);
      load(v, mediaById(hit.p.clip)); seek(v, hit.local);
      E.playing = true; v.play().catch(() => {});
      prepareNext(hit.i);
    }
    cancelAnimationFrame(raf); raf = requestAnimationFrame(tick);
    drawPlay();
  }
  function pause() {
    E.playing = false;
    vid("a").pause(); vid("b").pause();
    cancelAnimationFrame(raf);
    drawPlay();
  }
  const toggle = () => (E.playing ? pause() : play());
  function tick() {
    if (!E.playing) return;
    const v = vid(active);
    if (E.preview) {
      E.previewTime = v.currentTime;
      if (v.ended) { pause(); }
    } else {
      const hit = at(Math.min(E.playhead, total() - 1e-4));
      if (!hit) { pause(); return; }
      if (v.dataset.clip !== hit.p.clip) { load(v, mediaById(hit.p.clip)); seek(v, hit.local); v.play().catch(() => {}); }
      E.playhead = hit.start + Math.max(0, v.currentTime - hit.p.in);
      if (v.currentTime >= hit.p.out - 1 / (FPS * 2) || v.ended) {
        const next = E.pieces[hit.i + 1];
        if (!next) { E.playhead = total(); pause(); placeHead(); drawTc(); return; }
        // Cut: the other video already holds the next piece.
        const w = vid(other());
        load(w, mediaById(next.clip)); seek(w, next.in);
        swapTo(other());
        w.play().catch(() => {});
        E.playhead = startOf(hit.i + 1);
        prepareNext(hit.i + 1);
      }
      placeHead(); followHead();
    }
    drawTc();
    raf = requestAnimationFrame(tick);
  }
  function drawTc() {
    if (E.preview) {
      const c = mediaById(E.preview);
      ed("Tc").innerHTML = `<b>${fmt(E.previewTime)}</b> / ${fmt(durOf(c))} <span class="ed-faint">· this video</span>`;
    } else ed("Tc").innerHTML = `<b>${fmt(E.playhead)}</b> / ${fmt(total())}`;
  }
  const drawPlay = () => { ed("Play").innerHTML = E.playing ? SVG.pause : SVG.play; ed("Play").setAttribute("aria-label", E.playing ? "Pause" : "Play"); };
  function sizeFrame() {
    const st = ed("Stage"), [a, b] = RATIOS[E.ratio];
    const W = st.clientWidth - 28, H = st.clientHeight - 28, s = Math.max(0, Math.min(W / a, H / b));
    ed("Frame").style.width = Math.floor(a * s) + "px"; ed("Frame").style.height = Math.floor(b * s) + "px";
  }

  /* ---------------- timeline ---------------- */
  const tx = (t) => X0 + t * E.pxPerSec;
  const xt = (x) => Math.max(0, (x - X0) / E.pxPerSec);
  function drawTimeline() {
    const lane = ed("Lane"), inner = ed("Inner");
    const width = Math.max(lane.clientWidth, tx(total()) + 400);
    inner.style.width = width + "px";
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60];
    const major = steps.find((s) => s * E.pxPerSec >= 70) || 60;
    const minor = major / (major === 15 ? 3 : major === 5 ? 5 : 2);
    let html = "";
    for (let t = 0; tx(t) < width; t += minor) {
      const isMajor = Math.abs(t / major - Math.round(t / major)) < 1e-6;
      html += `<div class="ed-tick ${isMajor ? "major" : ""}" style="left:${tx(t)}px">${isMajor ? `<span>${major < 1 ? t.toFixed(1) + " s" : clock(t)}</span>` : ""}</div>`;
    }
    ed("Ruler").innerHTML = html;
    const tr = ed("Track");
    if (!E.pieces.length) tr.innerHTML = `<div class="ed-track-empty">Drag videos here, or press + on a video</div>`;
    else {
      tr.innerHTML = E.pieces.map((p, i) => {
        const c = mediaById(p.clip), x = tx(startOf(i)), w = Math.max(6, len(p) * E.pxPerSec - 2);
        return `<div class="ed-clip ${E.selected === p.id ? "sel" : ""}" data-piece="${p.id}" style="left:${x}px;width:${w}px">
          <div class="ed-lab">${esc(c?.prompt || "")}<b>${short(len(p))}</b></div>
          <canvas class="ed-strip"></canvas><canvas class="ed-wave ${E.muted ? "muted" : ""}"></canvas>
          <div class="ed-handle l" data-trim="l"></div><div class="ed-handle r" data-trim="r"></div>
        </div>`;
      }).join("");
      tr.querySelectorAll(".ed-clip").forEach(paintClip);
    }
    placeHead();
    ed("Undo").disabled = !E.past.length; ed("Redo").disabled = !E.future.length;
    ed("Del").disabled = !E.selected; ed("Cut").disabled = !canSplit();
    ed("Mute").innerHTML = E.muted ? SVG.mute : SVG.sound;
    ed("Mute").classList.toggle("off", E.muted);
    ed("Mute").title = E.muted ? "Sound is off in the preview and export. Click to turn it on." : "Sound is on in the preview and export";
    header();
  }
  function paintClip(el) {
    const p = E.pieces.find((q) => q.id === el.dataset.piece), info = E.strips[p.clip];
    const w = el.clientWidth;
    const strip = el.querySelector(".ed-strip"), wave = el.querySelector(".ed-wave");
    strip.width = w; strip.height = 52; wave.width = w; wave.height = 18;
    if (!info || info === "loading" || !info.img) { loadStrip(p.clip); return; }
    const g = strip.getContext("2d"), [tw, th] = info.tile, dw = Math.round(52 * tw / th);
    for (let x = 0; x < w; x += dw) {
      const t = p.in + (x / Math.max(1, w)) * len(p);
      const k = Math.max(0, Math.min(info.count - 1, Math.floor(t / info.step)));
      g.drawImage(info.img, k * tw, 0, tw, th, x, 0, dw, 52);
    }
    const peaks = info.peaks || [], per = info.peaksPerSecond || 25, wg = wave.getContext("2d");
    wg.fillStyle = "#7fb3c9";
    for (let x = 0; x < w; x++) {
      const t = p.in + (x / w) * len(p), v = peaks[Math.floor(t * per)] || 0;
      const h = Math.max(1, Math.min(1, v * 2.2) * 18);
      wg.fillRect(x, 18 - h, 1, h);
    }
  }
  const placeHead = () => { ed("Head").style.left = tx(E.playhead) + "px"; };
  function followHead() {
    const lane = ed("Lane"), x = tx(E.playhead);
    if (x > lane.scrollLeft + lane.clientWidth - 60) lane.scrollLeft = x - 120;
    if (x < lane.scrollLeft) lane.scrollLeft = Math.max(0, x - 60);
  }
  function scrollToHead() { const lane = ed("Lane"), x = tx(E.playhead); if (x < lane.scrollLeft || x > lane.scrollLeft + lane.clientWidth - 40) lane.scrollLeft = Math.max(0, x - lane.clientWidth / 3); }
  function draw() { if (!E.visible) return; drawMedia(); drawTimeline(); showFrame(); drawPlay(); }

  function seekFrom(e) {
    const box = ed("Inner").getBoundingClientRect();
    E.playhead = Math.round(Math.min(total(), xt(e.clientX - box.left)) * FPS) / FPS;
    if (E.preview) { E.preview = null; drawMedia(); }
    placeHead(); showFrame();
  }
  // A new clip goes into the gap between clips nearest the playhead (by +, double-click or Enter),
  // or nearest the pointer (by dragging).
  const gapAtHead = () => dropIndex(E.playhead);
  const dropIndex = (t) => { let s = 0; for (let i = 0; i < E.pieces.length; i++) { const l = len(E.pieces[i]); if (t < s + l / 2) return i; s += l; } return E.pieces.length; };
  function showInsert(index) {
    let line = ed("Insert");
    if (!line) { line = document.createElement("div"); line.className = "ed-insert"; line.id = "edInsert"; ed("Inner").appendChild(line); }
    line.style.left = tx(startOf(index)) - 1 + "px";
  }
  const hideInsert = () => ed("Insert")?.remove();
  function canSplit() {
    if (!E.pieces.length) return false;
    const hit = at(E.playhead);
    return hit && E.playhead - hit.start >= MIN_PIECE - 1e-6 && hit.start + len(hit.p) - E.playhead >= MIN_PIECE - 1e-6;
  }
  function split() {
    if (!canSplit()) { say("Move the playhead into a clip, at least half a second from its edges, to split it."); return; }
    const hit = at(E.playhead);
    remember();
    const a = hit.p, b = newPiece(a.clip, hit.local, a.out);
    a.out = hit.local;
    E.pieces.splice(hit.i + 1, 0, b);
    E.selected = b.id;
    changed();
  }
  function del() {
    const i = E.pieces.findIndex((p) => p.id === E.selected);
    if (i < 0) return;
    remember();
    E.pieces.splice(i, 1);
    E.selected = E.pieces[Math.min(i, E.pieces.length - 1)]?.id || null;
    changed();
  }
  const zoomFrom = (v) => 8 * Math.pow(200 / 8, v / 100), sliderFrom = (z) => (100 * Math.log(z / 8)) / Math.log(200 / 8);
  function setZoom(z) { E.pxPerSec = Math.max(8, Math.min(200, z)); ed("Zoom").value = sliderFrom(E.pxPerSec); drawTimeline(); scrollToHead(); }

  /* ---------------- events ---------------- */
  function wire() {
    ed("Q").addEventListener("input", (e) => { E.q = e.target.value; drawMedia(); });
    ed("Filter").addEventListener("change", (e) => { E.filter = e.target.value; drawMedia(); });
    ed("Hide").addEventListener("click", () => { E.hideAdded = !E.hideAdded; drawMedia(); });
    ed("PerRow").addEventListener("input", (e) => { E.perRow = +e.target.value; ed("PerRowN").textContent = E.perRow; ed("Media").style.setProperty("--ed-cols", E.perRow); });
    const grid = ed("Media");
    grid.addEventListener("mouseover", (e) => { const v = e.target.closest(".ed-mtile")?.querySelector("video"); if (v && v.paused) { v.loop = true; v.play().catch(() => {}); } });
    grid.addEventListener("mouseout", (e) => {
      const tile = e.target.closest(".ed-mtile");
      if (tile && !tile.contains(e.relatedTarget)) { const v = tile.querySelector("video"); v.pause(); v.currentTime = 0.5; }
    });
    grid.addEventListener("click", (e) => {
      const add = e.target.closest("[data-edadd]");
      if (add) { hideInsert(); addClip(add.dataset.edadd, gapAtHead()); return; }
      const tile = e.target.closest(".ed-mtile");
      if (!tile) return;
      // A click plays the video on its own in the player; click it again (or the timeline) to go back to the edit.
      pickMedia(E.preview === tile.dataset.clip ? null : tile.dataset.clip);
    });
    grid.addEventListener("dblclick", (e) => { const tile = e.target.closest(".ed-mtile"); if (tile) addClip(tile.dataset.clip, gapAtHead()); });
    // Hovering + shows where the video will go.
    grid.addEventListener("pointerover", (e) => { if (e.target.closest(".ed-add") && E.pieces.length) showInsert(gapAtHead()); });
    grid.addEventListener("pointerout", (e) => { if (e.target.closest(".ed-add")) hideInsert(); });
    grid.addEventListener("dragstart", (e) => {
      const tile = e.target.closest(".ed-mtile");
      if (!tile) return;
      e.dataTransfer.setData("text/x-mmvision-clip", tile.dataset.clip);
      e.dataTransfer.effectAllowed = "copy";
    });

    ed("Play").addEventListener("click", toggle);
    ed("Ratio").addEventListener("change", (e) => { E.ratio = e.target.value; sizeFrame(); save(); });
    ed("Full").addEventListener("click", () => { document.fullscreenElement ? document.exitFullscreen() : ed("Stage").requestFullscreen?.(); });
    document.addEventListener("fullscreenchange", () => setTimeout(() => E.visible && sizeFrame(), 50));
    ed("Va").addEventListener("click", toggle); ed("Vb").addEventListener("click", toggle);

    ed("Ruler").addEventListener("pointerdown", (e) => {
      pause(); seekFrom(e);
      const move = (ev) => seekFrom(ev), up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
      addEventListener("pointermove", move); addEventListener("pointerup", up);
    });
    ed("Inner").addEventListener("pointerdown", (e) => {
      if (e.target.closest(".ed-clip") || e.target.closest(".ed-ruler")) return;
      E.selected = null; pause(); seekFrom(e); drawTimeline();
    });
    ed("Track").addEventListener("pointerdown", onClipDown);
    const lane = ed("Lane");
    lane.addEventListener("dragover", (e) => {
      if (!e.dataTransfer.types.includes("text/x-mmvision-clip")) return;
      e.preventDefault(); e.dataTransfer.dropEffect = "copy";
      ed("Track").classList.add("drop");
      showInsert(dropIndex(xt(e.clientX - ed("Inner").getBoundingClientRect().left)));
    });
    lane.addEventListener("dragleave", (e) => { if (!lane.contains(e.relatedTarget)) { ed("Track").classList.remove("drop"); hideInsert(); } });
    lane.addEventListener("drop", (e) => {
      const id = e.dataTransfer.getData("text/x-mmvision-clip");
      if (!id) return;
      e.preventDefault(); e.stopPropagation();
      ed("Track").classList.remove("drop"); hideInsert();
      addClip(id, dropIndex(xt(e.clientX - ed("Inner").getBoundingClientRect().left)));
    });
    lane.addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); setZoom(E.pxPerSec * (e.deltaY < 0 ? 1.12 : 1 / 1.12)); } }, { passive: false });

    ed("Cut").addEventListener("click", split);
    ed("Del").addEventListener("click", del);
    ed("Undo").addEventListener("click", undo);
    ed("Redo").addEventListener("click", redo);
    ed("Mute").addEventListener("click", () => { E.muted = !E.muted; vid("a").muted = vid("b").muted = E.muted; save(); drawTimeline(); });
    ed("Zoom").addEventListener("input", (e) => setZoom(zoomFrom(+e.target.value)));
    ed("ZoomIn").addEventListener("click", () => setZoom(E.pxPerSec * 1.4));
    ed("ZoomOut").addEventListener("click", () => setZoom(E.pxPerSec / 1.4));
    ed("Fit").addEventListener("click", () => { if (total()) { setZoom((lane.clientWidth - 60) / total()); lane.scrollLeft = 0; } });
    ed("SplitV").addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const start = e.clientY, h0 = ed("Timeline").offsetHeight;
      const move = (ev) => { $("editView").style.setProperty("--ed-tl", Math.max(200, Math.min(innerHeight - 300, h0 - (ev.clientY - start))) + "px"); sizeFrame(); };
      const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); drawTimeline(); };
      addEventListener("pointermove", move); addEventListener("pointerup", up);
    });

    ed("ExGo").addEventListener("click", runExport);
    ed("ExName").addEventListener("keydown", (e) => { if (e.key === "Enter") runExport(); });
    ed("ExCancel").addEventListener("click", () => { if (E.exportJob) api("DELETE", "export/" + E.exportJob).catch(() => {}); });
    ed("Export").addEventListener("click", (e) => {
      if (!ed("Ask").hidden) return;
      if (e.target.closest("[data-edclose]") || (e.target === ed("Export") && !E.exportJob)) ed("Export").hidden = true;
    });

    addEventListener("keydown", onKey);
    addEventListener("resize", () => { if (E.visible) { sizeFrame(); drawTimeline(); } });
  }

  // Clips: click to select, drag to move, drag an edge to trim.
  function onClipDown(e) {
    const el = e.target.closest(".ed-clip");
    if (!el) return;
    e.preventDefault();
    pause();
    const i = E.pieces.findIndex((p) => p.id === el.dataset.piece), p = E.pieces[i], c = mediaById(p.clip);
    E.selected = p.id;
    if (E.preview) { E.preview = null; drawMedia(); }
    ed("Track").querySelectorAll(".ed-clip.sel").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel");
    ed("Del").disabled = false;
    const trim = e.target.dataset.trim, x0 = e.clientX, before = { in: p.in, out: p.out }, full = durOf(c);
    let moved = false, landing = null, tip = null;
    const onMove = (ev) => {
      const dx = ev.clientX - x0;
      if (!moved && Math.abs(dx) < 4) return;
      if (!moved) { moved = true; if (trim) remember(); }
      if (trim) {
        // Trimming: the clips after it move along, so there's never a gap.
        const dt = Math.round((dx / E.pxPerSec) * FPS) / FPS;
        if (trim === "l") p.in = Math.max(0, Math.min(before.out - MIN_PIECE, before.in + dt));
        else p.out = Math.min(full, Math.max(before.in + MIN_PIECE, before.out + dt));
        drawTimeline();
        const now = ed("Track").querySelector(`[data-piece="${p.id}"]`);
        if (!tip) { tip = document.createElement("div"); tip.className = "ed-tip"; ed("Inner").appendChild(tip); }
        tip.textContent = `${short(len(p))} · ${p.in.toFixed(1)}–${p.out.toFixed(1)} s of ${full.toFixed(1)} s`;
        tip.style.left = (trim === "l" ? now.offsetLeft : Math.max(0, now.offsetLeft + now.offsetWidth - 150)) + "px";
        E.playhead = trim === "l" ? startOf(i) : startOf(i) + len(p) - 1 / FPS;
        placeHead(); showFrame();
      } else {
        ed("Track").querySelector(`[data-piece="${p.id}"]`)?.classList.add("ghost");
        landing = dropIndex(xt(ev.clientX - ed("Inner").getBoundingClientRect().left));
        showInsert(landing);
      }
    };
    const onUp = () => {
      removeEventListener("pointermove", onMove); removeEventListener("pointerup", onUp);
      tip?.remove(); hideInsert();
      if (moved && !trim && landing !== null && landing !== i && landing !== i + 1) {
        remember();
        const [piece] = E.pieces.splice(i, 1);
        E.pieces.splice(landing > i ? landing - 1 : landing, 0, piece);
        E.playhead = startOf(E.pieces.indexOf(piece));
      }
      if (moved) changed(); else drawTimeline();
    };
    addEventListener("pointermove", onMove); addEventListener("pointerup", onUp);
  }

  // Keys: Space play, S split, ⌫ delete, ⌘Z undo, ←/→ one frame (⇧ one second), Home/End.
  function onKey(e) {
    if (!E.visible || state.view !== "gallery" || e.target.closest("input, select, textarea") || !ed("Export").hidden) return;
    const k = e.key.toLowerCase();
    if (E.preview && !e.metaKey && !e.ctrlKey && !e.altKey && mediaKey(k)) { e.preventDefault(); return; }
    if (k === " ") { e.preventDefault(); toggle(); }
    else if (k === "s" && !e.metaKey && !e.ctrlKey) split();
    else if (k === "backspace" || k === "delete") { e.preventDefault(); del(); }
    else if (k === "z" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (k === "arrowleft" || k === "arrowright") {
      e.preventDefault(); pause(); E.preview = null;
      E.playhead += (k === "arrowright" ? 1 : -1) * (e.shiftKey ? 1 : 1 / FPS);
      clampHead(); placeHead(); followHead(); showFrame();
    } else if (k === "home") { pause(); E.playhead = 0; placeHead(); followHead(); showFrame(); }
    else if (k === "end") { pause(); E.playhead = total(); placeHead(); followHead(); showFrame(); }
  }

  /* ---------------- export ---------------- */
  function openExport() {
    if (!E.pieces.length) return;
    pause();
    const sizes = (OPT.editSizes || {})[E.ratio] || [[1056, 608]];
    ed("ExSize").innerHTML = sizes.map(([w, h], i) => `<option value="${w}x${h}">${w} × ${h}${i === 0 ? " (as made)" : h === 720 || w === 720 ? " (720p)" : h === 1080 || w === 1080 ? " (1080p)" : ""}</option>`).join("");
    ed("ExName").value = E.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "edit";
    ed("ExSound").checked = !E.muted;
    ed("ExSummary").textContent = `${E.pieces.length} clip${E.pieces.length > 1 ? "s" : ""} · ${fmt(total())} · ${E.ratio} · MP4 (H.264, AAC), 24 fps. Videos of another shape get black bars.`;
    ed("ExForm").hidden = false; ed("ExRun").hidden = true; ed("ExDone").hidden = true;
    ed("Export").hidden = false;
    ed("ExName").focus(); ed("ExName").select();
  }
  async function runExport() {
    if (E.exportJob) return;
    clearTimeout(saveTimer);
    try { await api("PUT", "edit", { name: E.name, pieces: E.pieces, ratio: E.ratio, muted: E.muted }); } catch {}
    const [w, h] = ed("ExSize").value.split("x").map(Number);
    ed("ExForm").hidden = true; ed("ExRun").hidden = false;
    ed("ExBar").style.width = "0"; ed("ExStep").textContent = "Starting…";
    try {
      const job = await api("POST", "export", { name: ed("ExName").value, width: w, height: h, sound: ed("ExSound").checked });
      E.exportJob = job.job;
    } catch (error) {
      ed("ExForm").hidden = false; ed("ExRun").hidden = true;
      say(error.message);
    }
  }
  function onExport(d) {
    if (!d || d.job !== E.exportJob) return;
    if (d.progress != null) ed("ExBar").style.width = Math.round(d.progress * 100) + "%";
    if (d.step) ed("ExStep").textContent = d.step + " · " + Math.round((d.progress || 0) * 100) + "%";
    if (d.done) {
      E.exportJob = null;
      ed("ExRun").hidden = true; ed("ExDone").hidden = false;
      ed("ExPath").textContent = d.subfolder + "/" + d.file;
      ed("ExDownload").href = fileUrl(d.subfolder, d.file);
      ed("ExDownload").setAttribute("download", d.file);
      ed("ExDownload").click();   // the file downloads by itself; the button is there to get it again
    } else if (d.cancelled || d.error) {
      E.exportJob = null;
      ed("Export").hidden = true;
      say(d.error ? "The export failed: " + d.error : "Export cancelled.");
    }
  }

  /* ---------------- what app.js calls ---------------- */
  return {
    load(edit) {
      if (!edit) return;
      E.name = edit.name || "Untitled edit";
      E.pieces = (edit.pieces || []).map((p) => ({ id: p.id, clip: p.clip, in: +p.in, out: +p.out }));
      E.ratio = RATIOS[edit.ratio] ? edit.ratio : "16:9";
      E.muted = !!edit.muted;
    },
    show() {
      build();
      const first = !E.visible;
      E.visible = true;
      $("editView").hidden = false;
      document.documentElement.style.setProperty("--head-h", document.querySelector(".top").offsetHeight + "px");
      prune();
      if (first) {
        ed("Ratio").value = E.ratio;
        vid("a").muted = vid("b").muted = E.muted;
        new Set(E.pieces.map((p) => p.clip)).forEach(loadStrip);
        requestAnimationFrame(() => { sizeFrame(); drawTimeline(); });
      }
      sizeFrame();
      draw();
    },
    hide() {
      if (!E.visible) return;
      E.visible = false;
      pause();
      $("editView").hidden = true;
      header();
    },
    clipsChanged() { if (E.visible) { prune(); drawMedia(); drawTimeline(); } },
    // For the gallery's right-click menu: is a video in the edit, and put it at the end.
    has: (id) => E.pieces.some((p) => p.clip === id),
    append(id) {
      const c = mediaById(id);
      if (!c || E.pieces.some((p) => p.clip === id)) return false;
      if (E.visible) { addClip(id, E.pieces.length); return true; }
      remember();
      const p = newPiece(c.id, 0, durOf(c));
      if (!E.strips[c.id]?.duration) p.toEnd = true;
      if (!E.pieces.length) E.ratio = shapeOf(c);
      E.pieces.push(p);
      E.playhead = startOf(E.pieces.length - 1);
      E.selected = p.id;
      loadStrip(c.id);
      save();
      return true;
    },
    onExport,
  };
})();
