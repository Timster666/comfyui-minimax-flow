# MiniMax H3 Vision

A simple prompt-and-gallery video studio for MiniMax H3, inside ComfyUI.

Open it from the **MiniMax H3 Vision** button in ComfyUI's sidebar (or View → Open MiniMax H3 Vision).
It opens full screen over ComfyUI; the × in the top-right corner brings you back.

## Real and fake renders

MiniMax H3 Vision picks by itself (`"mode": "auto"` in `config.json`):

- **Real renders** when the H3 editor pack (comfyui-h3-editor) and at least one of its models
  are installed. MiniMax H3 Vision hands each clip to the H3 editor's own graph builder, so a render
  is built the way the editor builds one, and then sets the size, steps, LoRAs and file
  locations to your settings. Every `@image` in the prompt becomes a reference picture.
  While a clip renders you see its steps and the H3 editor's moving live preview.
- **Fake renders** otherwise. A fake render counts through its steps like a real one and then
  hands out a sample clip from `samples/`, so the app can be tried on any machine. The header
  shows a "Fake renders" badge.

`"mode": "real"` or `"mode": "fake"` forces one. For fake renders,
`fake_seconds_per_step` and `fake_load_seconds` set how long they take.

## Where things are kept

In ComfyUI's output folder, under `minimax_flow/`:

- `project.json`: your images, clips and last-used settings
- `images/`: uploaded reference images
- `clips/`: finished videos
- `preview/`: live previews while rendering (the H3 editor cleans these up)

## Files

- `__init__.py`: tells ComfyUI about the plugin
- `flow_server.py`: the plugin's server: its API, storage and fake renderer
- `h3_bridge.py`: real renders, through the H3 editor pack
- `web/minimax_flow_launcher.js`: the sidebar button
- `app/index.html`, `app/app.js`: the app itself
