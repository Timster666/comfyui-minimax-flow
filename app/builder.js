// The prompt builder: a library of prompts in categories and subcategories, and a text field to put a prompt
// together from them. Opened with Builder in the prompt bar; Apply puts the text into the prompt.
//
// The library is kept on the server (and in the Google Drive folder when it is set up) as a flat list of nodes:
// {id, kind: "cat" | "sub" | "prompt", parent, pos, at, name, text}, and the ids deleted, with when. Every change
// here is saved a moment later; the text field only reaches the prompt with Apply.
"use strict";

const PB = {
  open: false, lib: { version: 1, nodes: [], deleted: {} }, drive: { state: "off" }, loaded: false,
  sel: null, q: "", folded: new Set(), editing: null, renaming: null, original: "", caret: null,
  saveTimer: null, saving: false, undo: null, undoTimer: null, drag: null, moveFor: null,
};
const pb$ = (id) => document.getElementById(id);
const pbNow = () => Date.now() / 1000;
const pbId = (kind) => kind[0] + Math.random().toString(36).slice(2, 12);
const pbNode = (id) => PB.lib.nodes.find((n) => n.id === id);
const pbKids = (parent, kind) => PB.lib.nodes.filter((n) => (n.parent || null) === (parent || null) && (!kind || n.kind === kind))
  .sort((a, b) => a.pos - b.pos || (a.id < b.id ? -1 : 1));
const pbCats = () => pbKids(null, "cat");
const pbCount = (id) => pbKids(id, "prompt").length + pbKids(id, "sub").reduce((n, s) => n + pbKids(s.id, "prompt").length, 0);
const pbPath = (id) => { const n = pbNode(id); if (!n) return ""; const p = n.parent && pbNode(n.parent); return p ? p.name + " › " + n.name : n.name; };

// ---- Loading and saving -------------------------------------------------------
async function pbLoad() {
  try {
    const r = await api("GET", "prompts");
    PB.lib = r.lib; PB.drive = r.drive; PB.loaded = true;
  } catch (error) { say("Couldn't read the prompt library: " + error.message); }
  if (!PB.sel || !pbNode(PB.sel)) PB.sel = (pbCats()[0] || {}).id || null;
  pbRender();
}
function pbChanged() {
  PB.dirty = true; PB.saving = true;
  pbRender();
  clearTimeout(PB.saveTimer);
  PB.saveTimer = setTimeout(pbSave, 400);
}
async function pbSave() {
  PB.dirty = false;
  try {
    const r = await api("PUT", "prompts", { lib: PB.lib });
    // The newer change of each node wins, so what was changed while this was on its way is kept, and the
    // server's copy brings in what other pods did.
    PB.lib = pbMergeLocal(PB.lib, r.lib);
    PB.drive = r.drive;
  } catch (error) { say("Couldn't save the prompt library: " + error.message); PB.dirty = true; }
  PB.saving = PB.dirty && !!PB.saveTimer;
  pbRender();
  if (PB.drive.state === "pending" || PB.drive.state === "syncing") pbPollDrive();
}
// The newer change of each node wins, as on the server.
function pbMergeLocal(mine, theirs) {
  const nodes = new Map();
  for (const n of [...theirs.nodes, ...mine.nodes]) { const o = nodes.get(n.id); if (!o || n.at >= o.at) nodes.set(n.id, n); }
  const deleted = { ...theirs.deleted, ...mine.deleted };
  return { version: 1, nodes: [...nodes.values()].filter((n) => !(deleted[n.id] >= n.at)), deleted };
}
let pbDriveTimer = null;
function pbPollDrive() {
  clearTimeout(pbDriveTimer);
  pbDriveTimer = setTimeout(async () => {
    try {
      const r = await api("GET", "prompts");
      PB.drive = r.drive;
      if (!PB.dirty) { PB.lib = pbMergeLocal(PB.lib, r.lib); if (PB.open && !PB.editing && !PB.renaming) pbRender(); }
    } catch {}
    pbStatus();
    if (PB.open && ["pending", "syncing"].includes(PB.drive.state)) pbPollDrive();
  }, 2500);
}
function pbStatus() {
  const el = pb$("pbStatus");
  if (!el) return;
  const d = PB.drive || {};
  let text, cls = "", sync = false;
  if (PB.saving) text = "Saving…";
  else if (d.state === "off") text = "Saved on this pod";
  else if (d.state === "synced") text = "Saved to Google Drive";
  else if (d.state === "pending" || d.state === "syncing") text = "Saving to Google Drive…";
  else if (d.state === "error") { text = "Not saved to Google Drive"; cls = "bad"; sync = true; }
  else text = "Saved";
  el.className = "pb-status " + cls;
  el.title = d.state === "error" ? d.error || "" : "";
  el.innerHTML = esc(text) + (sync ? ` <button class="link" data-b="sync">Try again</button>` : "");
}

// ---- Changing the library ------------------------------------------------------
function pbTouch(n, fields) { Object.assign(n, fields, { at: pbNow() }); }
function pbReindex(parent, kind) { pbKids(parent, kind).forEach((n, i) => { if (n.pos !== i + 1) pbTouch(n, { pos: i + 1 }); }); }
function pbAdd(kind, parent, fields, before) {
  const sibs = pbKids(parent, kind);
  const node = { id: pbId(kind), kind, parent: parent || null, pos: sibs.length + 1, at: pbNow(), name: "", ...fields };
  PB.lib.nodes.push(node);
  if (before !== undefined) pbPlace(node, parent, before);
  return node;
}
// Put a node among its new siblings, before the one given (or at the end).
function pbPlace(node, parent, beforeId) {
  const sibs = pbKids(parent, node.kind).filter((n) => n.id !== node.id);
  let at = beforeId ? sibs.findIndex((n) => n.id === beforeId) : -1;
  if (at < 0) at = sibs.length;
  sibs.splice(at, 0, node);
  pbTouch(node, { parent: parent || null });
  sibs.forEach((n, i) => { if (n.pos !== i + 1 || n === node) pbTouch(n, { pos: i + 1 }); });
}
function pbRemove(id) {
  const n = pbNode(id);
  if (!n) return [];
  const gone = [n, ...PB.lib.nodes.filter((x) => x.parent === id), ...PB.lib.nodes.filter((x) => { const p = x.parent && pbNode(x.parent); return p && p.parent === id; })];
  const ids = new Set(gone.map((x) => x.id)), t = pbNow();
  PB.lib.nodes = PB.lib.nodes.filter((x) => !ids.has(x.id));
  ids.forEach((k) => { PB.lib.deleted[k] = t; });
  return gone.map((x) => ({ ...x }));
}
function pbUndoable(what, nodes) {
  clearTimeout(PB.undoTimer);
  PB.undo = { what, nodes };
  PB.undoTimer = setTimeout(() => { PB.undo = null; pbRender(); }, 8000);
}
function pbDoUndo() {
  if (!PB.undo) return;
  const t = pbNow();
  for (const n of PB.undo.nodes) { delete PB.lib.deleted[n.id]; PB.lib.nodes.push({ ...n, at: t }); }
  PB.undo = null;
  pbChanged();
}

// ---- Drawing -----------------------------------------------------------------
const PB_ICON = {
  plus: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  edit: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
  trash: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  move: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7h6l2 2h10v10H3z"/><path d="M12 13h5M15 11l2 2-2 2"/></svg>',
  caret: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
  grip: '<svg width="10" height="14" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true"><circle cx="2" cy="2" r="1.4"/><circle cx="8" cy="2" r="1.4"/><circle cx="2" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="2" cy="14" r="1.4"/><circle cx="8" cy="14" r="1.4"/></svg>',
};
function pbRender() {
  if (!PB.open) return;
  pbRenderTree();
  pbRenderList();
  pbStatus();
  pb$("pbUndo").hidden = !PB.undo;
  if (PB.undo) pb$("pbUndoText").textContent = PB.undo.what;
}
function pbRow(n, depth) {
  const kids = n.kind === "cat" ? pbKids(n.id, "sub") : [];
  const folded = PB.folded.has(n.id), on = PB.sel === n.id && !PB.q;
  const name = PB.renaming === n.id
    ? `<input class="pb-rename" id="pbRename" value="${esc(n.name)}" aria-label="Name" maxlength="120">`
    : `<span class="pb-name">${esc(n.name || "Untitled")}</span>`;
  return `<div class="pb-row ${n.kind} ${on ? "on" : ""}" draggable="${PB.renaming === n.id ? "false" : "true"}" data-node="${n.id}" data-b="select" data-id="${n.id}" role="treeitem" aria-selected="${on}" ${n.kind === "cat" && kids.length ? `aria-expanded="${!folded}"` : ""} tabindex="0">
      ${n.kind === "cat" ? `<button class="pb-fold ${folded ? "" : "open"} ${kids.length ? "" : "none"}" data-b="fold" data-id="${n.id}" aria-label="${folded ? "Show" : "Hide"} subcategories" tabindex="-1">${PB_ICON.caret}</button>` : ""}
      ${name}<span class="pb-count">${pbCount(n.id) || ""}</span>
      <span class="pb-acts">
        ${n.kind === "cat" ? `<button data-b="addSub" data-id="${n.id}" title="Add a subcategory" aria-label="Add a subcategory to ${esc(n.name)}">${PB_ICON.plus}</button>` : ""}
        <button data-b="rename" data-id="${n.id}" title="Rename" aria-label="Rename ${esc(n.name)}">${PB_ICON.edit}</button>
        <button data-b="delNode" data-id="${n.id}" title="Delete" aria-label="Delete ${esc(n.name)}">${PB_ICON.trash}</button>
      </span>
    </div>${kids.length && !folded ? `<div class="pb-subs" role="group">${kids.map((k) => pbRow(k, depth + 1)).join("")}</div>` : ""}`;
}
function pbRenderTree() {
  const cats = pbCats();
  pb$("pbTree").innerHTML = cats.length ? cats.map((c) => pbRow(c, 0)).join("")
    : `<div class="pb-empty">No categories yet.<br>Make one for each kind of prompt, like <i>Camera</i>, <i>Lighting</i> or <i>Style</i>.</div>`;
  if (PB.renaming) {
    const inp = pb$("pbRename");
    if (inp && document.activeElement !== inp) { inp.focus(); inp.select(); }
  }
}
function pbCard(p, withPath) {
  if (PB.editing === p.id) return pbEditor(p);
  const lines = p.text.length > 260 ? p.text.slice(0, 260).trimEnd() + "…" : p.text;
  return `<div class="pb-card" draggable="true" data-node="${p.id}">
      <button class="pb-use" data-b="use" data-id="${p.id}" title="Add to the prompt">
        ${withPath ? `<span class="pb-where">${esc(pbPath(p.parent))}</span>` : ""}
        ${p.name ? `<b>${esc(p.name)}</b>` : ""}<span class="pb-text">${esc(lines) || "<i>Empty</i>"}</span>
      </button>
      <div class="pb-card-foot">
        <span class="pb-grip" title="Drag to move" aria-hidden="true">${PB_ICON.grip}</span>
        <button data-b="edit" data-id="${p.id}">${PB_ICON.edit}Edit</button>
        <button data-b="move" data-id="${p.id}" aria-haspopup="menu">${PB_ICON.move}Move</button>
        <button data-b="delPrompt" data-id="${p.id}">${PB_ICON.trash}Delete</button>
        <button data-b="addAfter" data-id="${p.id}" title="Add a new prompt below this one">${PB_ICON.plus}Add</button>
      </div>
    </div>`;
}
function pbEditor(p) {
  return `<div class="pb-card editing" data-node="${p.id}">
      <input class="pb-ed-name" id="pbEdName" placeholder="Name (optional)" value="${esc(p.name || "")}" maxlength="120" aria-label="Name">
      <textarea class="pb-ed-text" id="pbEdText" placeholder="The prompt text" aria-label="Prompt text">${esc(p.text || "")}</textarea>
      <div class="pb-card-foot">
        <span class="hint">${navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+Enter saves</span>
        <button data-b="editCancel">Cancel</button><button class="primary" data-b="editSave" data-id="${p.id}">Save</button>
      </div>
    </div>`;
}
function pbRenderList() {
  const head = pb$("pbListHead"), list = pb$("pbList");
  const q = PB.q.trim().toLowerCase();
  let prompts, addable = !q && PB.sel && pbNode(PB.sel);
  if (q) {
    prompts = PB.lib.nodes.filter((n) => n.kind === "prompt" && (n.text + " " + (n.name || "")).toLowerCase().includes(q));
    const order = (n) => { const p = pbNode(n.parent); const g = p && p.parent ? pbNode(p.parent) : p; return [g ? g.pos : 0, p && p.parent ? p.pos : 0, n.pos]; };
    prompts.sort((a, b) => { const x = order(a), y = order(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; });
    head.innerHTML = `<h3>${prompts.length} found</h3><span class="hint">for “${esc(PB.q.trim())}”</span>`;
  } else if (addable) {
    prompts = pbKids(PB.sel, "prompt");
    const n = pbNode(PB.sel), subs = n.kind === "cat" ? pbKids(n.id, "sub") : [];
    head.innerHTML = `<h3>${esc(pbPath(PB.sel))}</h3><span class="hint">${prompts.length} prompt${prompts.length === 1 ? "" : "s"}${subs.length ? ` here, ${pbCount(n.id) - prompts.length} in its subcategories` : ""}</span>`;
  } else {
    prompts = [];
    head.innerHTML = `<h3>Prompts</h3>`;
  }
  const add = (where) => addable ? `<button class="pb-add" data-b="addPrompt" data-id="${where}">${PB_ICON.plus}Add prompt</button>` : "";
  const fresh = PB.editing && PB.editing.startsWith("new:") ? PB.editing.split(":") : null;   // new:<where>:<before id>
  const freshCard = (spot) => fresh && fresh[1] === spot ? pbEditor({ id: PB.editing, name: "", text: "" }) : "";
  let html = add("top") + freshCard("top");
  for (const p of prompts) {
    if (fresh && fresh[1] === "after" && fresh[2] === p.id) html += pbCard(p, !!q) + freshCard("after");
    else html += pbCard(p, !!q);
  }
  if (!prompts.length) {
    html += q ? `<div class="pb-empty">No prompt has that in it.</div>`
      : addable ? (fresh ? "" : `<div class="pb-empty">No prompts here yet. Add one, or drag one here from another category.</div>`)
      : `<div class="pb-empty">Pick a category on the left${pbCats().length ? "" : ", or make one"}.</div>`;
  }
  html += freshCard("bottom") + (prompts.length ? add("bottom") : "");
  const keep = list.scrollTop;
  list.innerHTML = html;
  list.scrollTop = keep;
  const ed = pb$("pbEdText");
  if (ed && document.activeElement !== ed && document.activeElement?.id !== "pbEdName") { ed.focus(); ed.setSelectionRange(ed.value.length, ed.value.length); }
}

// ---- Opening, the text field, Apply ---------------------------------------------
function openBuilder() {
  PB.open = true; PB.q = ""; PB.editing = null; PB.renaming = null; PB.moveFor = null;
  const text = $("prompt").value;
  PB.original = text;
  pb$("pbText").value = text;
  PB.caret = text.length;
  pb$("pbSearch").value = "";
  pb$("builder").hidden = false;
  pbRender();
  pbLoad();
  setTimeout(() => { const t = pb$("pbText"); t.focus(); t.setSelectionRange(t.value.length, t.value.length); }, 30);
}
function closeBuilder() {
  PB.open = false; pb$("builder").hidden = true; pbMoveMenu(null);
  $("prompt").focus({ preventScroll: true });
}
async function pbCancel() {
  if (pb$("pbText").value !== PB.original && !(await askConfirm("Discard the changes to the prompt?", "The library keeps its changes; only the text in the prompt field is dropped.", "Discard"))) return;
  closeBuilder();
}
function pbApply() {
  const ta = $("prompt");
  ta.value = pb$("pbText").value;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  closeBuilder();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}
// A prompt into the text field where the cursor was, with a space after it (and one before, if it would touch).
function pbInsert(text) {
  const t = pb$("pbText"), v = t.value;
  const at = PB.caret == null || PB.caret > v.length ? v.length : PB.caret;
  const before = v.slice(0, at), after = v.slice(at);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const piece = lead + text.trim() + " ";
  t.value = before + piece + after.replace(/^ /, "");
  PB.caret = at + piece.length;
  t.focus(); t.setSelectionRange(PB.caret, PB.caret);
  pbCountText();
}
function pbCountText() {
  const v = pb$("pbText").value;
  pb$("pbTextCount").textContent = v.length ? `${v.trim() ? v.trim().split(/\s+/).length : 0} words · ${v.length} characters` : "";
  pb$("pbReset").disabled = v === PB.original;
}

// ---- Move menu ------------------------------------------------------------------
function pbMoveMenu(promptId, anchor) {
  const menu = pb$("pbMoveMenu");
  PB.moveFor = promptId;
  if (!promptId) { menu.hidden = true; return; }
  const p = pbNode(promptId);
  menu.innerHTML = `<div class="pb-menu-head">Move to</div>` + pbCats().map((c) => [c, ...pbKids(c.id, "sub")].map((n) =>
    `<button role="menuitem" class="${n.kind}" data-b="moveTo" data-id="${n.id}" ${n.id === p.parent ? 'aria-current="true" disabled' : ""}>${esc(n.name || "Untitled")}</button>`).join("")).join("");
  menu.hidden = false;
  const r = anchor.getBoundingClientRect(), box = pb$("builder").querySelector(".pb").getBoundingClientRect();
  menu.style.left = Math.min(r.left - box.left, box.width - 240) + "px";
  const below = r.bottom - box.top + 4, h = Math.min(menu.scrollHeight, 320);
  menu.style.top = (below + h > box.height - 10 ? r.top - box.top - h - 4 : below) + "px";
  menu.querySelector("button:not([disabled])")?.focus();
}

// ---- Clicks -----------------------------------------------------------------------
const PB_ACT = {
  close: () => pbCancel(),
  cancel: () => pbCancel(),
  apply: () => pbApply(),
  reset: () => { pb$("pbText").value = PB.original; PB.caret = PB.original.length; pbCountText(); pb$("pbText").focus(); },
  sync: async () => { PB.drive = { state: "syncing" }; pbStatus(); try { const r = await api("POST", "prompts/sync"); PB.lib = r.lib; PB.drive = r.drive; } catch (e) { say(e.message); } pbRender(); },
  undo: () => pbDoUndo(),
  select: (id, el, e) => {
    if (e.target.closest(".pb-acts, .pb-fold, .pb-rename")) return;
    PB.sel = id; PB.q = ""; pb$("pbSearch").value = ""; PB.editing = null; pbRender();
  },
  fold: (id) => { PB.folded.has(id) ? PB.folded.delete(id) : PB.folded.add(id); pbRender(); },
  addCat: () => { const c = pbAdd("cat", null, { name: "New category" }); PB.sel = c.id; PB.renaming = c.id; pbChanged(); },
  addSub: (id) => { const s = pbAdd("sub", id, { name: "New subcategory" }); PB.folded.delete(id); PB.sel = s.id; PB.renaming = s.id; pbChanged(); },
  rename: (id) => { PB.renaming = id; pbRender(); },
  delNode: async (id) => {
    const n = pbNode(id), count = pbCount(id), subs = n.kind === "cat" ? pbKids(id, "sub").length : 0;
    if (count || subs) {
      const what = [count ? `${count} prompt${count === 1 ? "" : "s"}` : "", subs ? `${subs} subcategor${subs === 1 ? "y" : "ies"}` : ""].filter(Boolean).join(" and ");
      if (!(await askConfirm(`Delete ${n.name || "this"}?`, `It has ${what} in it. They're deleted too.`, "Delete"))) return;
    }
    const gone = pbRemove(id);
    if (PB.sel && !pbNode(PB.sel)) PB.sel = n.parent || (pbCats()[0] || {}).id || null;
    pbUndoable(`Deleted ${n.name || "the category"}`, gone);
    pbChanged();
  },
  use: (id) => { const p = pbNode(id); if (p) pbInsert(p.text); },
  edit: (id) => { PB.editing = id; pbRender(); },
  addPrompt: (where) => { PB.editing = "new:" + where + ":"; pbRender(); },
  addAfter: (id) => {
    if (PB.q) { const p = pbNode(id); PB.sel = p.parent; PB.q = ""; pb$("pbSearch").value = ""; }
    PB.editing = "new:after:" + id; pbRender();
  },
  editCancel: () => { PB.editing = null; pbRender(); },
  editSave: (id) => {
    const name = pb$("pbEdName").value.trim(), text = pb$("pbEdText").value.trim();
    if (!text) { say("Write the prompt text first."); pb$("pbEdText").focus(); return; }
    if (id.startsWith("new:")) {
      const [, where, after] = id.split(":");
      const sibs = pbKids(PB.sel, "prompt");
      const before = where === "top" ? (sibs[0] || {}).id : where === "after" ? (sibs[sibs.findIndex((s) => s.id === after) + 1] || {}).id : null;
      pbAdd("prompt", PB.sel, { name, text }, before || null);
    } else pbTouch(pbNode(id), { name, text });
    PB.editing = null;
    pbChanged();
  },
  delPrompt: (id) => {
    const p = pbNode(id);
    pbUndoable(`Deleted ${p.name ? "“" + p.name + "”" : "a prompt"}`, pbRemove(id));
    pbReindex(p.parent, "prompt");
    pbChanged();
  },
  move: (id, el) => pbMoveMenu(PB.moveFor === id ? null : id, el),
  moveTo: (target) => {
    const p = pbNode(PB.moveFor), from = p.parent;
    pbPlace(p, target, null); pbReindex(from, "prompt");
    say(`Moved to ${pbPath(target)}.`);
    pbMoveMenu(null); pbChanged();
  },
  saveAs: () => {
    const text = pb$("pbText").value.trim();
    if (!text) { say("Write something in the prompt field first."); return; }
    const target = PB.sel && pbNode(PB.sel) ? PB.sel : (pbCats()[0] || pbAdd("cat", null, { name: "Saved prompts" })).id;
    PB.sel = target; PB.q = ""; pb$("pbSearch").value = "";
    pbAdd("prompt", target, { name: "", text });
    say(`Saved in ${pbPath(target)}.`);
    pbChanged();
  },
};

// ---- Wiring ------------------------------------------------------------------------
{
  const root = pb$("builder");
  root.addEventListener("click", (e) => {
    if (!e.target.closest("#pbMoveMenu, [data-b=move]")) pbMoveMenu(null);
    if (e.target === root) return pbCancel();
    const el = e.target.closest("[data-b]");
    if (!el || el.disabled) return;
    e.stopPropagation();
    PB_ACT[el.dataset.b]?.(el.dataset.id, el, e);
  });
  root.addEventListener("dblclick", (e) => {
    const row = e.target.closest(".pb-row");
    if (row && !e.target.closest(".pb-acts, .pb-fold")) PB_ACT.rename(row.dataset.id);
  });
  const text = pb$("pbText");
  const keepCaret = () => { PB.caret = text.selectionStart; };
  ["keyup", "mouseup", "blur", "input"].forEach((ev) => text.addEventListener(ev, keepCaret));
  text.addEventListener("input", pbCountText);
  pb$("pbSearch").addEventListener("input", (e) => { PB.q = e.target.value; PB.editing = null; pbRenderList(); });
  // Keys inside the window: Escape steps back (a rename, an edit, the menu, then the window), Ctrl/⌘+Enter applies
  // (or saves the prompt being edited), Enter on a rename keeps it. Nothing reaches the app behind.
  root.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (e.target.id === "pbRename") {
      if (e.key === "Enter") { e.preventDefault(); e.target.blur(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); PB.renaming = null; pbRender(); }
      e.stopPropagation();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      if (!pb$("pbMoveMenu").hidden) pbMoveMenu(null);
      else if (PB.editing) { PB.editing = null; pbRender(); }
      else if (PB.q) { PB.q = ""; pb$("pbSearch").value = ""; pbRender(); }
      else pbCancel();
      return;
    }
    if (mod && e.key === "Enter") {
      e.preventDefault(); e.stopPropagation();
      if (PB.editing) PB_ACT.editSave(PB.editing); else pbApply();
      return;
    }
    if (e.key === "Enter" && e.target.classList.contains("pb-row")) { e.preventDefault(); PB_ACT.select(e.target.dataset.id, e.target, e); }
    e.stopPropagation();
  });
  root.addEventListener("focusout", (e) => {
    if (e.target.id !== "pbRename" || !PB.renaming) return;
    const n = pbNode(PB.renaming), name = e.target.value.trim();
    PB.renaming = null;
    if (n && name && name !== n.name) { pbTouch(n, { name }); pbChanged(); } else pbRender();
  });

  // Dragging: prompts onto a category or subcategory (moves it there) or onto another prompt (puts it before that
  // one); categories and subcategories onto each other to reorder them, and a subcategory onto a category to
  // move it into that category.
  const clear = () => root.querySelectorAll(".drop-on, .drop-before").forEach((x) => x.classList.remove("drop-on", "drop-before"));
  root.addEventListener("dragstart", (e) => {
    const el = e.target.closest("[data-node]");
    if (!el || e.target.closest("input, textarea")) return;
    PB.drag = el.dataset.node;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", pbNode(PB.drag)?.text || pbNode(PB.drag)?.name || "");
    el.classList.add("dragging");
  });
  root.addEventListener("dragend", (e) => { e.target.closest?.("[data-node]")?.classList.remove("dragging"); PB.drag = null; clear(); });
  const dropFor = (e) => {
    const drag = PB.drag && pbNode(PB.drag), el = e.target.closest("[data-node]");
    if (!drag) return null;
    if (!el) return e.target.closest("#pbList") && drag.kind === "prompt" && PB.sel && !PB.q ? { into: PB.sel, el: null } : null;
    const over = pbNode(el.dataset.node);
    if (!over || over.id === drag.id) return null;
    if (drag.kind === "prompt") return over.kind === "prompt" ? (PB.q ? null : { into: over.parent, before: over.id, el }) : { into: over.id, el, on: true };
    if (drag.kind === "cat") return over.kind === "cat" ? { into: null, before: over.id, el } : null;
    if (drag.kind === "sub") return over.kind === "sub" ? { into: over.parent, before: over.id, el } : over.kind === "cat" ? { into: over.id, el, on: true } : null;
    return null;
  };
  root.addEventListener("dragover", (e) => {
    const d = dropFor(e);
    if (!d) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    clear();
    if (d.el) d.el.classList.add(d.on ? "drop-on" : "drop-before");
  });
  root.addEventListener("drop", (e) => {
    const d = dropFor(e);
    clear();
    if (!d) return;
    e.preventDefault();
    const n = pbNode(PB.drag), from = n.parent;
    pbPlace(n, d.into, d.before || null);
    if (from !== d.into) pbReindex(from, n.kind);
    if (n.kind === "prompt" && from !== d.into) say(`Moved to ${pbPath(d.into)}.`);
    PB.drag = null;
    pbChanged();
  });
}
