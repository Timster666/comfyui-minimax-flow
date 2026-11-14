# MiniMax H3 Vision

A simple prompt-and-gallery studio for MiniMax H3 videos and MOSS-Image pictures, inside ComfyUI.

Open it from the **MiniMax H3 Vision** button in ComfyUI's sidebar (or View → Open MiniMax H3 Vision).
It opens full screen over ComfyUI; the × in the top-right corner brings you back.

## Video and Image

The app has two tabs, and it always opens on **Video**.

- **Video** makes clips with MiniMax H3, through the H3 editor pack (comfyui-h3-editor). Every
  `@image` in the prompt is a reference picture.
- **Image** makes pictures with MOSS-Image, which ComfyUI supports natively. It's good at words in
  pictures: put the text you want in quotes. A finished image can be sent to the Video tab with
  **Use in a video**, and the Video tab's **Add image** pop-up lists all of them under
  **Generated images**.

## Real and fake renders

Each tab picks by itself (`"mode": "auto"` in `config.json`):

- **Video is real** when the H3 editor pack and at least one of its models are installed.
  MiniMax H3 Vision hands each clip to the H3 editor's own graph builder, so a render is built
  the way the editor builds one, and then sets the size, steps, LoRAs and file locations to your
  settings. While a clip renders you see its steps and the H3 editor's moving live preview.
- **Image is real** when MOSS-Image's three files are installed (below). The graph follows
  Comfy-Org's MOSS-Image template (er_sde / simple, the recommended
  negative prompt), at 50 steps and guidance 3.0. While an image renders you see its steps and ComfyUI's live preview.
- **Fake** otherwise: a fake render counts through its steps and then hands out a sample from
  `samples/`, so the app can be tried on any machine. The tab shows a "Fake renders" badge;
  click it to see why.

`"mode"` (both tabs) or `"image_mode"` (Image only) can be `"real"` or `"fake"` to force one.

## MOSS-Image files

From [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image), into ComfyUI's `models` folder:

| File | Folder |
|---|---|
| `moss_image_sft_int8_convrot.safetensors` | `models/diffusion_models/` |
| `qwen3.5_9b_w4a8_gptq.safetensors` | `models/text_encoders/` |
| `flux2-vae_small.safetensors` | `models/vae/` |

Any other MOSS-Image checkpoint in `models/diffusion_models` (BF16, Base, a finetune) shows up
in the Image tab's model list as well.

**Turbo (optional).** [Spectrallic's 8-step DMD checkpoint](https://huggingface.co/Spectrallic/MOSS-Image-SFT-8step-DMD-int8-convrot)
(`moss_image_sft_8stepdmd_int8_convrot.safetensors`, 5.4 GB) also goes in `models/diffusion_models/`
and uses the same text encoder and VAE. It shows up as "MOSS-Image SFT Turbo 8-step (INT8)" and
renders with 8 steps, guidance 1, Euler and no negative prompt. Any checkpoint whose file name
has "dmd", "turbo" or "8step" gets these turbo settings.

## File names and importing

Finished files are named after the start of their prompt, like
`vision_a_detective_in_a_long_coat_walks_00001_.mp4`, and downloads keep that name.

Each file also carries its prompt and settings inside it (ComfyUI's metadata, under `mmflow`).
**Import**, next to the All and Starred filters, brings a video or image back into the grid with
them, ready for **Use this prompt and settings**. You can also drop files on the page: videos
anywhere, images in the Image tab. Older files without `mmflow` work too when ComfyUI saved its
workflow in them: clips from earlier versions of this app or from the H3 editor, and MOSS-Image
images. Their settings are worked out from that workflow. Files saved with ComfyUI's
`--disable-metadata` have nothing to read.

## Where things are kept

In ComfyUI's output folder, under `minimax_flow/`:

- `project.json`: your images, clips and last-used settings
- `images/`: uploaded reference images
- `clips/`: finished videos
- `stills/`: finished images
- `preview/`: live previews while rendering (the H3 editor cleans these up)

## Files

- `__init__.py`: tells ComfyUI about the plugin
- `flow_server.py`: the plugin's server: its API, storage and fake renderer
- `h3_bridge.py`: real videos, through the H3 editor pack
- `clip_info.py`: the prompt and settings saved in each file, and reading them back on import
- `moss_bridge.py`: real images, with MOSS-Image
- `comfy_link.py`: sending jobs to ComfyUI's queue, following and stopping them
- `web/minimax_flow_launcher.js`: the sidebar button
- `app/index.html`, `app/app.js`: the app itself
