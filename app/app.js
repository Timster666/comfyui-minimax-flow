/* MiniMax H3 Vision: the app page. It talks to the plugin's server (/mmflow/api/...),
   gets render progress over ComfyUI's websocket, and shows files through ComfyUI's /view. */

/* ------------------------------------------------------------------
   Options. The server sends the real lists; these are only fallbacks.
   ------------------------------------------------------------------ */
let OPT = {
  models: [{ name: "MiniMax H3", steps: 20, maxSteps: 40 }],
  aspects: { "16:9": [16, 9], "9:16": [9, 16], "1:1": [1, 1], "4:3": [4, 3], "3:4": [3, 4], "21:9": [21, 9] },
  megapixels: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.5, 2.0],
  length: { min: 2, max: 20, fps: 24 },
  loras: [],
  missing: null,             // why no video model can be used, when none can
  root: "minimax_flow",
  // The Image tab's options (MOSS-Image). The server sends the real lists.
  image: {
    models: [{ id: "moss_image_sft_int8_convrot.safetensors", name: "MOSS-Image SFT (INT8)", steps: 50, minSteps: 1, maxSteps: 80, stepSize: 1, cfg: 3.0 },
      { id: "moss_image_sft_8stepdmd_int8_convrot.safetensors", name: "MOSS-Image SFT Turbo 8-step (INT8)", steps: 8, minSteps: 1, maxSteps: 80, stepSize: 1, cfg: 1.0 }],
    aspects: { "1:1": [1, 1], "4:5": [4, 5], "3:4": [3, 4], "2:3": [2, 3], "9:16": [9, 16], "16:9": [16, 9], "3:2": [3, 2], "4:3": [4, 3], "5:4": [5, 4], "21:9": [21, 9] },
    megapixels: [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0],
    recommendedMP: [1.0, 1.5, 2.0],
    cfg: { min: 1, max: 10, step: 0.5 },
    missing: null,
  },
  // The Klein tab's options (FLUX.2 [klein] 9B). The server sends the real lists.
  klein: {
    models: [{ id: "flux-2-klein-9b.safetensors", name: "Klein 9B Distilled (BF16)", steps: 4, minSteps: 1, maxSteps: 12, stepSize: 1, cfg: 1.0, base: false }],
    aspects: { "1:1": [1, 1], "4:5": [4, 5], "3:4": [3, 4], "2:3": [2, 3], "9:16": [9, 16], "16:9": [16, 9], "3:2": [3, 2], "4:3": [4, 3], "5:4": [5, 4], "21:9": [21, 9] },
    megapixels: [0.5, 1.0, 1.5, 2.0, 3.0, 4.0],
    recommendedMP: [1.0, 1.5, 2.0],
    cfg: { min: 1, max: 10, step: 0.5 },
    slots: 4,
    missing: null,
  },
};
const TONES = ["#2a3a37", "#33302a", "#2b2d3a", "#3b3328", "#26332f", "#30262a", "#222a33", "#2f2a36"];

/* ------------------------------------------------------------------
   State. Everything on screen is drawn from this.
   ------------------------------------------------------------------ */
const state = {
  ready: false,
  view: "gallery",           // "gallery" | "clip"
  openId: null,
  filter: "all",
  perRow: 3,
  prompt: "",
  caret: 0,
  mentionIndex: 0,
  mentionClosed: false,
  refs: [],                  // every uploaded image: {id, name, file, added, img, tone}
  attached: [],              // ids of the images in the prompt bar
  library: { open: false, source: "uploads", selected: null, selectedGen: null, query: "", sort: "newest" },
  clips: [],
  tab: "video",              // "video" | "image" | "klein" | "edit": "edit" is the editor (edit.js), the others the gallery and prompt
  negative: "",              // the Image tab's negative prompt (empty: the model's recommended one)
  negativeOpen: false,
  drafts: { video: "", image: "", klein: "", edit: "" },   // each tab keeps its own prompt
  videoSettings: { model: "HyperFlow H3 Flash", steps: 6, res: 0.6, aspect: "16:9", len: 5, seedMode: "random", seed: 48213, loras: [] },
  imageSettings: { model: "MOSS-Image SFT (INT8)", steps: 50, cfg: 3.0, res: 1.0, aspect: "1:1", seedMode: "random", seed: 48213, loras: [] },
  // size: "match" (image 1's shape) or an aspect ratio, which is also kept in aspect.
  kleinSettings: { model: "Klein 9B Distilled (BF16)", steps: 4, cfg: 1.0, res: 1.0, size: "match", aspect: "1:1", seedMode: "random", seed: 48213, loras: [] },
  // The Klein tab's four slots, image 1-4: ids of images from the Add image library, or null. The slots keep
  // their names: whatever is in slot 3 is "image 3" in the prompt.
  kleinSlots: [null, null, null, null],
  settingsOpen: false,
  loraPicker: false,
};
// state.settings is the settings of the tab you're on.
Object.defineProperty(state, "settings", {
  get: () => (state.tab === "image" ? state.imageSettings : state.tab === "klein" ? state.kleinSettings : state.videoSettings),
});
const deleted = new Set();   // clips deleted here, so a late progress message can't bring one back

/* ------------------------------------------------------------------
   Helpers
   ------------------------------------------------------------------ */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const newSeed = () => Math.floor(Math.random() * 2 ** 31);
const clipById = (id) => state.clips.find((c) => c.id === id);
const mpText = (mp) => (Math.round(mp * 10) === mp * 10 ? Number(mp).toFixed(1) : String(mp)) + " MP";
const isImage = () => state.tab === "image";
const isKlein = () => state.tab === "klein";
const kindOf = (c) => c.kind || "video";
// Which tab a clip belongs to: Klein's images are images too (page, download, Use in a video), in their own tab.
const tabOf = (c) => (c.family === "klein" ? "klein" : kindOf(c));
// The options of the tab you're on: models, aspect ratios and sizes.
const tabOpt = () => (isImage() ? OPT.image : isKlein() ? OPT.klein : { models: OPT.models, aspects: OPT.aspects, megapixels: OPT.megapixels });
// With nothing installed, the settings keep the saved model so the pop-up still works.
const modelOf = (name) => tabOpt().models.find((m) => m.name === name) || tabOpt().models[0]
  || { name: name || "Not installed", steps: state.settings.steps || 20, minSteps: 1, maxSteps: 80, stepSize: 1, cfg: state.settings.cfg || 3 };
const aspectOf = (a) => OPT.aspects[a] || OPT.image.aspects[a] || (/^\d+:\d+$/.test(a || "") ? a.split(":").map(Number) : [16, 9]);
// A clip's shape: its aspect ratio, or (Klein's Match image 1) its exact width and height.
const shapeOf = (c) => (c.family === "klein" && c.width && c.height ? c.width + ":" + c.height : c.aspect);
const clock = (s) => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
const toneOf = (id) => TONES[[...id].reduce((a, ch) => a + ch.charCodeAt(0), 0) % TONES.length];
const viewUrl = (folder, file) => `/view?type=output&subfolder=${encodeURIComponent(OPT.root + "/" + folder)}&filename=${encodeURIComponent(file)}`;
const fileUrl = (subfolder, file) => `/view?type=output&subfolder=${encodeURIComponent(subfolder)}&filename=${encodeURIComponent(file)}`;
const clipUrl = (c) => c.url || (c.file ? fileUrl(c.subfolder || OPT.root + (kindOf(c) === "image" ? "/stills" : "/clips"), c.file) : "");
// H3 takes lengths in steps of 17 frames, so a clip gets the nearest such length.
const snapFrames = (seconds) => 5 + 17 * Math.max(1, Math.round((seconds * OPT.length.fps - 5) / 17));
const isBusy = (c) => c.status === "queued" || c.status === "rendering";
const loraText = (l) => l.name.replace(/\\/g, "/").split("/").pop().replace(/\.[^.]+$/, "") + " · " + Number(l.strength).toFixed(2);
const attach = (id) => { if (!state.attached.includes(id)) state.attached.push(id); };

// Width and height for an aspect ratio at a size in megapixels. H3 wants sides in steps of 32, MOSS-Image in steps of 16.
function dims(aspect, mp, kind = state.tab) {
  const [a, b] = aspectOf(aspect);
  const grid = kind === "image" || kind === "klein" ? 16 : 32;
  const pixels = mp * 1024 * 1024;
  const w = Math.max(grid, Math.round(Math.sqrt((pixels * a) / b) / grid) * grid);
  const h = Math.max(grid, Math.round(Math.sqrt((pixels * b) / a) / grid) * grid);
  return [w, h];
}
const sizeText = (aspect, mp) => dims(aspect, mp).join("×");

async function api(method, path, body) {
  const form = body instanceof FormData;
  const res = await fetch("/mmflow/api/" + path, {
    method,
    headers: body && !form ? { "Content-Type": "application/json" } : undefined,
    body: form ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}

const ICON = {
  x: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  plus: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  back: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>',
  star: (on) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z"/></svg>`,
  dice: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.2" fill="currentColor"/><circle cx="15.5" cy="15.5" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/></svg>',
  play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg>',
  pause: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>',
  expand: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  shrink: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>',
  search: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
  upload: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>',
  download: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 4v12M7 11l5 5 5-5M5 20h14"/></svg>',
  trash: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  reuse: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"/></svg>',
  video: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/></svg>',
  image: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>',
  klein: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><path d="M17 13v8M13 17h8"/></svg>',
  edit: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15h18M8 15v4M14 15v4"/></svg>',
  copy: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
};

function mentionsIn(text) {
  return [...text.matchAll(/@([A-Za-z0-9_]+)/g)].map((m) => m[1]);
}

// The prompt as HTML, with known @names highlighted and unknown ones underlined.
function promptHTML(text, kind = state.tab, slots = state.kleinSlots) {
  if (kind === "image") return esc(text);
  // Klein: "image 1" to "image 4" name the slots; a slot with nothing in it is underlined.
  if (kind === "klein") {
    return text.split(/\b(image\s?[1-4])\b/i).map((part, i) => i % 2
      ? `<span class="${slots[Number(part.slice(-1)) - 1] ? "m" : "bad"}">${esc(part)}</span>` : esc(part)).join("");
  }
  const names = new Set(state.refs.map((r) => r.name));
  return text.split(/(@[A-Za-z0-9_]+)/).map((part) => {
    if (/^@[A-Za-z0-9_]+$/.test(part)) return `<span class="${names.has(part.slice(1)) ? "m" : "bad"}">${esc(part)}</span>`;
    return esc(part);
  }).join("");
}

// A box with the clip's aspect ratio, fitted inside a 16:9 frame.
function picStyle(aspect, tone) {
  const [a, b] = aspectOf(aspect);
  const fit = a / b >= 16 / 9 ? "width:100%" : "height:100%";
  return `${fit};aspect-ratio:${a} / ${b};background:${tone}`;
}

const activeLoras = () => state.settings.loras.filter((l) => l.on);
const stepsText = (n, rec) => n + (n === rec ? " · recommended" : "");

function summary() {
  const s = state.settings;
  const n = activeLoras().length;
  if (isKlein()) {
    const m = modelOf(s.model), [w, h] = kleinSize();
    return [s.model, s.steps + " steps", ...(m.base ? ["Guidance " + s.cfg] : []), s.size === "match" ? "Match image 1" : s.aspect,
      w + "×" + h, s.seedMode === "fixed" ? "Seed " + s.seed : "Random seed", n + (n === 1 ? " LoRA" : " LoRAs")].join(" · ");
  }
  return [s.model, s.steps + " steps", ...(isImage() ? ["Guidance " + s.cfg] : []), mpText(s.res), s.aspect, ...(isImage() ? [] : [s.len + " s"]),
    s.seedMode === "fixed" ? "Seed " + s.seed : "Random seed", n + (n === 1 ? " LoRA" : " LoRAs")].join(" · ");
}

function progressText(c) {
  if (c.status === "queued") return "Waiting…";
  if (c.status === "failed") return "Render failed";
  if (!c.step) return "Loading the model…";
  if (c.step >= c.steps) return kindOf(c) === "image" ? "Finishing the image…" : "Finishing the video…";
  return "Step " + c.step + " of " + c.steps;
}
const barWidth = (c) => ((c.step || 0) / c.steps) * 100 + "%";
const takeProgress = (c) => (c.status === "queued" ? " · waiting" : c.status === "rendering" ? " · step " + (c.step || 0) + "/" + c.steps : c.status === "failed" ? " · failed" : "");

let toastTimer;
function say(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

// Settings, the prompt draft and the prompt bar are saved on the server, so they survive a reload.
let saveTimer;
function saveUi() {
  if (!state.ready) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    state.drafts[state.tab] = state.prompt;
    api("PUT", "ui", { settings: state.videoSettings, imageSettings: state.imageSettings, attached: state.attached, perRow: state.perRow,
      prompt: state.drafts.video, imagePrompt: state.drafts.image, imageNegative: state.negative, negativeOpen: state.negativeOpen,
      kleinSettings: state.kleinSettings, kleinPrompt: state.drafts.klein, kleinSlots: state.kleinSlots,
      tab: state.tab }).catch(() => {});
  }, 400);
}

/* ------------------------------------------------------------------
   Drawing
   ------------------------------------------------------------------ */
function render() {
  const editing = state.view === "gallery" && state.tab === "edit";
  const inGallery = state.view === "gallery" && !editing;
  if (state.view === "gallery") { $("pv")?.pause(); shownClip = null; }
  $("gallery").hidden = !inGallery;
  $("dock").hidden = !inGallery;
  if (editing) {
    $("clipView").hidden = true;
    $("scrim").hidden = true;
    renderHeader();
    EDIT.show();
    saveUi();
    return;
  }
  EDIT.hide();
  $("clipView").hidden = inGallery;
  // An open pop-up darkens everything else: the page, the top bar and the prompt bar, all but its own button.
  const popup = inGallery && (state.settingsOpen || state.library.open);
  $("scrim").hidden = !popup;
  $("composerDim").hidden = !popup;
  document.body.classList.toggle("popup-open", popup);
  document.body.classList.toggle("library-open", popup && state.library.open);
  renderHeader();
  if (inGallery) {
    renderFilters();
    renderGrid();
    renderRefs();
    renderPrompt();
    renderSettings();
    renderLibrary();
  } else {
    renderClip();
  }
  saveUi();
}

function renderHeader() {
  $("tabs").hidden = state.view !== "gallery";
  $("tabs").innerHTML = [["video", "Video", ICON.video], ["image", "Image", ICON.image], ["klein", "Klein 9b", ICON.klein], ["edit", "Edit", ICON.edit]].map(([id, label, icon]) => {
    const busy = state.clips.filter((c) => tabOf(c) === id && isBusy(c)).length;
    return `<button class="tab ${state.tab === id ? "on" : ""}" data-act="tab" data-id="${id}" role="tab" aria-selected="${state.tab === id}">${icon}${label}${busy ? `<span class="tab-busy" title="${busy} rendering">${busy}</span>` : ""}</button>`;
  }).join("");
  $("headLeft").innerHTML = (state.view === "clip"
    ? `<button class="back" data-act="back">${ICON.back}Back to gallery</button>`
    : `<span class="brand">MiniMax H3 Vision</span>`)
    + (state.ready && missingWhy() && state.view === "gallery" && state.tab !== "edit" ? `<button class="missing" data-act="whyMissing" title="${esc(missingWhy())}">Not installed</button>` : "");
  const busy = state.clips.filter(isBusy).length;
  $("queueDot").classList.toggle("on", busy > 0);
  $("queueText").textContent = busy ? busy + " rendering" : "Queue empty";
}

// Why this tab can't render, when its models aren't installed.
const missingWhy = () => (isImage() ? OPT.image.missing : isKlein() ? OPT.klein.missing : OPT.missing) || "";

const tabClips = () => state.clips.filter((c) => tabOf(c) === state.tab);
const filteredClips = () => tabClips().filter((c) => state.filter === "all" || c.starred);

function renderFilters() {
  const starred = tabClips().filter((c) => c.starred).length;
  $("filters").innerHTML = [["all", "All", tabClips().length], ["starred", "Starred", starred]]
    .map(([id, label, n]) => `<button class="chip ${state.filter === id ? "on" : ""}" data-act="filter" data-id="${id}">${label} · ${n}</button>`).join("")
    + `<label class="chip import" title="Bring in a video or image made earlier. Its prompt and settings are read from the file. A .zip from Export .zip brings its reference images too. You can also drop files on the page.">${ICON.upload}Import
        <input type="file" accept="video/*,image/png,image/jpeg,image/webp,.zip,application/zip" multiple data-change="importFiles"></label>`;
}

function renderGrid() {
  const grid = $("grid");
  grid.style.setProperty("--per-row", state.perRow);
  $("perRow").value = state.perRow;
  $("perRowVal").textContent = state.perRow;
  $("perRowLabel").textContent = isImage() ? "Images per row" : "Videos per row";
  const clips = filteredClips();
  if (!clips.length) {
    tileKeys = new WeakMap();
    grid.innerHTML = `<div class="empty">${state.ready ? (state.filter === "starred" ? (isImage() ? "No starred images yet." : "No starred clips yet.") : isImage() ? "No images yet. Describe one below and press the arrow button." : "Nothing here yet. Write a prompt below and press the arrow button.")
      : state.loadError ? `The MiniMax H3 Vision server answered with an error, so nothing could be loaded. Trying again…<br><span class="fail-why">${esc(state.loadError.slice(0, 300))}</span>` : "Loading…"}</div>`;
    return;
  }
  patchGrid(grid, clips.map((c) => [c.id, kindOf(c) === "image" ? imageTile(c) : videoTile(c)]));
  // A kept tile's progress (Waiting… → Loading the model… → Step n of m) is brought up to date here.
  clips.forEach((c) => {
    if (!isBusy(c)) return;
    const tile = grid.querySelector(`[data-clip="${c.id}"]`);
    const text = tile?.querySelector("[data-progtext]"), fill = tile?.querySelector("[data-prog]");
    if (text && text.textContent !== progressText(c)) text.textContent = progressText(c);
    if (fill) fill.style.width = barWidth(c);
  });
  if (hover.id) requestAnimationFrame(hoverFrame);
}

// Tiles are only rebuilt when what they show changed. Rebuilding every tile reloaded every video, so
// opening or closing a pop-up made the whole grid blink. Progress text and bar width are left out of
// the comparison: renderGrid sets them on the kept tiles.
let tileKeys = new WeakMap();
const tileKey = (html) => html.replace(/(data-progtext="[^"]*">)[^<]*/, "$1").replace(/(data-prog="[^"]*" style=")[^"]*/, "$1");
function patchGrid(grid, tiles) {
  const old = new Map([...grid.children].filter((el) => el.dataset.clip).map((el) => [el.dataset.clip, el]));
  const nodes = tiles.map(([id, html]) => {
    const el = old.get(id), key = tileKey(html);
    if (el && tileKeys.get(el) === key) return el;
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    const fresh = t.content.firstElementChild;
    tileKeys.set(fresh, key);
    return fresh;
  });
  // Leave the grid alone when nothing changed; otherwise put the tiles in order, moving only what moved.
  const now = [...grid.children];
  if (now.length === nodes.length && nodes.every((n, i) => now[i] === n)) return;
  [...grid.children].forEach((el) => { if (!nodes.includes(el)) el.remove(); });
  nodes.forEach((n, i) => { if (grid.children[i] !== n) grid.insertBefore(n, grid.children[i] || null); });
}

// A video in the grid.
function videoTile(c) {
  const busy = isBusy(c), failed = c.status === "failed";
  return `<div class="tile" data-clip="${c.id}">
      <div class="frame">
        <span class="pic" style="${picStyle(c.aspect, toneOf(c.id))}">${busy && c.preview ? liveVideo(c) : c.file ? `<video src="${clipUrl(c)}" muted loop playsinline preload="metadata"></video>` : ""}</span>
        <button class="tile-hit" data-act="open" data-id="${c.id}" aria-label="Open clip: ${esc(c.prompt)}"></button>
        ${busy ? `<span class="busy ${c.preview ? "has-live" : ""}"><span data-progtext="${c.id}">${progressText(c)}</span><span class="track"><span class="fill" data-prog="${c.id}" style="width:${barWidth(c)}"></span></span>
          <button class="cancel" data-act="cancel" data-id="${c.id}">Cancel</button></span>` : ""}
        ${failed ? `<span class="busy failed"><span>Render failed</span><span class="fail-why">${esc(c.error || "")}</span>
          <button class="cancel" data-act="deleteClip" data-id="${c.id}">Remove</button></span>` : ""}
        <span class="badge">${mpText(c.res)}</span>
        <span class="len" data-len="${c.id}">${c.len} s</span>
        ${c.file ? `<span class="hover-line" aria-hidden="true"><span data-hoverbar="${c.id}"></span></span>` : ""}
        <button class="star ${c.starred ? "on" : ""}" data-act="star" data-id="${c.id}" aria-label="${c.starred ? "Unstar" : "Star"}">${ICON.star(c.starred)}</button>
      </div>
    </div>`;
}

// An image in the grid: the picture, or its live preview while it renders.
function imageTile(c) {
  const busy = isBusy(c), failed = c.status === "failed", done = c.status === "done";
  const picture = busy && c.preview ? `<img data-live="${c.id}" src="${c.preview}" alt="">` : done ? `<img src="${clipUrl(c)}" alt="" loading="lazy" draggable="false">` : "";
  // A finished image can be dragged: onto a Klein slot, or into the Video tab's prompt as an @image.
  return `<div class="tile" data-clip="${c.id}" ${done ? `draggable="true" data-drag-gen="${c.id}"` : ""}>
      <div class="frame">
        <span class="pic" style="${picStyle(shapeOf(c), toneOf(c.id))}">${picture}</span>
        <button class="tile-hit" data-act="open" data-id="${c.id}" aria-label="Open image: ${esc(c.prompt)}"></button>
        ${busy ? `<span class="busy ${c.preview ? "has-live" : ""}"><span data-progtext="${c.id}">${progressText(c)}</span><span class="track"><span class="fill" data-prog="${c.id}" style="width:${barWidth(c)}"></span></span>
          <button class="cancel" data-act="cancel" data-id="${c.id}">Cancel</button></span>` : ""}
        ${failed ? `<span class="busy failed"><span>Render failed</span><span class="fail-why">${esc(c.error || "")}</span>
          <button class="cancel" data-act="deleteClip" data-id="${c.id}">Remove</button></span>` : ""}
        <span class="badge">${mpText(c.res)}</span>
        <span class="len">${c.family === "klein" && c.width ? c.width + "×" + c.height : c.aspect}</span>
        ${done && isKlein() ? `<button class="edit-this" data-act="editThis" data-id="${c.id}" title="Put this image in image 1 and keep editing it">Edit this</button>` : ""}
        <button class="star ${c.starred ? "on" : ""}" data-act="star" data-id="${c.id}" aria-label="${c.starred ? "Unstar" : "Star"}">${ICON.star(c.starred)}</button>
      </div>
    </div>`;
}

// The Image tab's negative prompt: a second, smaller box under the prompt, opened from the bar.
function renderNegative() {
  const on = isImage();
  $("negToggle").hidden = !on;
  $("negBox").hidden = !(on && state.negativeOpen);
  if (!on) return;
  $("negToggle").innerHTML = (state.negativeOpen ? "− " : "+ ") + "Negative prompt" + (state.negative.trim() ? "" : " · none");
  $("negToggle").setAttribute("aria-expanded", state.negativeOpen);
  const box = $("negative");
  if (box.value !== state.negative) box.value = state.negative;
  box.placeholder = "No negative prompt";
}

function renderRefs() {
  renderNegative();
  // Reset (top right of the prompt) shows when there is something to clear.
  $("resetPrompt").hidden = !(state.prompt.trim() || (isKlein() ? state.kleinSlots.some(Boolean) : !isImage() && state.attached.length));
  if (isKlein()) { renderSlots(); return; }
  document.querySelector(".composer").classList.toggle("no-refs", isImage());
  // MOSS-Image takes no reference pictures, so the Image tab has no image strip.
  $("refs").hidden = isImage();
  $("hint").textContent = isImage() ? "MOSS-Image is good at words in pictures: put the text you want in quotes" : "Type @ to use an image";
  if (isImage()) return;
  const used = new Set(mentionsIn(state.prompt));
  const strip = state.attached.map((id) => state.refs.find((r) => r.id === id)).filter(Boolean);
  // @names in the prompt with no image (from an old clip, or a deleted image): drop an image on one to fill it in.
  const known = new Set(state.refs.map((r) => r.name));
  const missing = [...new Set(mentionsIn(state.prompt))].filter((n) => !known.has(n));
  $("refs").innerHTML = missing.map((n) => `
    <div class="ref used ref-gone">
      <span class="ref-pic ref-missing" data-replace-name="${esc(n)}" title="No image is called @${esc(n)}. Drag an image here from Add image to use it instead.">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 10a3 3 0 1 1 4 2.8c-.6.3-1 .8-1 1.5M12 17h.01"/></svg>
        <small>Missing</small></span>
      <span class="ref-name bad-name">@${esc(n)}</span>
    </div>`).join("") + strip.map((r) => `
    <div class="ref ${used.has(r.name) ? "used" : ""}">
      <span class="ref-pic" style="background:${r.tone}" draggable="true" data-drag-ref="${r.id}" data-replace-ref="${r.id}" title="Drag into the prompt. Drop another image here to replace this one."><img src="${r.img}" alt="" draggable="false">
        <button class="ref-x" data-act="removeRef" data-id="${r.id}" aria-label="Take @${esc(r.name)} out of the prompt bar">${ICON.x}</button>
      </span>
      <label class="ref-name">@<input value="${esc(r.name)}" data-change="renameRef" data-id="${r.id}" aria-label="Name of @${esc(r.name)}"></label>
    </div>`).join("") + `
    <button class="add-ref" id="addRef" data-act="openLibrary" aria-expanded="${state.library.open}">${ICON.plus}Add image</button>`;
}

/* Klein's four slots, image 1-4. They keep their names: swapping two pictures (drag one onto another) or
   putting a new one in doesn't change the prompt. Each shows the picture's own size under it. */
const slotRef = (i) => state.refs.find((r) => r.id === state.kleinSlots[i]) || null;
function refSize(r) {
  if (r.w || r.sizing) return;
  r.sizing = true;
  const im = new Image();
  im.onload = () => { r.w = im.naturalWidth; r.h = im.naturalHeight; if (isKlein() && state.view === "gallery") renderRefs(); if (state.view === "clip") renderClip(); };
  im.src = r.img;
}
// The size a Klein render comes out at: image 1's shape (Match image 1), or an aspect ratio, at the resolution.
function kleinSize(s = state.kleinSettings) {
  const r = slotRef(0);
  if (s.size === "match" && r && r.w) {
    const k = Math.sqrt((s.res * 1024 * 1024) / (r.w * r.h));
    return [Math.max(16, Math.round((r.w * k) / 16) * 16), Math.max(16, Math.round((r.h * k) / 16) * 16)];
  }
  return dims(s.size === "match" ? "1:1" : s.aspect, s.res, "klein");
}
// The slots the prompt names that have nothing in them.
const emptyNamed = (text = state.prompt) => [...new Set([...text.matchAll(/\bimage\s?([1-4])\b/gi)].map((m) => Number(m[1])))]
  .filter((n) => !state.kleinSlots[n - 1]).sort();
function renderSlots() {
  $("refs").hidden = false;
  document.querySelector(".composer").classList.remove("no-refs");
  const filled = state.kleinSlots.filter(Boolean).length, empty = emptyNamed();
  $("hint").textContent = empty.length
    ? `The prompt names ${empty.map((n) => "image " + n).join(" and ")}, but ${empty.length > 1 ? "those slots are" : "that slot is"} empty`
    : filled ? `${filled} of 4 pictures · drag one onto another slot to swap them` : "No pictures: Klein makes one from the prompt alone";
  $("hint").classList.toggle("warn", empty.length > 0);
  $("refs").innerHTML = state.kleinSlots.map((id, i) => {
    const r = slotRef(i);
    if (!r) return `<div class="ref slot">
        <button class="slot-empty" data-act="pickSlot" data-id="${i}" data-slot="${i}" aria-label="Add image ${i + 1}" title="Add a picture here: click, or drag one in from the grid, Add image or your computer">${ICON.plus}image ${i + 1}</button>
        <span class="slot-res"></span></div>`;
    refSize(r);
    return `<div class="ref slot used">
        <span class="ref-pic slot-pic" style="background:${r.tone}" draggable="true" data-drag-slot="${i}" data-slot="${i}" title="image ${i + 1}: @${esc(r.name)}. Drag onto another slot to swap them.">
          <img src="${r.img}" alt="" draggable="false"><span class="slot-name">image ${i + 1}</span>
          <button class="ref-x" data-act="clearSlot" data-id="${i}" aria-label="Empty image ${i + 1}">${ICON.x}</button>
        </span>
        <span class="slot-res">${r.w ? r.w + "×" + r.h : ""}</span></div>`;
  }).join("") + `<button class="add-ref" id="addRef" data-act="openLibrary" aria-expanded="${state.library.open}">${ICON.plus}Add image</button>`;
}
// Put a picture (an Add image library entry) in a slot, or in the first empty one. Returns the slot, or -1.
function putInSlot(ref, slot = -1) {
  if (slot < 0) slot = state.kleinSlots.indexOf(null);
  if (slot < 0) { say("All four slots are full. Drop it on one to replace that picture."); return -1; }
  state.kleinSlots[slot] = ref.id;
  render();
  say(`@${ref.name} is image ${slot + 1}.`);
  return slot;
}

function renderPrompt() {
  $("mirror").innerHTML = state.prompt
    ? promptHTML(state.prompt) + "​"
    : `<span class="ph">${isImage() ? "Describe an image, for example: a neon sign that says \"OPEN ALL NIGHT\" in the rain."
      : isKlein() ? "Describe the edit. Call the pictures image 1, image 2, image 3 and image 4, for example: put the bag from image 2 in the hand of the woman in image 1."
      : "Describe a shot. Type @ to use one of your images."}</span>`;
  if ($("prompt").value !== state.prompt) $("prompt").value = state.prompt;
  fitPrompt();
  renderMention();
}

/* @-mentions */
function mentionQuery() {
  if (state.mentionClosed || isImage() || isKlein()) return null;
  const m = /(^|\s)@([A-Za-z0-9_]*)$/.exec(state.prompt.slice(0, state.caret));
  return m ? m[2] : null;
}

function mentionMatches() {
  const q = mentionQuery();
  if (q === null) return [];
  return state.refs.filter((r) => r.name.toLowerCase().startsWith(q.toLowerCase()));
}

function renderMention() {
  const q = mentionQuery();
  const box = $("mention");
  if (q === null) { box.hidden = true; return; }
  const list = mentionMatches();
  const idx = Math.min(state.mentionIndex, Math.max(list.length - 1, 0));
  const used = new Set(mentionsIn(state.prompt));
  box.hidden = false;
  box.innerHTML = `<span class="mention-title">${list.length ? (q ? `Images starting with "${esc(q)}"` : "Your images") : state.refs.length ? `No image called @${esc(q)}` : "No images yet. Use Add image to upload one."}</span>`
    + list.map((r, i) => `
      <button class="mention-item ${i === idx ? "on" : ""}" data-act="pickMention" data-id="${r.id}" role="option" aria-selected="${i === idx}">
        <span class="mention-thumb" style="background:${r.tone}"><img src="${r.img}" alt=""></span>
        <span>@${esc(r.name)}<span class="mention-sub">${used.has(r.name) ? "Already in the prompt" : "Image"}</span></span>
      </button>`).join("")
    + `<span class="mention-help">↑ ↓ to pick · Enter to insert · Esc to close</span>`;
}

function insertMention(ref) {
  attach(ref.id);
  const before = state.prompt.slice(0, state.caret).replace(/@([A-Za-z0-9_]*)$/, "@" + ref.name + " ");
  const after = state.prompt.slice(state.caret).replace(/^[A-Za-z0-9_]*\s?/, "");
  state.prompt = before + after;
  state.caret = before.length;
  state.mentionIndex = 0;
  state.mentionClosed = true;
  renderPrompt();
  renderRefs();
  saveUi();
  const ta = $("prompt");
  ta.focus();
  ta.setSelectionRange(state.caret, state.caret);
}

/* Image library: a pop-up above the prompt bar */
// The library has two sources: your uploads, and the finished images from the Image tab.
// An image from the Image tab becomes an @image (a library entry) the first time it is used.
const libSources = () => [
  ["uploads", "Uploads", ICON.upload, uploadItems().length],
  ["generated", "Generated images", ICON.image, state.clips.filter((c) => kindOf(c) === "image" && c.status === "done").length],
];
const isGen = () => state.library.source === "generated";
const refForClip = (clipId) => state.refs.find((r) => r.from_clip === clipId);

function uploadItems() {
  const L = state.library;
  const q = L.query.trim().toLowerCase();
  const items = state.refs.filter((r) => !r.from_clip && (!q || r.name.toLowerCase().includes(q)));
  const order = {
    newest: (a, b) => b.added - a.added,
    oldest: (a, b) => a.added - b.added,
    name: (a, b) => a.name.localeCompare(b.name),
  }[L.sort];
  return items.sort(order);
}

function generatedItems() {
  const L = state.library;
  const q = L.query.trim().toLowerCase();
  const items = state.clips.filter((c) => kindOf(c) === "image" && c.status === "done" && (!q || c.prompt.toLowerCase().includes(q)
    || (refForClip(c.id)?.name || "").includes(q)));
  const order = {
    newest: (a, b) => (b.created || 0) - (a.created || 0),
    oldest: (a, b) => (a.created || 0) - (b.created || 0),
    name: (a, b) => a.prompt.localeCompare(b.prompt),
  }[L.sort];
  return items.sort(order);
}

// The ids shown in the list now, in order (for the arrow keys).
const libraryIds = () => (isGen() ? generatedItems() : uploadItems()).map((x) => x.id);
const libSelected = () => (isGen() ? state.library.selectedGen : state.library.selected);

// Where pop-ups above the prompt bar may reach up to: a little under the top bar.
const belowTop = () => (document.querySelector(".top")?.getBoundingClientRect().bottom || 0) + 12;

function renderLibrary() {
  const box = $("library");
  box.hidden = !state.library.open;
  if (!state.library.open) return;
  box.style.maxHeight = Math.max(320, document.querySelector(".composer").getBoundingClientRect().top - 12 - belowTop()) + "px";
  if (!box.firstChild) {
    // The frame is drawn once, so the search field keeps focus while you type.
    box.innerHTML = `
      <div class="lib-top">
        <label class="lib-search">${ICON.search}<input id="libQuery" type="search" placeholder="Search images" aria-label="Search images" data-input="libQuery" aria-controls="libList"></label>
        <select class="select lib-sort" aria-label="Sort images" data-change="libSort">
          <option value="newest">Newest</option><option value="oldest">Oldest</option><option value="name">Name A–Z</option>
        </select>
      </div>
      <div class="lib-body">
        <nav class="lib-nav" id="libNav" aria-label="Where the images come from"></nav>
        <div class="lib-list" id="libList" role="listbox" aria-label="Your images"></div>
        <div class="lib-preview" id="libPreview"></div>
      </div>`;
  }
  box.querySelector(".lib-sort").value = state.library.sort;
  renderLibNav();
  renderLibList();
  renderLibPreview();
}

function renderLibNav() {
  $("libNav").innerHTML = libSources().map(([id, label, icon, count]) => `
    <button class="lib-src ${state.library.source === id ? "on" : ""}" data-act="libSource" data-id="${id}" aria-pressed="${state.library.source === id}">
      ${icon}<span>${label}</span><span class="lib-count">${count}</span>
    </button>`).join("");
}

function renderLibList() {
  if (isGen()) { renderGenList(); return; }
  const items = uploadItems();
  $("libList").innerHTML = `
    <label class="lib-upload">${ICON.upload}Upload image
      <input type="file" accept="image/*" multiple data-change="addImages">
    </label>`
    + (items.length ? items.map((r) => `
      <div class="lib-item ${r.id === state.library.selected ? "on" : ""}" data-act="libSelect" data-id="${r.id}" role="option" tabindex="${r.id === state.library.selected ? 0 : -1}" aria-selected="${r.id === state.library.selected}" draggable="true" data-drag-ref="${r.id}" title="Drag into the prompt">
        <span class="lib-thumb" style="background:${r.tone}"><img src="${r.img}" alt="" draggable="false"></span>
        <span class="lib-text"><span class="lib-name">@${esc(r.name)}</span><span class="lib-sub">${state.attached.includes(r.id) ? "Image · in the prompt bar" : "Image"}</span></span>
      </div>`).join("")
    : `<span class="lib-none">${state.refs.some((r) => !r.from_clip) ? `No images match "${esc(state.library.query)}".` : "No uploads yet. Upload one, or drop images anywhere on the page."}</span>`);
}

function renderGenList() {
  const items = generatedItems();
  $("libList").innerHTML = items.length ? items.map((c) => {
    const ref = refForClip(c.id), on = c.id === state.library.selectedGen;
    return `
      <div class="lib-item ${on ? "on" : ""}" data-act="libSelect" data-id="${c.id}" role="option" tabindex="${on ? 0 : -1}" aria-selected="${on}" draggable="true" data-drag-gen="${c.id}" title="Drag into the prompt">
        <span class="lib-thumb" style="background:${toneOf(c.id)}"><img src="${clipUrl(c)}" alt="" draggable="false"></span>
        <span class="lib-text"><span class="lib-name">${ref ? "@" + esc(ref.name) : esc(c.prompt)}</span><span class="lib-sub">${ref && state.attached.includes(ref.id) ? "In the prompt bar" : esc(c.model.replace(/ \(.*\)$/, "")) + " · " + c.aspect}</span></span>
      </div>`;
  }).join("")
    : `<span class="lib-none">${state.clips.some((c) => kindOf(c) === "image" && c.status === "done") ? `No images match "${esc(state.library.query)}".`
      : `No generated images yet. Make some in the <button class="link inline" data-act="tab" data-id="image">Image tab</button>.`}</span>`;
}

function renderLibPreview() {
  if (isGen()) {
    const c = clipById(state.library.selectedGen);
    if (!c || kindOf(c) !== "image") { $("libPreview").innerHTML = `<span class="lib-none">Pick an image to see it here.</span>`; return; }
    const ref = refForClip(c.id), inBar = ref && state.attached.includes(ref.id);
    $("libPreview").innerHTML = `
      <div class="lib-big" draggable="true" data-drag-gen="${c.id}" title="Drag into the prompt"><img src="${clipUrl(c)}" alt="${esc(c.prompt)}" draggable="false"></div>
      <p class="lib-caption">${esc(c.prompt)}</p>
      ${libButton(ref?.id, inBar)}`;
    return;
  }
  const r = state.refs.find((x) => x.id === state.library.selected);
  if (!r) { $("libPreview").innerHTML = `<span class="lib-none">Pick an image to see it here.</span>`; return; }
  const inBar = state.attached.includes(r.id);
  $("libPreview").innerHTML = `
    <div class="lib-big" draggable="true" data-drag-ref="${r.id}" title="Drag into the prompt"><img src="${r.img}" alt="@${esc(r.name)}" draggable="false"></div>
    ${libButton(r.id, inBar)}`;
}
// The button under the library's big preview: into the prompt bar, or (Klein) into a slot.
function libButton(refId, inBar) {
  if (isKlein()) {
    const at = refId ? state.kleinSlots.indexOf(refId) : -1;
    const to = state.library.slot != null ? state.library.slot : state.kleinSlots.indexOf(null);
    if (at >= 0) return `<button class="btn lib-add" data-act="libToggle">Take out of image ${at + 1}</button>`;
    return `<button class="btn primary lib-add" data-act="libToggle" ${to < 0 ? "disabled" : ""}>${to < 0 ? "All four slots are full" : "Put in image " + (to + 1)}</button>`;
  }
  return `<button class="btn ${inBar ? "" : "primary"} lib-add" data-act="libToggle">${inBar ? "Remove from prompt" : "Add to prompt"}</button>`;
}

// Move the selection up or down the list, and keep it in view.
function libMove(delta) {
  const ids = libraryIds();
  if (!ids.length) return;
  const i = ids.indexOf(libSelected());
  const next = ids[i < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, i + delta))];
  ACT.libSelect(next, null, null, true);
}

// The @image for a finished image from the Image tab, made the first time it is needed.
async function refFromClip(clipId) {
  const have = refForClip(clipId);
  if (have) return have;
  const { image } = await api("POST", "images/from-clip", { clip_id: clipId });
  const ref = fromImage(image);
  if (!state.refs.some((r) => r.id === ref.id)) state.refs.push(ref);
  return ref;
}

/* Settings: a pop-up that opens above its button */
function renderSettings() {
  const s = state.settings;
  $("pill").textContent = summary();
  $("pill").setAttribute("aria-expanded", state.settingsOpen);
  const box = $("settings");
  box.hidden = !state.settingsOpen;
  if (!state.settingsOpen) return;
  // Fit between the top bar and the button.
  box.style.maxHeight = Math.max(240, $("pill").getBoundingClientRect().top - 12 - belowTop()) + "px";

  const T = tabOpt();
  const model = modelOf(s.model);
  const minSteps = model.minSteps || 1, stepSize = model.stepSize || 1;
  const recPct = ((model.steps - minSteps) / (model.maxSteps - minSteps)) * 100;
  if (isKlein()) { box.innerHTML = kleinSettingsHTML(s, model, T, minSteps, stepSize, recPct); return; }
  box.innerHTML = `
    <div class="field">
      <label class="label" for="model">Model</label>
      <select class="select" id="model" data-change="model">
        ${T.models.map((m) => `<option value="${esc(m.name)}" ${m.name === s.model ? "selected" : ""}>${esc(m.name)}</option>`).join("")}
      </select>
    </div>

    <div class="field">
      <span class="label">Aspect ratio</span>
      <div class="aspects ${isImage() ? "five" : ""}">
        ${Object.keys(T.aspects).map((a) => {
          const [w, h] = T.aspects[a];
          const k = 24 / Math.max(w, h);
          return `<button class="${a === s.aspect ? "on" : ""}" data-act="aspect" data-id="${a}"><i style="width:${Math.round(w * k)}px;height:${Math.round(h * k)}px"></i>${a}</button>`;
        }).join("")}
      </div>
    </div>

    ${isImage() ? "" : `<div class="field">
      <div class="field-head"><label class="label" for="len">Length</label><span class="value" id="lenVal">${s.len} s</span></div>
      <input id="len" type="range" min="${OPT.length.min}" max="${OPT.length.max}" step="1" value="${s.len}" data-input="len">
    </div>`}

    <div class="field">
      <label class="label" for="res">Resolution</label>
      <select class="select" id="res" data-change="res">
        ${T.megapixels.map((mp) => `<option value="${mp}" ${mp === s.res ? "selected" : ""}>${mpText(mp)} (${sizeText(s.aspect, mp)})${(T.recommendedMP || []).includes(mp) ? " · recommended" : ""}</option>`).join("")}
      </select>
    </div>

    <div class="field">
      <div class="field-head"><label class="label" for="steps">Steps</label><span class="value ${s.steps === model.steps ? "rec" : ""}" id="stepsVal">${stepsText(s.steps, model.steps)}</span></div>
      <div class="steps-wrap">
        <input id="steps" type="range" min="${minSteps}" max="${model.maxSteps}" step="${stepSize}" value="${s.steps}" data-input="steps" aria-describedby="stepsRec">
        <span class="rec-mark" style="left:calc(${recPct}% + ${(0.5 - recPct / 100) * 16}px)" aria-hidden="true"></span>
      </div>
      <div class="field-head steps-foot"><span class="hint" id="stepsRec">Recommended for ${esc(model.name)}: ${model.steps}${stepSize > 1 ? ` · takes ${minSteps}–${model.maxSteps} in steps of ${stepSize}` : ""}</span>
        <button class="link" data-act="recSteps" id="recBtn" style="visibility:${s.steps === model.steps ? "hidden" : "visible"}">Use ${model.steps}</button></div>
    </div>

    ${isImage() ? guidanceField(s, model) : ""}

    <div class="field">
      <span class="label">Seed</span>
      <div class="seed-row">
        <div class="seg">
          <button class="${s.seedMode === "random" ? "on" : ""}" data-act="seedMode" data-id="random">Random</button>
          <button class="${s.seedMode === "fixed" ? "on" : ""}" data-act="seedMode" data-id="fixed">Fixed</button>
        </div>
        ${s.seedMode === "fixed"
          ? `<input class="seed-input" inputmode="numeric" aria-label="Seed number" value="${s.seed}" data-change="seed">
             <button class="icon-btn" data-act="rollSeed" aria-label="New random seed">${ICON.dice}</button>`
          : `<span class="seed-note">A new seed every run, saved with each ${isImage() ? "image" : "clip"}.</span>`}
      </div>
    </div>

    ${lorasField(s)}

    ${samplingFields(s, model)}`;
}

// The Klein tab's settings: Model, Size (Match image 1 or an aspect ratio), Resolution, Steps, Guidance (Base
// only: Distilled renders without it), Seed, LoRAs (the loras/Flux Klein folder).
function kleinSettingsHTML(s, model, T, minSteps, stepSize, recPct) {
  const r1 = slotRef(0), [w, h] = kleinSize(s);
  const sizeOf = (mp) => kleinSize({ ...s, res: mp }).join("×");
  return `
    <div class="field">
      <label class="label" for="model">Model</label>
      <select class="select" id="model" data-change="model">
        ${(T.models.length ? T.models : [model]).map((m) => `<option value="${esc(m.name)}" ${m.name === s.model ? "selected" : ""}>${esc(m.name)}</option>`).join("")}
      </select>
      <span class="hint">${model.base ? "Undistilled: slower and more varied, with guidance." : "Distilled: fast, 4 steps, no guidance."}</span>
    </div>

    <div class="field">
      <span class="label">Size</span>
      <div class="aspects five klein-sizes">
        <button class="match ${s.size === "match" ? "on" : ""}" data-act="kleinMatch">Match image 1<small>${r1 ? (r1.w ? r1.w + "×" + r1.h : "image 1") : "image 1 is empty: 1:1"}</small></button>
        ${Object.keys(T.aspects).map((a) => {
          const [aw, ah] = T.aspects[a];
          const k = 24 / Math.max(aw, ah);
          return `<button class="${s.size !== "match" && a === s.aspect ? "on" : ""}" data-act="aspect" data-id="${a}"><i style="width:${Math.round(aw * k)}px;height:${Math.round(ah * k)}px"></i>${a}</button>`;
        }).join("")}
      </div>
    </div>

    <div class="field">
      <label class="label" for="res">Resolution</label>
      <select class="select" id="res" data-change="res">
        ${T.megapixels.map((mp) => `<option value="${mp}" ${mp === s.res ? "selected" : ""}>${mpText(mp)} (${sizeOf(mp)})${(T.recommendedMP || []).includes(mp) ? " · recommended" : ""}</option>`).join("")}
      </select>
      <span class="hint">Renders at ${w}×${h}${s.size === "match" && r1 ? ", image 1's shape" : ""}.</span>
    </div>

    <div class="field">
      <div class="field-head"><label class="label" for="steps">Steps</label><span class="value ${s.steps === model.steps ? "rec" : ""}" id="stepsVal">${stepsText(s.steps, model.steps)}</span></div>
      <div class="steps-wrap">
        <input id="steps" type="range" min="${minSteps}" max="${model.maxSteps}" step="${stepSize}" value="${s.steps}" data-input="steps" aria-describedby="stepsRec">
        <span class="rec-mark" style="left:calc(${recPct}% + ${(0.5 - recPct / 100) * 16}px)" aria-hidden="true"></span>
      </div>
      <div class="field-head steps-foot"><span class="hint" id="stepsRec">Recommended for ${esc(model.name)}: ${model.steps}</span>
        <button class="link" data-act="recSteps" id="recBtn" style="visibility:${s.steps === model.steps ? "hidden" : "visible"}">Use ${model.steps}</button></div>
    </div>

    ${model.base ? guidanceField(s, model) : ""}

    <div class="field">
      <span class="label">Seed</span>
      <div class="seed-row">
        <div class="seg">
          <button class="${s.seedMode === "random" ? "on" : ""}" data-act="seedMode" data-id="random">Random</button>
          <button class="${s.seedMode === "fixed" ? "on" : ""}" data-act="seedMode" data-id="fixed">Fixed</button>
        </div>
        ${s.seedMode === "fixed"
          ? `<input class="seed-input" inputmode="numeric" aria-label="Seed number" value="${s.seed}" data-change="seed">
             <button class="icon-btn" data-act="rollSeed" aria-label="New random seed">${ICON.dice}</button>`
          : `<span class="seed-note">A new seed every run, saved with each image.</span>`}
      </div>
    </div>

    ${lorasField(s)}`;
}

// Sampler and scheduler. "Model default" (the empty choice) uses what the model is tuned for.
function samplingFields(s, model) {
  const hyperflow = model.scheduler === "hyperflow";
  const schedulers = (OPT.schedulers || []).concat(isImage() || !OPT.beta57 ? [] : [OPT.beta57]);
  const opt = (value, current, label) => `<option value="${esc(value)}" ${value === current ? "selected" : ""}>${esc(label || value)}</option>`;
  return `<div class="field sampling">
      <div class="sampling-row">
        <div><label class="label" for="sampler">Sampler</label>
          <select class="select" id="sampler" data-change="sampler">
            ${opt("", s.sampler || "", "Model default (" + (model.sampler || "?") + ")")}
            ${(OPT.samplers || []).map((n) => opt(n, s.sampler || "")).join("")}
          </select></div>
        <div><label class="label" for="scheduler">Scheduler</label>
          <select class="select" id="scheduler" data-change="scheduler" ${hyperflow ? "disabled" : ""}>
            ${hyperflow ? opt("", "", "HyperFlow's own")
              : opt("", s.scheduler || "", "Model default (" + (model.scheduler || "?") + ")") + schedulers.map((n) => opt(n, s.scheduler || "")).join("")}
          </select></div>
      </div>
      ${hyperflow ? `<span class="hint">HyperFlow samples on its own 4, 6 or 8-step schedule.</span>` : ""}
    </div>`;
}

function lorasField(s) {
  return `<div class="field">
      <div class="field-head"><span class="label">LoRAs</span><button class="link" data-act="loraPicker">+ Add LoRA</button></div>
      ${s.loras.map((l) => `<div class="lora ${l.on ? "" : "dim"}">
          <button class="switch ${l.on ? "on" : ""}" data-act="toggleLora" data-id="${esc(l.id)}" aria-label="${l.on ? "Switch off" : "Switch on"} ${esc(l.name)}" aria-pressed="${l.on}"></button>
          <div class="lora-body">
            <span class="lora-name" title="${esc(l.name)}">${esc(loraStem(l.name))}</span>
            <input type="range" min="0" max="2" step="0.05" value="${l.strength}" data-input="loraStrength" data-id="${esc(l.id)}" aria-label="Strength of ${esc(l.name)}">
          </div>
          <span class="lora-val" data-strength="${esc(l.id)}">${Number(l.strength).toFixed(2)}</span>
          <button class="lora-x" data-act="removeLora" data-id="${esc(l.id)}" aria-label="Remove ${esc(l.name)}">${ICON.x}</button>
        </div>`).join("")}
    </div>`;
}

// Guidance (CFG): how closely MOSS-Image follows the prompt. The recommended value is marked on the slider.
function guidanceField(s, model) {
  const g = (isKlein() ? OPT.klein.cfg : OPT.image.cfg) || OPT.image.cfg, rec = model.cfg;
  const pct = ((rec - g.min) / (g.max - g.min)) * 100;
  return `<div class="field">
      <div class="field-head"><label class="label" for="cfg">Guidance</label><span class="value ${s.cfg === rec ? "rec" : ""}" id="cfgVal">${cfgText(s.cfg, rec)}</span></div>
      <div class="steps-wrap">
        <input id="cfg" type="range" min="${g.min}" max="${g.max}" step="${g.step}" value="${s.cfg}" data-input="cfg" aria-describedby="cfgRec">
        <span class="rec-mark" style="left:calc(${pct}% + ${(0.5 - pct / 100) * 16}px)" aria-hidden="true"></span>
      </div>
      <div class="field-head steps-foot"><span class="hint" id="cfgRec">How closely the image follows the prompt. Recommended: ${rec}</span>
        <button class="link" data-act="recCfg" id="cfgBtn" style="visibility:${s.cfg === rec ? "hidden" : "visible"}">Use ${rec}</button></div>
    </div>`;
}
const cfgText = (n, rec) => Number(n).toFixed(1) + (n === rec ? " · recommended" : "");

/* Clip page */
// Full screen covers the clip page and hides everything but the player, so redrawing keeps it.
const isFull = () => document.fullscreenElement === $("clipView");
let shownClip = null;   // which clip (and in which state) the player shows, so it is only rebuilt when that changes

function renderClip() {
  const c = clipById(state.openId);
  if (!c) { state.view = "gallery"; render(); return; }
  $("clipView").classList.toggle("fs", isFull());
  const key = c.id + ":" + c.status + ":" + (c.file || c.url || "") + ":" + Boolean(isBusy(c) && c.preview);
  if (shownClip !== key && kindOf(c) === "image") {
    shownClip = key;
    $("clipView").innerHTML = `
      <div class="clip-main">
        <div class="player still" data-clip="${c.id}">
          <span class="pic" style="${picStyle(shapeOf(c), toneOf(c.id))}">${isBusy(c) && c.preview ? `<img data-live="${c.id}" src="${c.preview}" alt="">` : c.status === "done" ? `<img id="pimg" src="${clipUrl(c)}" alt="${esc(c.prompt)}">` : ""}</span>
          ${isBusy(c)
            ? `<span class="busy ${c.preview ? "has-live" : ""}"><span data-progtext="${c.id}">${progressText(c)}</span><span class="track"><span class="fill" data-prog="${c.id}" style="width:${barWidth(c)}"></span></span>
                <button class="cancel" data-act="cancel" data-id="${c.id}">Cancel render</button></span>`
            : c.status === "failed"
              ? `<span class="busy failed"><span>Render failed</span><span class="fail-why">${esc(c.error || "")}</span></span>`
              : `<button class="fs-btn still-fs" data-act="fullscreen" aria-label="Full screen">${ICON.expand}</button>`}
          <span class="badge">${mpText(c.res)} (${c.width}×${c.height})</span>
        </div>
        <span class="label takes-label">Other images from this prompt</span>
        <div class="takes" id="takes"></div>
      </div>
      <aside class="clip-side" id="clipSide"></aside>`;
  } else if (shownClip !== key) {
    shownClip = key;
    $("clipView").innerHTML = `
      <div class="clip-main">
        <div class="player" data-clip="${c.id}">
          <span class="pic" style="${picStyle(c.aspect, toneOf(c.id))}">${isBusy(c) && c.preview ? liveVideo(c) : c.file ? `<video id="pv" src="${clipUrl(c)}" loop playsinline preload="auto"></video>` : ""}</span>
          ${isBusy(c)
            ? `<span class="busy ${c.preview ? "has-live" : ""}"><span data-progtext="${c.id}">${progressText(c)}</span><span class="track"><span class="fill" data-prog="${c.id}" style="width:${barWidth(c)}"></span></span>
                <button class="cancel" data-act="cancel" data-id="${c.id}">Cancel render</button></span>`
            : c.status === "failed"
              ? `<span class="busy failed"><span>Render failed</span><span class="fail-why">${esc(c.error || "")}</span></span>`
              : `<div class="controls">
                  <button class="play" id="playBtn" data-act="play" aria-label="Play">${ICON.play}</button>
                  <span class="scrub" id="scrub"><span id="playFill" style="width:0%"></span></span>
                  <span class="time" id="playTime">0:00 / ${clock(c.len)}</span>
                  <button class="fs-btn" data-act="fullscreen" aria-label="Full screen">${ICON.expand}</button>
                </div>`}
          <span class="badge">${mpText(c.res)} (${c.width}×${c.height})</span>
        </div>
        <span class="label takes-label">Takes of this prompt</span>
        <div class="takes" id="takes"></div>
      </div>
      <aside class="clip-side" id="clipSide"></aside>`;
    wirePlayer();
    $("pv")?.play().catch(() => {});
  }
  renderTakes(c);
  renderClipSide(c);
  syncFullscreenButton();
}

function renderTakes(c) {
  const takes = state.clips.filter((x) => x.prompt === c.prompt && tabOf(x) === tabOf(c));
  $("takes").innerHTML = takes.map((x) => `<button class="take ${x.id === c.id ? "on" : ""}" data-act="open" data-id="${x.id}">
      <i style="background:${toneOf(x.id)}">${kindOf(x) === "image" ? (x.status === "done" ? `<img src="${clipUrl(x)}" alt="">` : "") : x.file ? `<video src="${clipUrl(x)}" muted preload="metadata"></video>` : ""}</i>${mpText(x.res)} · Seed ${x.seed}<span data-takeprog="${x.id}">${takeProgress(x)}</span>
    </button>`).join("");
}

function renderClipSide(c) {
  if (kindOf(c) === "image") { renderImageSide(c); return; }
  $("clipSide").innerHTML = `
    <div class="field">
      <span class="label">Prompt</span>
      <div class="prompt-box"><p class="clip-prompt">${promptHTML(c.prompt)}</p>
        <button class="copy prompt-copy" data-act="copyPrompt" aria-label="Copy the prompt" title="Copy the prompt">${ICON.copy}</button></div>
    </div>
    <dl class="facts">
      <dt>Model</dt><dd>${esc(c.model)}</dd>
      <dt>Steps</dt><dd>${c.steps}</dd>
      <dt>Resolution</dt><dd>${mpText(c.res)} (${c.width}×${c.height})</dd>
      <dt>Aspect</dt><dd>${c.aspect}</dd>
      <dt>Length</dt><dd>${c.len} s · ${snapFrames(c.len)} frames</dd>
      <dt>Seed</dt><dd class="seed-dd">${c.seed}<button class="copy" data-act="copySeed" aria-label="Copy seed">${ICON.copy}</button></dd>
      <dt>LoRAs</dt><dd>${c.loras.length ? c.loras.map((l) => esc(loraText(l))).join("<br>") : '<span class="label">None</span>'}</dd>
      ${samplingFacts(c)}
    </dl>
    ${c.imported ? `<p class="hint imported">Imported from ${esc(c.imported)}${c.found === "graph" ? ". Settings worked out from its ComfyUI workflow" : ""}.</p>` : ""}
    <div class="actions">
      <button class="btn primary" data-act="reuse">Use this prompt and settings</button>
      <button class="btn" data-act="keepSeed">Keep this seed</button>
      <div class="actions-grid">
        <button class="btn" data-act="download" ${c.file ? "" : "disabled"}>Download</button>
        <button class="btn" data-act="exportZip" ${c.file && c.status === "done" ? "" : "disabled"} title="The video and its reference images in one .zip. Import it here or in another MiniMax H3 Vision.">Export .zip</button>
        <button class="btn danger wide" data-act="deleteClip">Delete</button>
      </div>
    </div>`;
}

// A clip's sampler and scheduler: its own choice, or (in grey) its model's.
function samplingFacts(c) {
  const models = kindOf(c) === "image" ? OPT.image.models : OPT.models;
  const m = models.find((x) => x.name === c.model) || {};
  const one = (own, dflt) => own ? esc(own) : dflt ? `${esc(dflt === "hyperflow" ? "HyperFlow's own" : dflt)} <span class="label">· model default</span>` : '<span class="label">Model default</span>';
  return `<dt>Sampler</dt><dd>${one(c.sampler, m.sampler)}</dd><dt>Scheduler</dt><dd>${one(c.scheduler, m.scheduler)}</dd>`;
}
// Images saved before the negative prompt box existed used the model's own.
const negativeDefault = (modelName) => (OPT.image.models.find((m) => m.name === modelName) || OPT.image.models[0] || {}).negative || "";

function renderImageSide(c) {
  if (c.family === "klein") { renderKleinSide(c); return; }
  $("clipSide").innerHTML = `
    <div class="field">
      <span class="label">Prompt</span>
      <div class="prompt-box"><p class="clip-prompt">${esc(c.prompt)}</p>
        <button class="copy prompt-copy" data-act="copyPrompt" aria-label="Copy the prompt" title="Copy the prompt">${ICON.copy}</button></div>
    </div>
    <dl class="facts">
      <dt>Model</dt><dd>${esc(c.model)}</dd>
      <dt>Steps</dt><dd>${c.steps}</dd>
      <dt>Guidance</dt><dd>${Number(c.cfg).toFixed(1)}</dd>
      <dt>Resolution</dt><dd>${mpText(c.res)} (${c.width}×${c.height})</dd>
      <dt>Aspect</dt><dd>${c.aspect}</dd>
      <dt>Seed</dt><dd class="seed-dd">${c.seed}<button class="copy" data-act="copySeed" aria-label="Copy seed">${ICON.copy}</button></dd>
      <dt>LoRAs</dt><dd>${c.loras.length ? c.loras.map((l) => esc(loraText(l))).join("<br>") : '<span class="label">None</span>'}</dd>
      ${samplingFacts(c)}
      <dt>Negative</dt><dd>${c.negative != null ? (c.negative ? esc(c.negative) : '<span class="label">None</span>') : esc(negativeDefault(c.model)) || '<span class="label">None</span>'}</dd>
    </dl>
    ${c.imported ? `<p class="hint imported">Imported from ${esc(c.imported)}${c.found === "graph" ? ". Settings worked out from its ComfyUI workflow" : ""}.</p>` : ""}
    <div class="actions">
      <button class="btn accent" data-act="useInVideo" ${c.status === "done" ? "" : "disabled"}>${ICON.video}Use in a video</button>
      <button class="btn" data-act="editThis" ${c.status === "done" ? "" : "disabled"}>${ICON.klein}Edit in Klein</button>
      <button class="btn primary" data-act="reuse">Use this prompt and settings</button>
      <button class="btn" data-act="keepSeed">Keep this seed</button>
      <div class="actions-grid">
        <button class="btn" data-act="download" ${c.status === "done" ? "" : "disabled"}>Download</button>
        <button class="btn danger" data-act="deleteClip">Delete</button>
      </div>
    </div>`;
}

// A Klein image's page: its prompt, the pictures it was made from (in their slots) and its settings.
function renderKleinSide(c) {
  const slots = (c.slots || []).concat([null, null, null, null]).slice(0, 4);
  const done = c.status === "done";
  $("clipSide").innerHTML = `
    <div class="field">
      <span class="label">Prompt</span>
      <div class="prompt-box"><p class="clip-prompt">${promptHTML(c.prompt, "klein", slots)}</p>
        <button class="copy prompt-copy" data-act="copyPrompt" aria-label="Copy the prompt" title="Copy the prompt">${ICON.copy}</button></div>
    </div>
    <div class="field">
      <span class="label">Pictures</span>
      <div class="side-slots">${slots.map((x, i) => {
        const r = x && state.refs.find((y) => y.id === x.id);
        if (r) refSize(r);
        return `<div class="side-slot ${x ? "" : "vacant"}" title="${x ? "image " + (i + 1) + ": @" + esc(x.name) : "image " + (i + 1) + " was empty"}">
          <span class="side-pic" style="background:${r ? r.tone : "transparent"}">${r ? `<img src="${r.img}" alt="">` : x ? "?" : ""}</span>
          <span class="side-name">image ${i + 1}</span><span class="slot-res">${r && r.w ? r.w + "×" + r.h : x ? "@" + esc(x.name) : "empty"}</span></div>`;
      }).join("")}</div>
    </div>
    <dl class="facts">
      <dt>Model</dt><dd>${esc(c.model)}</dd>
      <dt>Steps</dt><dd>${c.steps}</dd>
      <dt>Guidance</dt><dd>${Number(c.cfg).toFixed(1)}</dd>
      <dt>Size</dt><dd>${c.size && c.size !== "match" ? esc(c.size) : "Match image 1"} · ${c.width}×${c.height} (${mpText(c.res)})</dd>
      <dt>Seed</dt><dd class="seed-dd">${c.seed}<button class="copy" data-act="copySeed" aria-label="Copy seed">${ICON.copy}</button></dd>
      <dt>LoRAs</dt><dd>${c.loras.length ? c.loras.map((l) => esc(loraText(l))).join("<br>") : '<span class="label">None</span>'}</dd>
    </dl>
    ${c.imported ? `<p class="hint imported">Imported from ${esc(c.imported)}${c.found === "graph" ? ". Settings worked out from its ComfyUI workflow" : ""}.</p>` : ""}
    <div class="actions">
      <button class="btn accent" data-act="editThis" ${done ? "" : "disabled"}>${ICON.klein}Edit this</button>
      <button class="btn" data-act="useInVideo" ${done ? "" : "disabled"}>${ICON.video}Use in a video</button>
      <button class="btn primary" data-act="reuse">Use this prompt and settings</button>
      <button class="btn" data-act="keepSeed">Keep this seed</button>
      <div class="actions-grid">
        <button class="btn" data-act="download" ${done ? "" : "disabled"}>Download</button>
        <button class="btn danger" data-act="deleteClip">Delete</button>
      </div>
    </div>`;
}

// The player: a looping <video> with our own controls.
function wirePlayer() {
  const v = $("pv");
  if (!v) return;
  const c = clipById(state.openId);
  // Hold on to this player's own controls: the video can still fire events after the page has moved on.
  const btn = $("playBtn"), fill = $("playFill"), time = $("playTime");
  const update = () => {
    const d = v.duration || c.len;
    fill.style.width = (v.currentTime / d) * 100 + "%";
    time.textContent = clock(v.currentTime) + " / " + clock(d);
  };
  v.addEventListener("timeupdate", update);
  v.addEventListener("loadedmetadata", update);
  v.addEventListener("play", () => { btn.innerHTML = ICON.pause; btn.setAttribute("aria-label", "Pause"); });
  v.addEventListener("pause", () => { btn.innerHTML = ICON.play; btn.setAttribute("aria-label", "Play"); });
  // A click on the picture plays or pauses it (a double-click is full screen).
  v.addEventListener("click", () => { if (v.paused) v.play().catch(() => {}); else v.pause(); });
  $("scrub").addEventListener("pointerdown", (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (v.duration) v.currentTime = ((e.clientX - box.left) / box.width) * v.duration;
  });
}

function syncFullscreenButton() {
  const b = document.querySelector(".fs-btn");
  if (!b) return;
  b.innerHTML = isFull() ? ICON.shrink : ICON.expand;
  b.setAttribute("aria-label", isFull() ? "Exit full screen" : "Full screen");
}

/* ------------------------------------------------------------------
   Clips from the server: new ones, progress and finished files
   ------------------------------------------------------------------ */
function upsertClip(clip) {
  if (deleted.has(clip.id)) return;
  const old = clipById(clip.id);
  if (!old) {
    state.clips.unshift(clip);
    render();
    return;
  }
  const changed = old.status !== clip.status || old.file !== clip.file;
  // The server doesn't keep the live step or preview of a real render; those come straight from ComfyUI.
  const live = { step: old.step || 0, preview: old.preview };
  Object.assign(old, clip);
  if (isBusy(old)) { old.step = Math.max(live.step, clip.step || 0); old.preview = live.preview; }
  else {
    old.preview = null;
    if (live.preview && live.preview.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(live.preview), 3000);
  }
  if (changed) {
    if (state.view === "gallery" && state.tab === "edit") { renderHeader(); EDIT.clipsChanged(); }
    else if (state.view === "gallery") { renderHeader(); renderFilters(); renderGrid(); } else renderClip();
    return;
  }
  showProgress(old);
}

// Only the step moved: update the few things that show it, so nothing under the pointer is redrawn.
function showProgress(clip) {
  document.querySelectorAll(`[data-prog="${clip.id}"]`).forEach((el) => { el.style.width = barWidth(clip); });
  document.querySelectorAll(`[data-progtext="${clip.id}"]`).forEach((el) => { el.textContent = progressText(clip); });
  document.querySelectorAll(`[data-takeprog="${clip.id}"]`).forEach((el) => { el.textContent = takeProgress(clip); });
}

/* A real render's steps and its moving preview come from ComfyUI as it samples. */
const SAMPLER_NODE = "15";

function onProgress(d) {
  const clip = state.clips.find((c) => c.prompt_id && c.prompt_id === d.prompt_id);
  if (!clip || !isBusy(clip) || String(d.node) !== SAMPLER_NODE) return;
  const wasQueued = clip.status === "queued";
  clip.status = "rendering";
  clip.step = Math.max(clip.step || 0, Number(d.value) || 0);
  if (wasQueued) redrawClip(); else showProgress(clip);
}

// The live preview of a video: each step, a short clip of the whole video so far (MMVisionLivePreview).
function liveVideo(c) {
  return `<video data-live="${c.id}" src="${c.preview}" autoplay loop muted playsinline></video>`;
}

function onPreview(d) {
  const clip = state.clips.find((c) => c.prompt_id && c.prompt_id === d.prompt_id);
  if (!clip || !isBusy(clip) || !d.filename) return;
  const first = !clip.preview;
  clip.preview = fileUrl(d.subfolder, d.filename);
  clip.status = "rendering";
  if (d.step) clip.step = Math.max(clip.step || 0, Math.min(Number(d.step), clip.steps));
  if (first) { redrawClip(); return; }
  document.querySelectorAll(`[data-live="${clip.id}"]`).forEach((v) => { v.src = clip.preview; v.play?.().catch(() => {}); });
  showProgress(clip);
}

// A live preview picture for any render (an image's preview arrives as a picture, not a file).
function setLivePreview(clip, url, step) {
  if (!clip || !isBusy(clip)) return;
  const first = !clip.preview;
  clip.preview = url;
  clip.status = "rendering";
  if (step) clip.step = Math.max(clip.step || 0, Math.min(step, clip.steps));
  if (first) { redrawClip(); return; }
  document.querySelectorAll(`[data-live="${clip.id}"]`).forEach((el) => { el.src = url; });
  showProgress(clip);
}

// ComfyUI's live preview of an image while it samples: a small JPEG, sent as a binary message.
// Type 4 carries the job's prompt id; the older type 1 doesn't, so it goes to the image that is rendering.
const PREVIEW_IMAGE = 1, PREVIEW_IMAGE_WITH_METADATA = 4;

function onPreviewPicture(buf) {
  if (buf.byteLength < 8) return;
  const view = new DataView(buf);
  const event = view.getUint32(0);
  let clip, bytes, type = "image/jpeg";
  if (event === PREVIEW_IMAGE_WITH_METADATA) {
    const size = view.getUint32(4);
    let meta = {};
    try { meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, size))); } catch { return; }
    clip = state.clips.find((c) => c.prompt_id && c.prompt_id === meta.prompt_id);
    bytes = buf.slice(8 + size);
    type = meta.image_type || type;
  } else if (event === PREVIEW_IMAGE) {
    clip = state.clips.find((c) => kindOf(c) === "image" && c.status === "rendering" && c.prompt_id);
    bytes = buf.slice(8);
    if (view.getUint32(4) === 2) type = "image/png";
  } else return;
  // Videos have their own moving preview (mmvision_preview); these pictures are for images only.
  if (!clip || kindOf(clip) !== "image" || !isBusy(clip)) return;
  const old = clip.preview;
  setLivePreview(clip, URL.createObjectURL(new Blob([bytes], { type })));
  if (old && old.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(old), 3000);
}

function redrawClip() {
  if (state.view === "gallery") { renderHeader(); renderGrid(); } else renderClip();
}

function connect() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws?clientId=mmflow-" + Math.random().toString(36).slice(2));
  ws.binaryType = "arraybuffer";
  // Ask ComfyUI to say which job each preview picture belongs to.
  ws.addEventListener("open", () => ws.send(JSON.stringify({ type: "feature_flags", data: { supports_preview_metadata: true } })));
  ws.addEventListener("message", (e) => {
    if (e.data instanceof ArrayBuffer) { onPreviewPicture(e.data); return; }
    if (typeof e.data !== "string") return;
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "mmflow.clip" && msg.data?.clip) upsertClip(msg.data.clip);
    else if (msg.type === "progress" && msg.data) onProgress(msg.data);
    else if (msg.type === "mmvision_preview" && msg.data) onPreview(msg.data);
    else if (msg.type === "mmvision_export" && msg.data) EDIT.onExport(msg.data);
  });
  // ComfyUI restarted or the connection dropped: reconnect, and reload the state in case something finished meanwhile.
  ws.addEventListener("close", () => setTimeout(() => { connect(); load(false); }, 2000));
}

async function generate() {
  if (!state.ready) { say("MiniMax H3 Vision hasn't loaded from the server yet."); return; }
  const s = state.settings;
  const prompt = state.prompt.trim();
  if (!prompt) { say("Write a prompt first."); return; }
  const names = new Set(state.refs.map((r) => r.name));
  const unknown = isImage() || isKlein() ? null : mentionsIn(prompt).find((n) => !names.has(n));
  if (unknown) { say("There is no image called @" + unknown + "."); return; }
  const empty = isKlein() ? emptyNamed(prompt) : [];
  if (empty.length) { say(`The prompt names ${empty.map((n) => "image " + n).join(" and ")}, but ${empty.length > 1 ? "those slots are" : "that slot is"} empty. Fill it, or change the prompt.`); return; }
  state.settingsOpen = false;
  state.filter = "all";
  render();
  try {
    const { clip } = await api("POST", "generate", { prompt, settings: s, kind: state.tab, negative: isImage() ? state.negative : undefined,
      slots: isKlein() ? state.kleinSlots : undefined });
    upsertClip(clip);
  } catch (error) {
    say(error.message);
  }
}

/* ------------------------------------------------------------------
   Hover preview in the grid: the clip plays and loops while the pointer is over it,
   and goes back to its first frame when the pointer leaves.
   ------------------------------------------------------------------ */
const hover = { id: null };

function hoverStart(id) {
  const c = clipById(id);
  if (!c || !c.file || kindOf(c) === "image" || hover.id === id) return;
  hoverStop();
  hover.id = id;
  requestAnimationFrame(hoverFrame);
}

function hoverStop() {
  if (!hover.id) return;
  const id = hover.id;
  hover.id = null;
  const tile = document.querySelector(`.tile[data-clip="${id}"]`);
  if (!tile) return;
  const v = tile.querySelector("video");
  if (v) { v.pause(); v.currentTime = 0; }
  const bar = tile.querySelector("[data-hoverbar]");
  if (bar) bar.style.width = "0";
  const c = clipById(id);
  if (c) tile.querySelector("[data-len]").textContent = c.len + " s";
  tile.classList.remove("hovering");
}

function hoverFrame() {
  if (!hover.id) return;
  // Look the tile up each frame, so a redraw of the grid doesn't stop the preview.
  const tile = document.querySelector(`.tile[data-clip="${hover.id}"]`);
  const v = tile?.querySelector("video");
  if (!v) { hover.id = null; return; }
  tile.classList.add("hovering");
  if (v.paused) v.play().catch(() => {});
  const d = v.duration || clipById(hover.id).len;
  tile.querySelector("[data-hoverbar]").style.width = (v.currentTime / d) * 100 + "%";
  tile.querySelector("[data-len]").textContent = clock(v.currentTime) + " / " + clock(d);
  requestAnimationFrame(hoverFrame);
}

document.addEventListener("mouseover", (e) => {
  const tile = e.target.closest(".tile[data-clip]");
  if (tile) hoverStart(tile.dataset.clip);
  else hoverStop();
});
document.addEventListener("mouseleave", hoverStop);

/* ------------------------------------------------------------------
   Right-click menu on a clip
   ------------------------------------------------------------------ */
let menuFor = null;

function openMenu(id, x, y) {
  const c = clipById(id);
  if (!c) return;
  menuFor = id;
  const m = $("ctx");
  m.innerHTML = `
    <button role="menuitem" data-act="menuDownload" ${c.status === "done" ? "" : 'aria-disabled="true" class="off"'}>${ICON.download}Download</button>${kindOf(c) === "video" ? `
    <button role="menuitem" data-act="menuExportZip" ${c.status === "done" && c.file ? "" : 'aria-disabled="true" class="off"'}>${ICON.download}Export .zip with reference images</button>` : ""}
    <button role="menuitem" data-act="menuDelete" class="danger">${ICON.trash}${isBusy(c) ? "Cancel and delete" : "Delete"}</button>
    <span class="ctx-line"></span>${kindOf(c) === "video" ? (EDIT.has(c.id)
      ? `<button role="menuitem" aria-disabled="true" class="off" title="This video is on the edit's timeline already.">${ICON.check}Already in the edit</button>`
      : `<button role="menuitem" data-act="menuAddToEdit" ${c.status === "done" && c.file ? "" : 'aria-disabled="true" class="off"'}>${ICON.edit}Add to edit</button>`) : ""}
    <button role="menuitem" data-act="menuReuse">${ICON.reuse}Use this prompt and settings</button>${kindOf(c) === "image" ? `
    <button role="menuitem" data-act="menuUseInVideo" ${c.status === "done" ? "" : 'aria-disabled="true" class="off"'}>${ICON.video}Use in a video</button>
    <button role="menuitem" data-act="menuEditThis" ${c.status === "done" ? "" : 'aria-disabled="true" class="off"'}>${ICON.klein}${tabOf(c) === "klein" ? "Edit this (image 1)" : "Edit in Klein (image 1)"}</button>` : ""}`;
  m.hidden = false;
  // Keep the whole menu inside the window.
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
  m.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
  m.querySelector("button:not(.off)").focus();
}

function closeMenu() {
  const id = menuFor;
  menuFor = null;
  $("ctx").hidden = true;
  return id;
}

document.addEventListener("contextmenu", (e) => {
  const host = e.target.closest("[data-clip]");
  if (!host) { closeMenu(); return; }
  e.preventDefault();
  // From the keyboard (menu key or Shift+F10) there is no pointer, so open at the clip.
  const keyboard = e.clientX === 0 && e.clientY === 0;
  const box = host.getBoundingClientRect();
  openMenu(host.dataset.clip, keyboard ? box.left + 16 : e.clientX, keyboard ? box.top + 16 : e.clientY);
});
document.addEventListener("pointerdown", (e) => { if (menuFor && !e.target.closest("#ctx")) closeMenu(); });
window.addEventListener("scroll", () => menuFor && closeMenu(), true);
window.addEventListener("blur", () => menuFor && closeMenu());
$("ctx").addEventListener("keydown", (e) => {
  const items = [...$("ctx").querySelectorAll("button:not(.off)")];
  const i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); closeMenu(); }
});

/* ------------------------------------------------------------------
   Actions. Buttons name one with data-act; inputs with data-change or data-input.
   ------------------------------------------------------------------ */
function openClip(id) {
  hoverStop();
  state.view = "clip";
  state.openId = id;
  state.settingsOpen = false;
  state.library.open = false;
  shownClip = null;
  render();
  window.scrollTo(0, 0);
}

async function removeClip(id, message) {
  deleted.add(id);
  state.clips = state.clips.filter((x) => x.id !== id);
  if (state.openId === id) { state.view = "gallery"; state.openId = null; }
  render();
  try {
    await api("DELETE", "clips/" + id);
    say(message);
  } catch (error) {
    say("Couldn't delete: " + error.message);
  }
}

/* ------------------------------------------------------------------
   Add LoRA: a window listing the LoRA files in ComfyUI's loras folders
   ------------------------------------------------------------------ */
const LM = { open: false, q: "", sel: 0, sizes: {}, busy: false, all: false };
// LoRAs are sorted by model in folders inside models/loras: "MiniMax H3" for the Video tab, "MOSS Image" for the
// Image tab, "Flux Klein" for Klein. Each tab lists its own folder and the loose files in loras itself
// ("Not sorted"); Show all lists every folder. Folder names are matched loosely (minimax_h3, moss, klein…).
const LORA_FOLDER = { video: "MiniMax H3", image: "MOSS Image", klein: "Flux Klein" };
const loraTop = (n) => { const p = n.replace(/\\/g, "/"); return p.includes("/") ? p.slice(0, p.indexOf("/")) : ""; };
function loraFamily(n) {
  const top = loraTop(n).toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!top) return "";
  if (top.startsWith("minimax") || top.startsWith("h3") || top === "hyperflow") return "video";
  if (top.startsWith("moss")) return "image";
  if (top.includes("klein") || top.startsWith("flux")) return "klein";
  return "other";
}
const loraTab = () => (isImage() ? "image" : isKlein() ? "klein" : "video");
const loraBase = (n) => n.replace(/\\/g, "/").split("/").pop();
const loraFolder = (n) => (n.includes("/") ? n.slice(0, n.lastIndexOf("/")) : n.includes("\\") ? n.slice(0, n.lastIndexOf("\\")) : "");
const loraStem = (n) => n.slice(Math.max(n.lastIndexOf("/"), n.lastIndexOf("\\")) + 1).replace(/\.[^.]+$/, "");
const loraSize = (b) => (b >= 1 << 30 ? (b / (1 << 30)).toFixed(1) + " GB" : b >= 1 << 20 ? Math.round(b / (1 << 20)) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
// The LoRAs to list, in groups: this tab's folder, then Not sorted, then (with Show all) other models' folders.
// A folder that isn't one of the model folders counts as Not sorted too: only another model's LoRAs are hidden.
const loraRank = (n) => { const f = loraFamily(n); return f === loraTab() ? 0 : f === "" || f === "other" ? 1 : 2; };
const loraShown = () => {
  const words = LM.q.toLowerCase().split(/\s+/).filter(Boolean);
  return OPT.loras
    .filter((n) => LM.all || loraRank(n) < 2)
    .filter((n) => words.every((w) => n.toLowerCase().includes(w)))
    .sort((a, b) => loraRank(a) - loraRank(b) || a.toLowerCase().localeCompare(b.toLowerCase()));
};
// A LoRA in the settings counts as this file when the name is the same, or the file name is (it was moved).
const loraUsed = (n) => state.settings.loras.some((l) => l.id === n || loraBase(l.id) === loraBase(n));
function loraHeading(n) {
  const rank = loraRank(n), parts = n.replace(/\\/g, "/").split("/").slice(0, -1);
  if (rank === 1) return parts.length ? parts.join(" / ") + " · not sorted" : "Not sorted · loose in the loras folder";
  if (rank === 0) return parts.join(" / ");
  return parts.join(" / ") + " · for another model";
}

function openLoras() {
  LM.open = true; LM.q = ""; LM.sel = 0; LM.all = false;
  $("loraModal").hidden = false;
  renderLoras(true);
  $("loraQuery").focus();
  fetchLoras(false);
}
function closeLoras() {
  if (!LM.open) return;
  LM.open = false;
  $("loraModal").hidden = true;
  render();
  document.querySelector('[data-act="loraPicker"]')?.focus();
}
async function fetchLoras(refresh) {
  if (LM.busy) return;
  LM.busy = true; renderLoraHead();
  const before = new Set(OPT.loras);
  try {
    const r = await api("GET", "loras" + (refresh ? "?refresh=1" : ""));
    OPT.loras = r.loras; LM.sizes = r.sizes || {};
    if (refresh) {
      const added = r.loras.filter((n) => !before.has(n)).length, gone = [...before].filter((n) => !r.loras.includes(n)).length;
      say(`Found ${r.loras.length} LoRA${r.loras.length === 1 ? "" : "s"}` + (added ? `, ${added} new` : "") + (gone ? `, ${gone} gone` : "") + (added || gone ? "." : ". Nothing changed."));
    }
  } catch (error) {
    if (refresh) say("Couldn't look for LoRAs: " + error.message);
  }
  LM.busy = false;
  if (LM.open) {
    renderLoras();
    if (!$("loraModal").contains(document.activeElement)) (refresh ? $("loraRefresh") : $("loraQuery")).focus();
  }
}
function toggleLoraFile(name) {
  if (loraUsed(name)) state.settings.loras = state.settings.loras.filter((l) => l.id !== name && loraBase(l.id) !== loraBase(name));
  else state.settings.loras.push({ id: name, name, strength: 1, on: true });
  const list = loraShown();
  LM.sel = Math.max(0, list.indexOf(name));
  render();
  renderLoras();
  if (!$("loraModal").contains(document.activeElement)) $("loraQuery").focus();
}
function renderLoraHead() {
  const b = $("loraRefresh");
  if (!b) return;
  b.disabled = LM.busy;
  b.classList.toggle("spin", LM.busy);
  b.lastChild.textContent = LM.busy ? "Looking…" : "Refresh";
}
function renderLoras(first) {
  if (first) $("loraQuery").value = "";
  renderLoraHead();
  const list = loraShown();
  LM.sel = Math.min(LM.sel, Math.max(0, list.length - 1));
  const n = state.settings.loras.length, folder = LORA_FOLDER[loraTab()];
  const own = OPT.loras.filter((x) => loraRank(x) === 0).length, others = OPT.loras.filter((x) => loraRank(x) === 2).length;
  $("loraTitle").textContent = "Add a LoRA · " + folder;
  $("loraCount").textContent = `${own} in loras/${folder} · ${n} in use`;
  $("loraAll").checked = LM.all;
  $("loraAllText").textContent = `Show all LoRAs${others ? ` (${others} for other models)` : ""}`;
  const tip = `<p class="lm-empty">Put this tab's LoRAs in <code>models/loras/${esc(folder)}</code> and click Refresh.</p>`;
  if (!OPT.loras.length) {
    $("loraList").innerHTML = `<p class="lm-empty">No LoRAs in ComfyUI's loras folder yet.</p>` + tip;
    return;
  }
  if (!list.length) {
    $("loraList").innerHTML = LM.q ? `<p class="lm-empty">No LoRA matches “${esc(LM.q)}”.</p>` : `<p class="lm-empty">No LoRAs for ${esc(folder)} yet.</p>` + tip;
    return;
  }
  let heading = null, html = "";
  list.forEach((name, i) => {
    const h = loraHeading(name);
    if (h !== heading) { heading = h; if (h) html += `<div class="lm-folder ${loraRank(name) ? "other" : ""}">${esc(h)}</div>`; }
    const on = loraUsed(name);
    html += `<button class="lm-item ${on ? "on" : ""} ${i === LM.sel ? "sel" : ""}" role="option" aria-selected="${i === LM.sel}" data-act="pickLora" data-id="${esc(name)}" title="${esc(name)}">
      <span class="lm-name">${esc(loraStem(name))}</span>
      <span class="lm-size">${LM.sizes[name] ? loraSize(LM.sizes[name]) : ""}</span>
      <span class="lm-state">${on ? ICON.check + "Added" : ICON.plus + "Add"}</span></button>`;
  });
  $("loraList").innerHTML = html;
  $("loraList").querySelector(".lm-item.sel")?.scrollIntoView({ block: "nearest" });
}
$("loraQuery").addEventListener("input", (e) => { LM.q = e.target.value; LM.sel = 0; renderLoras(); });
$("loraAll").addEventListener("change", (e) => { LM.all = e.target.checked; LM.sel = 0; renderLoras(); $("loraQuery").focus(); });
$("loraModal").addEventListener("mousedown", (e) => { if (e.target === $("loraModal")) closeLoras(); });
// Capture, so the window has its keys wherever the focus is, and Escape closes it and nothing else.
document.addEventListener("keydown", (e) => {
  if (!LM.open) return;
  const list = loraShown();
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeLoras(); }
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    LM.sel = Math.max(0, Math.min(list.length - 1, LM.sel + (e.key === "ArrowDown" ? 1 : -1)));
    renderLoras();
  } else if (e.key === "Enter" && list[LM.sel] && !e.target.closest?.("button")) {
    e.preventDefault();
    toggleLoraFile(list[LM.sel]);
  }
}, true);

const ACT = {
  filter: (id) => { state.filter = id; render(); },
  // Switching tabs keeps each tab's own prompt and settings.
  tab: (id) => {
    if (id === state.tab) return;
    hoverStop();
    state.drafts[state.tab] = state.prompt;
    state.tab = id;
    state.prompt = state.drafts[id] || "";
    state.caret = state.prompt.length;
    state.mentionClosed = true;
    state.settingsOpen = false; state.library.open = false; state.loraPicker = false;
    state.filter = "all";
    render();
  },
  recCfg: () => { state.settings.cfg = modelOf(state.settings.model).cfg; render(); },
  // Send a finished image to the Video tab as an @image, ready to animate.
  useInVideo: async (id) => {
    const c = clipById(id || state.openId);
    if (!c || c.status !== "done") return;
    try {
      const ref = await refFromClip(c.id);
      attach(ref.id);
      if (isFull()) document.exitFullscreen();
      state.view = "gallery";
      ACT.tab("video");
      const sep = state.prompt && !/\s$/.test(state.prompt) ? " " : "";
      state.prompt += sep + "@" + ref.name + " ";
      state.caret = state.prompt.length;
      render();
      $("prompt").focus();
      say("@" + ref.name + " is in the Video tab's prompt. Describe what happens next.");
    } catch (error) {
      say(error.message);
    }
  },
  open: (id) => openClip(id),
  back: () => {
    if (isFull()) document.exitFullscreen();
    state.view = "gallery";
    render();
  },
  close: () => {
    const busy = state.clips.filter(isBusy).length;
    $("confirmText").textContent = "You'll go back to ComfyUI. " + (busy
      ? (busy === 1 ? "The clip that is rendering keeps going" : "The " + busy + " clips that are rendering keep going") + " in the background, and everything is saved."
      : "Everything is saved, so you can pick up where you left off.");
    $("confirm").hidden = false;
    $("confirmYes").focus();
  },
  closeYes: () => { $("confirm").hidden = true; window.parent.postMessage("mmflow-close", location.origin); },
  closeNo: () => { $("confirm").hidden = true; $("closeBtn").focus(); },
  whyMissing: () => say(missingWhy()),
  star: (id) => {
    const c = clipById(id);
    c.starred = !c.starred;
    render();
    api("PATCH", "clips/" + id, { starred: c.starred }).catch((error) => say(error.message));
  },
  cancel: (id) => removeClip(id, "Render cancelled."),
  deleteClip: (id) => { id = id || state.openId; removeClip(id, kindOf(clipById(id) || {}) === "image" ? "Image deleted." : "Clip deleted."); },

  // The x on a prompt-bar image only takes it out of the bar; it stays in the library.
  resetPrompt: () => {
    state.prompt = ""; state.caret = 0; state.mentionClosed = true;
    if (isKlein()) state.kleinSlots = [null, null, null, null];
    else if (!isImage()) state.attached = [];
    render(); saveUi();
    $("prompt").focus();
    say(isImage() ? "Prompt cleared." : "Prompt and images cleared.");
  },
  removeRef: (id) => { state.attached = state.attached.filter((x) => x !== id); render(); },
  // Klein's slots: the x empties one; an empty one opens Add image to fill it.
  clearSlot: (id) => { state.kleinSlots[Number(id)] = null; render(); say(`Image ${Number(id) + 1} is empty now.`); },
  pickSlot: (id) => {
    state.library.slot = Number(id);
    if (!state.library.open) ACT.openLibrary(); else render();
    say(`Pick a picture for image ${Number(id) + 1}, or drag one onto it.`);
  },
  kleinMatch: () => { state.kleinSettings.size = "match"; render(); },
  // A finished image into the Klein tab's image 1, to keep editing it.
  editThis: async (id) => {
    const c = clipById(id || state.openId);
    if (!c || c.status !== "done") return;
    try {
      const ref = await refFromClip(c.id);
      if (isFull()) document.exitFullscreen();
      state.view = "gallery";
      if (!isKlein()) ACT.tab("klein");
      state.kleinSlots[0] = ref.id;
      render();
      $("prompt").focus();
      say("It's image 1 now. Describe the next edit.");
    } catch (error) {
      say(error.message);
    }
  },
  pickMention: (id) => insertMention(state.refs.find((r) => r.id === id)),
  openLibrary: () => {
    state.library.open = !state.library.open;
    if (!state.library.open) state.library.slot = null;
    state.settingsOpen = false;
    if (!uploadItems().some((r) => r.id === state.library.selected)) state.library.selected = uploadItems()[0]?.id || null;
    if (!generatedItems().some((c) => c.id === state.library.selectedGen)) state.library.selectedGen = generatedItems()[0]?.id || null;
    render();
    if (state.library.open) $("libQuery")?.focus();
  },
  libSource: (id) => {
    state.library.source = id;
    renderLibNav(); renderLibList(); renderLibPreview();
  },
  libSelect: (id, el, e, focus) => {
    const hadFocus = focus || document.activeElement?.classList.contains("lib-item");
    if (isGen()) state.library.selectedGen = id; else state.library.selected = id;
    // Only move the highlight: redrawing the list would swallow a double-click.
    document.querySelectorAll("#libList .lib-item").forEach((el) => {
      const on = el.dataset.id === id;
      el.classList.toggle("on", on);
      el.setAttribute("aria-selected", on);
      el.tabIndex = on ? 0 : -1;
    });
    renderLibPreview();
    const item = document.querySelector(`.lib-item[data-id="${id}"]`);
    item?.scrollIntoView({ block: "nearest" });
    if (hadFocus) item?.focus();
  },
  libToggle: async () => {
    let id = state.library.selected;
    if (isGen()) {
      if (!state.library.selectedGen) return;
      try { id = (await refFromClip(state.library.selectedGen)).id; } catch (error) { say(error.message); return; }
    }
    const ref = state.refs.find((r) => r.id === id);
    if (!ref) return;
    if (isKlein()) {
      const at = state.kleinSlots.indexOf(id);
      if (at >= 0) { state.kleinSlots[at] = null; render(); say(`Image ${at + 1} is empty now.`); return; }
      const target = state.library.slot;
      // Opened from an empty slot: fill it and close. Otherwise the next empty slot, and stay open.
      if (target != null) { state.library.slot = null; state.library.open = false; }
      putInSlot(ref, target != null ? target : -1);
      return;
    }
    if (state.attached.includes(id)) { state.attached = state.attached.filter((x) => x !== id); say("@" + ref.name + " taken out of the prompt bar."); }
    else { attach(id); say("@" + ref.name + " added. Type @" + ref.name + " in the prompt to use it."); }
    const hadFocus = document.activeElement?.classList.contains("lib-item");
    renderRefs(); renderLibNav(); renderLibList(); renderLibPreview(); saveUi();
    if (hadFocus) document.querySelector(".lib-item.on")?.focus();
  },
  closePopups: () => { state.settingsOpen = false; state.loraPicker = false; state.library.open = false; state.library.slot = null; render(); },
  toggleNegative: () => {
    state.negativeOpen = !state.negativeOpen;
    renderNegative();
    if (state.negativeOpen) $("negative").focus();
    saveUi();
  },
  toggleSettings: () => { state.settingsOpen = !state.settingsOpen; state.loraPicker = false; state.library.open = false; render(); },

  aspect: (id) => { state.settings.aspect = id; if (isKlein()) state.settings.size = id; render(); },
  recSteps: () => { state.settings.steps = modelOf(state.settings.model).steps; render(); },
  seedMode: (id) => { state.settings.seedMode = id; render(); },
  rollSeed: () => { state.settings.seed = newSeed(); render(); },
  loraPicker: () => openLoras(),
  closeLoras: () => closeLoras(),
  refreshLoras: () => fetchLoras(true),
  pickLora: (name) => toggleLoraFile(name),
  toggleLora: (id) => { const l = state.settings.loras.find((x) => x.id === id); l.on = !l.on; render(); },
  removeLora: (id) => { state.settings.loras = state.settings.loras.filter((x) => x.id !== id); render(); },
  generate: () => generate(),

  play: () => { const v = $("pv"); if (v) (v.paused ? v.play() : v.pause()); },
  fullscreen: () => {
    if (isFull()) document.exitFullscreen();
    else $("clipView").requestFullscreen?.().catch(() => say("Full screen isn't available here."));
  },
  copyPrompt: () => {
    const c = clipById(state.openId);
    navigator.clipboard?.writeText(c.prompt || "").catch(() => {});
    say("Prompt copied.");
  },
  copySeed: () => {
    const c = clipById(state.openId);
    navigator.clipboard?.writeText(String(c.seed)).catch(() => {});
    say("Seed " + c.seed + " copied.");
  },
  reuse: (id) => {
    const c = clipById(id || state.openId);
    if (tabOf(c) === "klein") { reuseKlein(c); return; }
    if (kindOf(c) !== state.tab) ACT.tab(kindOf(c));
    const model = tabOpt().models.some((m) => m.name === c.model) ? c.model : state.settings.model;
    Object.assign(state.settings, { model, steps: c.steps, res: c.res, aspect: c.aspect, seedMode: "random",
      loras: c.loras.map((l) => ({ id: l.name, name: l.name, strength: l.strength, on: true })) },
      kindOf(c) === "image" ? { cfg: c.cfg } : { len: c.len }, { sampler: c.sampler || "", scheduler: c.scheduler || "" });
    if (kindOf(c) === "image") state.negative = c.negative != null ? c.negative : negativeDefault(c.model);
    state.prompt = c.prompt; state.caret = c.prompt.length; state.mentionClosed = true;
    // Only this clip's reference images: the ones it recorded, else the @names in its prompt.
    if (kindOf(c) === "video") {
      state.attached = [];
      (c.refs || []).forEach((r) => { if (state.refs.some((x) => x.id === r.id)) attach(r.id); });
      mentionsIn(c.prompt).forEach((n) => { const r = state.refs.find((x) => x.name === n); if (r) attach(r.id); });
    }
    if (isFull()) document.exitFullscreen();
    state.view = "gallery";
    render();
    say("Prompt and settings are back in the prompt bar, with a random seed.");
  },
  keepSeed: () => {
    const c = clipById(state.openId);
    const s = { image: state.imageSettings, klein: state.kleinSettings }[tabOf(c)] || state.videoSettings;
    s.seedMode = "fixed"; s.seed = c.seed;
    saveUi();
    say("New " + (tabOf(c) === "klein" ? "Klein images" : kindOf(c) === "image" ? "images" : "clips") + " now use seed " + c.seed + ".");
  },
  download: (id) => {
    const c = clipById(id || state.openId);
    if (!c || c.status !== "done") { say("This is still rendering."); return; }
    const a = document.createElement("a");
    a.href = clipUrl(c);
    a.download = downloadName(c);
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  menuDownload: () => { const id = closeMenu(); ACT.download(id); },
  menuAddToEdit: () => {
    const id = closeMenu(), c = clipById(id);
    if (!c || c.status !== "done") return;
    if (EDIT.append(id)) say("Added to the end of the edit.");
    else say("It's in the edit already.");
  },
  menuExportZip: () => { const id = closeMenu(); ACT.exportZip(id); },
  exportZip: async (id) => {
    const c = clipById(id || state.openId);
    if (!c || !c.file) return;
    say("Saving " + c.file.replace(/\.\w+$/, ".zip") + "…");
    try {
      const r = await api("POST", "clips/" + encodeURIComponent(c.id) + "/export", {});
      const a = document.createElement("a");
      a.href = fileUrl(r.subfolder, r.file); a.download = r.file;
      document.body.appendChild(a); a.click(); a.remove();
      say(`Saved ${r.file}: the video and ${r.images} reference image${r.images === 1 ? "" : "s"}, in ${r.subfolder}.`);
    } catch (error) { say(error.message); }
  },
  menuDelete: () => { const id = closeMenu(); ACT.deleteClip(id); },
  menuReuse: () => { const id = closeMenu(); ACT.reuse(id); },
  menuUseInVideo: () => { const id = closeMenu(); ACT.useInVideo(id); },
  menuEditThis: () => { const id = closeMenu(); ACT.editThis(id); },
};

const CHANGE = {
  renameRef: async (el) => {
    const r = state.refs.find((x) => x.id === el.dataset.id);
    const next = el.value.replace(/[^A-Za-z0-9_]/g, "");
    if (!next || state.refs.some((x) => x !== r && x.name === next)) { el.value = r.name; say("Names need letters, digits or _ and must be unique."); return; }
    try {
      await api("PATCH", "images/" + r.id, { name: next });
    } catch (error) {
      el.value = r.name; say(error.message); return;
    }
    state.prompt = state.prompt.replace(new RegExp("@" + r.name + "(?![A-Za-z0-9_])", "g"), "@" + next);
    r.name = next;
    render();
  },
  addImages: (el) => { uploadImages([...el.files]); el.value = ""; },
  importFiles: (el) => { importFiles([...el.files]); el.value = ""; },
  libSort: (el) => { state.library.sort = el.value; renderLibList(); },
  // A new model starts at its recommended steps (and, for images, its recommended guidance).
  model: (el) => {
    const m = modelOf(el.value);
    state.settings.model = el.value; state.settings.steps = m.steps;
    state.settings.sampler = ""; state.settings.scheduler = "";   // back to what the new model is tuned for
    if (isImage() || isKlein()) state.settings.cfg = m.cfg;
    render();
  },
  res: (el) => { state.settings.res = Number(el.value); render(); },
  sampler: (el) => { state.settings.sampler = el.value; render(); },
  scheduler: (el) => { state.settings.scheduler = el.value; render(); },
  seed: (el) => { const v = el.value.replace(/\D/g, "").slice(0, 10); state.settings.seed = v ? Number(v) : 0; render(); },
};

const INPUT = {
  steps: (el) => {
    const s = state.settings, rec = modelOf(s.model).steps;
    s.steps = Number(el.value);
    $("stepsVal").textContent = stepsText(s.steps, rec);
    $("stepsVal").classList.toggle("rec", s.steps === rec);
    $("recBtn").style.visibility = s.steps === rec ? "hidden" : "visible";
    $("pill").textContent = summary();
    saveUi();
  },
  cfg: (el) => {
    const s = state.settings, rec = modelOf(s.model).cfg;
    s.cfg = Number(el.value);
    $("cfgVal").textContent = cfgText(s.cfg, rec);
    $("cfgVal").classList.toggle("rec", s.cfg === rec);
    $("cfgBtn").style.visibility = s.cfg === rec ? "hidden" : "visible";
    $("pill").textContent = summary();
    saveUi();
  },
  len: (el) => { state.settings.len = Number(el.value); $("lenVal").textContent = el.value + " s"; $("pill").textContent = summary(); saveUi(); },
  loraStrength: (el) => {
    const l = state.settings.loras.find((x) => x.id === el.dataset.id);
    l.strength = Number(el.value);
    document.querySelector(`[data-strength="${CSS.escape(l.id)}"]`).textContent = l.strength.toFixed(2);
    $("pill").textContent = summary();
    saveUi();
  },
  libQuery: (el) => { state.library.query = el.value; renderLibList(); },
  perRow: (el) => {
    state.perRow = Number(el.value);
    $("grid").style.setProperty("--per-row", state.perRow);
    $("perRowVal").textContent = state.perRow;
    saveUi();
  },
};

function fromImage(image) {
  return { ...image, img: image.url || viewUrl("images", image.file), tone: toneOf(image.id) };
}

// Use this prompt and settings, for a Klein image: its prompt, settings and the pictures it was made from, each
// back in its own slot (when they are still in the Add image library).
function reuseKlein(c) {
  if (!isKlein()) ACT.tab("klein");
  const s = state.kleinSettings;
  const model = OPT.klein.models.some((m) => m.name === c.model) ? c.model : s.model;
  Object.assign(s, { model, steps: c.steps, cfg: c.cfg, res: c.res, size: c.size || "match", seedMode: "random",
    loras: c.loras.map((l) => ({ id: l.name, name: l.name, strength: l.strength, on: true })) });
  if (c.size && c.size !== "match") s.aspect = c.size;
  const slots = (c.slots || []).concat([null, null, null, null]).slice(0, 4);
  state.kleinSlots = slots.map((x) => (x && state.refs.some((r) => r.id === x.id) ? x.id : null));
  const lost = slots.filter((x, i) => x && !state.kleinSlots[i]).length;
  state.prompt = c.prompt; state.caret = c.prompt.length; state.mentionClosed = true;
  if (isFull()) document.exitFullscreen();
  state.view = "gallery";
  render();
  say("Prompt, settings and pictures are back, with a random seed." + (lost ? ` ${lost === 1 ? "One picture isn't" : lost + " pictures aren't"} in the Add image library any more.` : ""));
}

// Saved files are named vision_<start of the prompt>; older ones get that name when downloaded.
const slugOf = (text) => {
  let out = "";
  for (const w of (text || "").toLowerCase().replace(/'/g, "").match(/[a-z0-9]+/g) || []) {
    if (out && out.length + 1 + w.length > 40) break;
    out = out ? out + "_" + w : w.slice(0, 40);
  }
  return out || "untitled";
};
function downloadName(c) {
  if (c.file && c.file.startsWith("vision_")) return c.file.replace(/_(\d{5})_(\.\w+)$/, "_$1$2");
  const ext = (c.file && c.file.match(/\.\w+$/)?.[0]) || (kindOf(c) === "image" ? ".png" : ".mp4");
  return "vision_" + slugOf(c.prompt) + "_" + c.seed + ext;
}

// Videos and images made earlier: the server reads the prompt and settings saved inside them.
const isZip = (f) => /\.zip$/i.test(f.name);

// What a zip import brought in: its videos join the grid, its reference pictures the Add image library.
function importedZip(r) {
  for (const c of [...r.clips].reverse()) if (!clipById(c.id)) state.clips.unshift(c);
  for (const i of r.images || []) if (!state.refs.some((x) => x.id === i.id)) state.refs.push(fromImage(i));
  return r.clips;
}
function zipSummary(r) {
  const n = r.clips.length, pics = (r.images || []).length;
  return `${n} video${n === 1 ? "" : "s"} added` + (r.same ? `, ${r.same} already here` : "")
    + (pics || r.sameImages ? `; ${pics} reference image${pics === 1 ? "" : "s"} added` + (r.sameImages ? `, ${r.sameImages} already here` : "") : "");
}

async function importFiles(files) {
  files = files.filter((f) => f.type.startsWith("video/") || f.type.startsWith("image/") || isZip(f));
  if (!files.length) return;
  const done = [], failed = [], zips = [];
  for (const [i, f] of files.entries()) {
    say(files.length === 1 ? "Reading " + f.name + "…" : `Importing ${i + 1} of ${files.length}…`);
    const form = new FormData();
    form.append("file", f, f.name);
    try {
      if (isZip(f)) {
        const r = await api("POST", "import-zip", form);
        zips.push(r);
        const here = r.clips.length ? r.clips : [];
        importedZip(r);
        done.push(...here);
        continue;
      }
      const { clip } = await api("POST", "import", form);
      if (!clipById(clip.id)) state.clips.unshift(clip);
      done.push(clip);
    } catch (error) {
      failed.push(error.message);
    }
  }
  if (zips.length && !failed.length) {
    const sum = { clips: zips.flatMap((r) => r.clips), images: zips.flatMap((r) => r.images || []),
      same: zips.reduce((a, r) => a + (r.same || 0), 0), sameImages: zips.reduce((a, r) => a + (r.sameImages || 0), 0) };
    if (state.tab === "edit") EDIT.clipsChanged();
    else if (done.length === 1) { if (state.tab !== "video") ACT.tab("video"); openClip(done[0].id); }
    else { if (state.tab !== "video") ACT.tab("video"); render(); }
    say((files.length === 1 ? "Imported " + files[0].name + ": " : "Imported: ") + zipSummary(sum) + "."
      + (zips.some((r) => r.edit) ? " Open a project's timeline from Edit › Project › Import project." : ""));
    return;
  }
  if (done.length) {
    // In the Edit tab, imported videos just join its media panel.
    if (state.tab === "edit" && done.every((c) => kindOf(c) === "video")) EDIT.clipsChanged();
    else {
      if (tabOf(done[done.length - 1]) !== state.tab) ACT.tab(tabOf(done[done.length - 1]));
      if (done.length === 1) openClip(done[0].id); else render();
    }
  }
  if (failed.length) say(failed.length === 1 ? failed[0] : `${failed.length} files had no prompt or settings inside them. ${failed[0]}`);
  else if (done.length === 1) say(done[0].found === "graph" ? "Imported. The settings were worked out from the file's ComfyUI workflow." : "Imported with its prompt and settings.");
  else if (done.length) say("Imported " + done.length + " files.");
}

async function uploadImages(files) {
  files = files.filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  const form = new FormData();
  files.forEach((f) => form.append("file", f, f.name));
  say(files.length === 1 ? "Uploading…" : "Uploading " + files.length + " images…");
  try {
    const { images } = await api("POST", "images", form);
    const added = images.map(fromImage);
    state.refs.push(...added);
    added.forEach((a) => attach(a.id));
    state.library.selected = added[0]?.id || state.library.selected;
    render();
    document.querySelector(".lib-item.on")?.scrollIntoView({ block: "nearest" });
    say("Added @" + added.map((a) => a.name).join(", @") + ". Type @ to use it.");
  } catch (error) {
    say("Upload failed: " + error.message);
  }
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (el && (el.classList.contains("off") || el.disabled)) return;
  if (el && ACT[el.dataset.act]) ACT[el.dataset.act](el.dataset.id, el, e);
});
// Picking from the @ list keeps the cursor in the prompt.
document.addEventListener("mousedown", (e) => { if (e.target.closest(".mention")) e.preventDefault(); });
document.addEventListener("change", (e) => {
  const el = e.target.closest("[data-change]");
  if (el && CHANGE[el.dataset.change]) CHANGE[el.dataset.change](el);
});
document.addEventListener("input", (e) => {
  const el = e.target.closest("[data-input]");
  if (el && INPUT[el.dataset.input]) INPUT[el.dataset.input](el);
});
document.addEventListener("keydown", (e) => {
  if (!$("confirm").hidden) {
    if (e.key === "Escape") { e.preventDefault(); ACT.closeNo(); }
    if (e.key === "Tab") {
      e.preventDefault();
      (document.activeElement === $("confirmYes") ? $("confirmNo") : $("confirmYes")).focus();
    }
    return;
  }
  if (e.key === "Escape" && e.target.id !== "prompt" && (state.settingsOpen || state.library.open)) ACT.closePopups();
});
document.addEventListener("keydown", (e) => {
  if (!state.library.open) return;
  const item = e.target.closest?.(".lib-item");
  const inSearch = e.target.id === "libQuery";
  if (!item && !inSearch) return;
  // Up and down move through the list, from the search field too.
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (inSearch && !libraryIds().includes(libSelected())) libMove(e.key === "ArrowDown" ? 1 : -1);
    else if (inSearch) ACT.libSelect(libSelected(), null, null, true);
    else libMove(e.key === "ArrowDown" ? 1 : -1);
    return;
  }
  if (e.key === "Home" || e.key === "End") {
    if (!item) return;
    e.preventDefault();
    const ids = libraryIds();
    if (ids.length) ACT.libSelect(e.key === "Home" ? ids[0] : ids[ids.length - 1], null, null, true);
    return;
  }
  // Enter picks an image, and Enter again adds it (or takes it out). From the search field, Enter adds the selected one.
  if (e.key === "Enter" && inSearch) { e.preventDefault(); if (libSelected()) ACT.libToggle(); return; }
  if (!item || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault();
  if (libSelected() === item.dataset.id && e.key === "Enter") ACT.libToggle();
  else ACT.libSelect(item.dataset.id, null, null, true);
});
document.addEventListener("fullscreenchange", () => { if (state.view === "clip") { $("clipView").classList.toggle("fs", isFull()); syncFullscreenButton(); } });
// On the clip page: double-click the picture or press F for full screen, Space to play or pause.
document.addEventListener("dblclick", (e) => { if (e.target.closest(".player") && !e.target.closest("button") && ($("pv") || $("pimg"))) ACT.fullscreen(); });
document.addEventListener("keydown", (e) => {
  if (state.view !== "clip" || e.target.closest("input, textarea, select") || !($("pv") || $("pimg"))) return;
  if (e.key === "f" || e.key === "F") ACT.fullscreen();
  // Left and right go to the previous and next video or image in the gallery.
  if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && !e.altKey && !e.metaKey && !e.ctrlKey) {
    const list = filteredClips(), at = list.findIndex((c) => c.id === state.openId);
    const next = list[at + (e.key === "ArrowRight" ? 1 : -1)];
    e.preventDefault();
    if (next) openClip(next.id);
    return;
  }
  if (!$("pv")) return;
  if (e.key === " " && !e.target.closest("button")) { e.preventDefault(); ACT.play(); }
});
// Double-click an image in the library to add it straight away.
document.addEventListener("dblclick", (e) => {
  const item = e.target.closest(".lib-item");
  if (!item) return;
  const refId = isGen() ? refForClip(item.dataset.id)?.id : item.dataset.id;
  if (refId && state.attached.includes(refId)) return;
  if (isGen()) state.library.selectedGen = item.dataset.id; else state.library.selected = item.dataset.id;
  ACT.libToggle();
});
window.addEventListener("resize", () => { if (state.settingsOpen) renderSettings(); if (state.library.open) renderLibrary(); });

// Image files can be dropped anywhere on the page, or pasted into the prompt.
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
document.addEventListener("dragover", (e) => {
  if (state.view !== "gallery" || !hasFiles(e)) return;
  e.preventDefault();
  if (!isImage()) $("addRef")?.classList.add("drag");
  if (isKlein()) markReplace(e.target.closest?.("[data-slot]") || null);
});
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) $("addRef")?.classList.remove("drag"); });
document.addEventListener("drop", (e) => {
  if (state.view !== "gallery" || !hasFiles(e)) return;
  e.preventDefault();
  $("addRef")?.classList.remove("drag");
  const files = [...e.dataTransfer.files];
  if (isKlein()) {
    // Pictures go in the slots (the one dropped on, or the empty ones); videos and zips are still imports.
    const slot = e.target.closest?.("[data-slot]");
    markReplace(null);
    const rest = files.filter((f) => f.type.startsWith("video/") || isZip(f));
    if (rest.length) importFiles(rest);
    uploadToSlots(files, slot ? Number(slot.dataset.slot) : -1);
    return;
  }
  // Videos are imports. Pictures are imports in the Image tab, and @images in the Video tab.
  const imports = files.filter((f) => f.type.startsWith("video/") || isZip(f) || (isImage() && f.type.startsWith("image/")));
  if (imports.length) importFiles(imports);
  if (!isImage() && state.tab !== "edit") uploadImages(files.filter((f) => f.type.startsWith("image/")));
});

/* ------------------------------------------------------------------
   Dragging your images into the prompt. The library list, its big preview and the
   prompt-bar images can all be dragged. Dropped on the text, the @name goes where you
   let go; dropped elsewhere on the prompt box, it goes at the end.
   ------------------------------------------------------------------ */
const REF_MIME = "application/x-minimax-ref";
const GEN_MIME = "application/x-minimax-generated";
const SLOT_MIME = "application/x-minimax-slot";   // a Klein slot's picture, dragged to another slot
const isRefDrag = (e) => [...(e.dataTransfer?.types || [])].some((t) => t === REF_MIME || t === GEN_MIME || t === SLOT_MIME);
const composer = document.querySelector(".composer");
const ta = $("prompt");

document.addEventListener("dragstart", (e) => {
  const slot = e.target.closest?.("[data-drag-slot]");
  if (slot) {
    e.dataTransfer.clearData();
    e.dataTransfer.setData(SLOT_MIME, slot.dataset.dragSlot);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setDragImage(slot, 40, 40);
    slot.closest(".slot")?.classList.add("dragging");
    document.body.classList.add("dragging-ref");
    return;
  }
  const gen = e.target.closest?.("[data-drag-gen]");
  if (gen) {
    const c = clipById(gen.dataset.dragGen);
    if (!c) return;
    const ref = refForClip(c.id);
    e.dataTransfer.clearData();
    e.dataTransfer.setData(GEN_MIME, c.id);
    e.dataTransfer.setData("text/plain", ref ? "@" + ref.name + " " : c.prompt);
    e.dataTransfer.effectAllowed = "copy";
    const thumb = gen.querySelector(".lib-thumb") || gen.querySelector(".pic img");
    if (thumb) e.dataTransfer.setDragImage(thumb, 24, 24);
    document.body.classList.add("dragging-ref");
    return;
  }
  const src = e.target.closest?.("[data-drag-ref]");
  if (!src) return;
  const r = state.refs.find((x) => x.id === src.dataset.dragRef);
  if (!r) return;
  e.dataTransfer.clearData();
  e.dataTransfer.setData(REF_MIME, r.id);
  e.dataTransfer.setData("text/plain", "@" + r.name + " ");
  e.dataTransfer.effectAllowed = "copy";
  e.dataTransfer.setDragImage(src.querySelector(".lib-thumb") || src, 24, 24);
  document.body.classList.add("dragging-ref");
});
document.addEventListener("dragend", () => {
  document.body.classList.remove("dragging-ref");
  document.querySelectorAll(".slot.dragging").forEach((x) => x.classList.remove("dragging"));
  composer.classList.remove("drop-on");
  markReplace(null);
});
// An image dropped on an image in the strip (or on a missing one) replaces it, in the strip and in the prompt.
const replaceTarget = (e) => e.target.closest?.(isKlein() ? "[data-slot]" : "[data-replace-ref], [data-replace-name]");
function markReplace(el) {
  document.querySelectorAll(".replace-on").forEach((x) => { if (x !== el) x.classList.remove("replace-on"); });
  el?.classList.add("replace-on");
}
composer.addEventListener("dragover", (e) => {
  if (!isRefDrag(e)) return;
  e.preventDefault();
  // A slot's picture moves (it is "move" only); everything else is copied in.
  e.dataTransfer.dropEffect = [...e.dataTransfer.types].includes(SLOT_MIME) ? "move" : "copy";
  composer.classList.add("drop-on");
  markReplace(replaceTarget(e));
});
composer.addEventListener("dragleave", (e) => { if (!composer.contains(e.relatedTarget)) { composer.classList.remove("drop-on"); markReplace(null); } });
composer.addEventListener("drop", async (e) => {
  if (!isRefDrag(e)) return;
  e.preventDefault();
  composer.classList.remove("drop-on");
  if (isKlein()) { dropOnSlots(e); return; }
  const genId = e.dataTransfer.getData(GEN_MIME);
  // Work out where it was dropped now: the drop event's position is only valid during it.
  const dropAt = e.target === ta ? textOffsetAt(e.clientX, e.clientY) : null;
  const target = replaceTarget(e);
  const oldId = target?.dataset.replaceRef, oldName = target?.dataset.replaceName;
  markReplace(null);
  let r;
  if (genId) { try { r = await refFromClip(genId); } catch (error) { say(error.message); return; } }
  else r = state.refs.find((x) => x.id === e.dataTransfer.getData(REF_MIME));
  if (!r) return;
  if (target) { replaceRef(oldId, oldName, r); return; }
  attach(r.id);
  const pos = dropAt !== null ? wordEdge(state.prompt, dropAt) : state.prompt.length;
  const before = state.prompt.slice(0, pos), after = state.prompt.slice(pos);
  const insert = (before && !/\s$/.test(before) ? " " : "") + "@" + r.name + (/^\s/.test(after) ? "" : " ");
  state.prompt = before + insert + after;
  state.caret = before.length + insert.length;
  state.mentionClosed = true;
  renderPrompt(); renderRefs();
  if (state.library.open) { renderLibNav(); renderLibList(); renderLibPreview(); }
  saveUi();
  ta.focus();
  ta.setSelectionRange(state.caret, state.caret);
});

// Klein: a picture dropped on a slot goes in that slot (a slot's picture swaps places with what's there); dropped
// anywhere else on the prompt bar, it goes in the first empty slot. The prompt stays as it is.
async function dropOnSlots(e) {
  const target = e.target.closest?.("[data-slot]");
  const to = target ? Number(target.dataset.slot) : -1;
  markReplace(null);
  const from = e.dataTransfer.getData(SLOT_MIME);
  if (from !== "") {
    const a = Number(from);
    if (to < 0 || a === to) return;
    [state.kleinSlots[a], state.kleinSlots[to]] = [state.kleinSlots[to], state.kleinSlots[a]];
    render();
    say(state.kleinSlots[a] ? `Swapped image ${a + 1} and image ${to + 1}.` : `Moved to image ${to + 1}.`);
    return;
  }
  const genId = e.dataTransfer.getData(GEN_MIME);
  let r;
  if (genId) { try { r = await refFromClip(genId); } catch (error) { say(error.message); return; } }
  else r = state.refs.find((x) => x.id === e.dataTransfer.getData(REF_MIME));
  if (!r) return;
  const replaced = to >= 0 && state.kleinSlots[to] && state.kleinSlots[to] !== r.id;
  const at = putInSlot(r, to);
  if (replaced && at >= 0) say(`@${r.name} is image ${at + 1} now (replaced).`);
  if (state.library.open) { renderLibNav(); renderLibList(); renderLibPreview(); }
}

// Files from your computer, for Klein: into the slot they were dropped on, or the empty slots in order.
async function uploadToSlots(files, slot = -1) {
  files = files.filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  const form = new FormData();
  files.forEach((f) => form.append("file", f, f.name));
  say(files.length === 1 ? "Uploading…" : "Uploading " + files.length + " images…");
  try {
    const { images } = await api("POST", "images", form);
    const added = images.map(fromImage);
    state.refs.push(...added);
    const placed = [];
    added.forEach((r, n) => {
      const at = n === 0 && slot >= 0 ? slot : state.kleinSlots.indexOf(null);
      if (at >= 0) { state.kleinSlots[at] = r.id; placed.push(`@${r.name} is image ${at + 1}`); }
    });
    render();
    say(placed.length ? placed.join(", ") + "." + (placed.length < added.length ? " The rest are in Add image (all four slots are full)." : "")
      : "All four slots are full: the pictures are in Add image.");
  } catch (error) {
    say("Upload failed: " + error.message);
  }
}

// Put image r where another was: in the strip at its place, and as @r.name wherever the prompt named the old one.
function replaceRef(oldId, oldName, r) {
  const old = oldId ? state.refs.find((x) => x.id === oldId) : null;
  const name = old ? old.name : oldName;
  if (old && old.id === r.id) return;
  if (old) {
    const at = state.attached.indexOf(old.id);
    state.attached = state.attached.filter((x) => x !== r.id);
    const i = state.attached.indexOf(old.id);
    if (i >= 0) state.attached.splice(i, 1, r.id); else state.attached.splice(Math.max(0, at), 0, r.id);
  } else attach(r.id);
  const re = new RegExp("@" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![A-Za-z0-9_])", "g");
  const count = (state.prompt.match(re) || []).length;
  state.prompt = state.prompt.replace(re, "@" + r.name);
  state.caret = Math.min(state.caret, state.prompt.length);
  state.mentionClosed = true;
  renderPrompt(); renderRefs();
  if (state.library.open) { renderLibNav(); renderLibList(); renderLibPreview(); }
  saveUi();
  say(`@${name} is now @${r.name}` + (count ? (count > 1 ? ` (${count} places in the prompt).` : " in the prompt.") : "."));
}

// A drop inside a word moves to the nearer end of that word, so words are never split.
function wordEdge(text, pos) {
  const word = /[^\s]/;
  if (!(word.test(text[pos - 1] || "") && word.test(text[pos] || ""))) return pos;
  let a = pos, b = pos;
  while (a > 0 && word.test(text[a - 1])) a--;
  while (b < text.length && word.test(text[b])) b++;
  return pos - a <= b - pos ? a : b;
}

// Which character of the prompt is under a point. The coloured copy under the text box
// has the same layout, so ask the browser for the spot in that copy.
function textOffsetAt(x, y) {
  ta.style.pointerEvents = "none";
  let node = null, offset = 0;
  if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(x, y);
    if (p) { node = p.offsetNode; offset = p.offset; }
  } else if (document.caretRangeFromPoint) {
    const r = document.caretRangeFromPoint(x, y);
    if (r) { node = r.startContainer; offset = r.startOffset; }
  }
  ta.style.pointerEvents = "";
  const mirror = $("mirror");
  if (!state.prompt || !node || !mirror.contains(node)) return state.prompt.length;
  const walk = document.createTreeWalker(mirror, NodeFilter.SHOW_TEXT);
  let count = 0;
  while (walk.nextNode()) {
    if (walk.currentNode === node) return Math.min(count + offset, state.prompt.length);
    count += walk.currentNode.length;
  }
  return state.prompt.length;
}

/* The prompt box */
// Clicking into the prompt closes an open pop-up (image library or settings).
ta.addEventListener("pointerdown", () => { if (state.library.open || state.settingsOpen) ACT.closePopups(); });
ta.addEventListener("input", () => {
  state.prompt = ta.value; state.caret = ta.selectionStart;
  state.mentionClosed = false; state.mentionIndex = 0;
  renderPrompt();
  renderRefs();
  saveUi();
});
ta.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (isKlein() && files.some((f) => f.type.startsWith("image/"))) { e.preventDefault(); uploadToSlots(files); return; }
  if (!isImage() && files.some((f) => f.type.startsWith("image/"))) { e.preventDefault(); uploadImages(files); }
});
const syncCaret = () => { if (ta.selectionStart !== state.caret) { state.caret = ta.selectionStart; renderMention(); } };
ta.addEventListener("click", syncCaret);
ta.addEventListener("keyup", (e) => { if (!["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"].includes(e.key)) syncCaret(); });
ta.addEventListener("keydown", (e) => {
  const list = mentionMatches();
  if (list.length) {
    const n = list.length, i = Math.min(state.mentionIndex, n - 1);
    if (e.key === "ArrowDown") { e.preventDefault(); state.mentionIndex = (i + 1) % n; renderMention(); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); state.mentionIndex = (i - 1 + n) % n; renderMention(); return; }
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertMention(list[i]); return; }
  }
  if (e.key === "Escape") {
    if (mentionQuery() !== null) { state.mentionClosed = true; renderMention(); }
    else if (state.settingsOpen || state.library.open) ACT.closePopups();
  }
});

/* ------------------------------------------------------------------
   Start
   ------------------------------------------------------------------ */
async function load(first = true) {
  try {
    const data = await api("GET", "state");
    OPT = { ...OPT, ...data.options };
    state.refs = data.images.map(fromImage);
    state.clips = data.clips.filter((c) => !deleted.has(c.id));
    if (first) {
      const ui = data.ui || {};
      Object.assign(state.videoSettings, ui.settings || {});
      Object.assign(state.imageSettings, ui.imageSettings || {});
      Object.assign(state.kleinSettings, ui.kleinSettings || {});
      // Settings saved earlier must still suit what is installed, in every tab.
      for (const tab of ["video", "image", "klein"]) {
        state.tab = tab;
        const T = tabOpt(), s = state.settings;
        if (T.models.length && !T.models.some((m) => m.name === s.model)) s.model = T.models[0].name;
        const m = modelOf(s.model), lo = m.minSteps || 1, every = m.stepSize || 1;
        if (s.steps < lo || s.steps > m.maxSteps || (s.steps - lo) % every) s.steps = m.steps;
        if (!T.megapixels.includes(s.res)) s.res = tab === "video" ? 0.6 : 1.0;
        if (!T.aspects[s.aspect]) s.aspect = tab === "video" ? "16:9" : "1:1";
        if (tab !== "video" && !(s.cfg >= OPT.image.cfg.min && s.cfg <= OPT.image.cfg.max)) s.cfg = m.cfg;
        if (tab === "klein") {
          if (s.size !== "match" && !T.aspects[s.size]) s.size = "match";
          if (!m.base) s.cfg = m.cfg;   // Distilled renders without guidance
        }
      }
      // The first time the app opens in this browser tab, the Video tab starts on HyperFlow (when
      // installed), 5 seconds and 0.6 MP. After that (a reload, closing and opening it again) it keeps
      // the settings you had.
      let opened = false;
      try { opened = sessionStorage.getItem("mmvision.opened") === "1"; sessionStorage.setItem("mmvision.opened", "1"); } catch {}
      if (!opened) {
        const v = state.videoSettings, flow = OPT.models.find((m) => m.id === "hyperflow");
        if (flow) { v.model = flow.name; v.steps = flow.steps; }
        v.len = 5;
        v.res = 0.6;
      }
      EDIT.load(data.edit);
      state.tab = "video";   // the app always opens on the Video tab
      state.drafts = { video: ui.prompt || "", image: ui.imagePrompt || "", klein: ui.kleinPrompt || "" };
      state.kleinSlots = [0, 1, 2, 3].map((i) => { const id = (ui.kleinSlots || [])[i]; return id && state.refs.some((r) => r.id === id) ? id : null; });
      state.negative = typeof ui.imageNegative === "string" ? ui.imageNegative : "";
      state.negativeOpen = !!ui.negativeOpen;
      state.attached = (ui.attached || []).filter((id) => state.refs.some((r) => r.id === id));
      state.perRow = ui.perRow || 3;
      state.prompt = state.drafts[state.tab];
      state.caret = state.prompt.length;
      state.mentionClosed = true;
    }
    state.ready = true;
    state.loadError = null;
    shownClip = null;
    render();
  } catch (error) {
    state.loadError = error.message;
    if (!state.ready) renderGrid();
    clearTimeout(load.retry);
    load.retry = setTimeout(() => load(first), 4000);
  }
}


// Hovering an image in the prompt bar shows it bigger, above it.
const peek = document.createElement("div");
peek.className = "ref-peek";
peek.hidden = true;
peek.innerHTML = '<img alt="">';
document.body.appendChild(peek);
const hidePeek = () => { peek.hidden = true; };
document.addEventListener("mouseover", (e) => {
  const pic = e.target.closest?.(".ref-pic");
  if (!pic || e.target.closest(".ref-x") || e.buttons) { if (!e.target.closest?.(".ref-peek")) hidePeek(); return; }
  const src = pic.querySelector("img")?.src;
  if (!src) return;
  const img = peek.firstChild;
  const place = () => {
    const box = pic.getBoundingClientRect();
    const ratio = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 1.5;
    // Klein's slots get a big view (up to 560 px), below the top bar.
    const big = pic.classList.contains("slot-pic");
    const h = Math.min(big ? 560 : 300, box.top - (big ? belowTop() + 12 : 24)), w = Math.min(big ? 760 : 420, h * ratio, innerWidth - 24);
    peek.style.width = w + "px"; peek.style.height = w / ratio + "px";
    peek.style.left = Math.max(12, Math.min(innerWidth - w - 12, box.left + box.width / 2 - w / 2)) + "px";
    peek.style.top = box.top - w / ratio - 12 + "px";
    peek.hidden = false;
  };
  if (img.getAttribute("src") !== src) { img.onload = place; img.src = src; if (img.complete && img.naturalWidth) place(); }
  else place();
});
document.addEventListener("mouseout", (e) => { if (e.target.closest?.(".ref-pic") && !e.relatedTarget?.closest?.(".ref-pic")) hidePeek(); });
document.addEventListener("dragstart", hidePeek, true);
document.addEventListener("pointerdown", hidePeek, true);

// The prompt box's height. By itself it grows with the text, up to half the window, and longer text
// scrolls. Dragging the handle in its top-right corner sets a height of your own (up makes it taller)
// and a width (sideways; it stays centred), smaller than the text if you like. They last for the session.
const PROMPT_SIZE = "mmvision.prompt-size";
const PROMPT_MIN = 78;
let promptSize = null;   // {width, height} you dragged to, or null
try { promptSize = JSON.parse(sessionStorage.getItem(PROMPT_SIZE) || "null"); } catch {}
function fitPrompt() {
  const root = document.documentElement.style, mirror = $("mirror");
  if (promptSize?.width) root.setProperty("--composer-width", promptSize.width + "px");
  let height = promptSize?.height;
  if (!height) {
    root.setProperty("--prompt-box", PROMPT_MIN + "px");   // measure the text at the smallest box
    height = Math.min(Math.max(PROMPT_MIN, mirror.scrollHeight), Math.round(innerHeight * 0.5));
  }
  root.setProperty("--prompt-box", height + "px");
  mirror.scrollTop = ta.scrollTop;
}
ta.addEventListener("scroll", () => { $("mirror").scrollTop = ta.scrollTop; });
addEventListener("resize", () => fitPrompt());
(function promptResizer() {
  const handle = document.createElement("div");
  handle.className = "prompt-resize";
  handle.title = "Drag to resize the prompt. Double-click to let it fit the text again.";
  handle.setAttribute("aria-hidden", "true");
  handle.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M1 1h10v10M5 1h6v6"/></svg>';
  document.querySelector(".composer").appendChild(handle);
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const inner = document.querySelector(".dock-inner");
    const start = { x: e.clientX, y: e.clientY, w: inner.offsetWidth, h: document.querySelector(".prompt-wrap").offsetHeight };
    let moved = false;
    const move = (ev) => {
      if (!moved && Math.abs(ev.clientX - start.x) + Math.abs(ev.clientY - start.y) < 3) return;
      moved = true;
      promptSize = {
        width: Math.round(Math.max(520, Math.min(innerWidth - 64, start.w + 2 * (ev.clientX - start.x)))),
        height: Math.round(Math.max(PROMPT_MIN - 40, Math.min(innerHeight * 0.75, start.h - (ev.clientY - start.y)))),
      };
      fitPrompt();
      if (state.settingsOpen) renderSettings();
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      if (moved) try { sessionStorage.setItem(PROMPT_SIZE, JSON.stringify(promptSize)); } catch {}
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
  // Double-click: back to fitting the text (the width you set stays).
  handle.addEventListener("dblclick", () => {
    if (promptSize) delete promptSize.height;
    try { sessionStorage.setItem(PROMPT_SIZE, JSON.stringify(promptSize)); } catch {}
    fitPrompt();
  });
  // A longer prompt makes the dock taller too, so the gallery keeps room below its last row.
  new ResizeObserver(() => document.documentElement.style.setProperty("--dock-space", $("dock").offsetHeight + 60 + "px")).observe($("dock"));
})();


$("negative").addEventListener("input", (e) => { state.negative = e.target.value; renderNegative(); saveUi(); });

// Start. Last, so everything above is defined.
// Only show the Close button when the app runs inside ComfyUI's page.
if (window.parent !== window) $("closeBtn").hidden = false;
render();
load();
connect();
