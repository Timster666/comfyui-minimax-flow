// Adds a "MiniMax Flow" button to ComfyUI's sidebar. It opens the app full screen
// over ComfyUI; the app's own Close button (or Esc on an empty screen) closes it again.
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const FRAME_ID = "mmflow-frame";

function closeFlow() {
  document.getElementById(FRAME_ID)?.remove();
}

function openFlow() {
  if (document.getElementById(FRAME_ID)) return;
  const frame = document.createElement("iframe");
  frame.id = FRAME_ID;
  // A new address each time, so the browser never shows an old copy after an update.
  frame.src = "/mmflow/app/index.html?v=" + Date.now();
  frame.title = "MiniMax Flow";
  frame.allow = "fullscreen; clipboard-write";
  frame.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;border:0;z-index:10000;background:#121316";
  document.body.appendChild(frame);
}

// Clip updates go to every window; this keeps the graph page from logging them as unknown.
api.addEventListener("mmflow.clip", () => {});

window.addEventListener("message", (event) => {
  if (event.origin === location.origin && event.data === "mmflow-close") closeFlow();
});

app.registerExtension({
  name: "minimax.flow",
  commands: [{ id: "MiniMaxFlow.Open", label: "Open MiniMax Flow", icon: "pi pi-video", function: openFlow }],
  menuCommands: [{ path: ["View"], commands: ["MiniMaxFlow.Open"] }],
  setup() {
    app.extensionManager?.registerSidebarTab?.({
      id: "minimax-flow",
      icon: "pi pi-video",
      title: "MiniMax Flow",
      tooltip: "Open MiniMax Flow",
      type: "custom",
      render(element) {
        const button = document.createElement("button");
        button.textContent = "Open MiniMax Flow";
        button.style.cssText = "margin:16px;padding:10px 16px;border-radius:8px;border:0;cursor:pointer;background:#e0a24a;color:#121316;font:600 14px system-ui";
        button.addEventListener("click", openFlow);
        element.replaceChildren(button);
        openFlow();
      },
    });
  },
});
