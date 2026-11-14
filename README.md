# MiniMax H3 Vision

A simple prompt-and-gallery studio for MiniMax H3 videos and MOSS-Image pictures, inside ComfyUI.

Open it from the **MiniMax H3 Vision** button in ComfyUI's sidebar (or View → Open MiniMax H3 Vision).
It opens full screen over ComfyUI; the × in the top-right corner brings you back.

## Install

In ComfyUI's `custom_nodes` folder:

```bash
git clone https://github.com/Timster666/comfyui-minimax-h3-vision
```

then restart ComfyUI. To update later, run `git pull` in that folder (and restart ComfyUI when
Python files changed). It needs nothing else installed: no other node pack, and no extra Python
packages beyond ComfyUI's own.

The repository used to be called `comfyui-minimax-flow`; that address still leads here, and a copy
cloned under the old name keeps working.

## Video, Image and Edit

The app has three tabs, and it always opens on **Video**.

- **Video** makes clips with MiniMax H3. Every `@image` in the prompt is a reference picture.
- **Image** makes pictures with MOSS-Image. It's good at words in pictures: put the text you want
  in quotes. A finished image can be sent to the Video tab with **Use in a video**, and the Video
  tab's **Add image** pop-up lists all of them under **Generated images**.
- **Edit** puts your videos together, like a very simple CapCut: your videos on the left, a player
  on the right and one timeline track below. Add a video with its **+**, by double-clicking it or
  with Enter (it goes into the gap between clips nearest the playhead), or drag it onto the timeline
  (it goes into the gap nearest where you drop it); drag clips to move them and their edges to trim them. **S**
  splits at the playhead, **⌫** deletes, **⌘Z** undoes, Space plays. **Export** saves one MP4
  (H.264 and AAC, 24 fps) in `minimax_flow/exports/`, at the videos' own size, 720p or 1080p; videos
  of another shape get black bars. It downloads by itself when done. Quality is visually lossless:
  CRF 16, and clips already at the export size are passed to the encoder without any conversion.
  The edit is saved as you go.
  **Project → Export project** saves the timeline, the videos it uses and their reference images
  (the `@images` in their prompts) as one `.zip` (in
  `minimax_flow/exports/`, and it downloads). **Import project** opens such a zip on any machine: its
  videos join your Video gallery and the Edit tab, its reference images join **Add image**, and its
  timeline replaces the current one (it asks first). If an image's name is taken, it gets a number (`detective2`) and
  the videos' prompts are changed to match, so **Use this prompt and settings** finds the right picture. Videos
  that are already there aren't copied twice.

It needs no other custom node pack. Videos run on ComfyUI's own MiniMax H3 nodes plus a few of
this pack's own (in the node menu under **MiniMax H3 Vision**), and images on ComfyUI's own nodes.
A tab whose models aren't installed shows a **Not installed** badge; click it to see which files
are missing.

While a clip renders you see its steps and a moving live preview (with `taeh3`, below). While an
image renders you see its steps and ComfyUI's live preview. MOSS-Image follows Comfy-Org's template
(er_sde / simple, the recommended negative prompt), at 50 steps and guidance 3.0.

## MiniMax H3 files

The models the Video tab needs, into ComfyUI's `models` folder:

| File | Folder | Where to get it |
|---|---|---|
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders/` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3), `text_encoders/` |
| `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` | Comfy-Org/MiniMax-H3, `vae/` |
| `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` | Comfy-Org/MiniMax-H3, `vae/` |

and at least one model (each model in the Video tab's list is one of these families):

| Model | Files |
|---|---|
| HyperFlow H3 Flash | `minimax_h3_ref2va_hyperflow_flash_14b-int8_convrot.safetensors` and `minimax_h3_fl2va_hyperflow_flash_14b-int8_convrot.safetensors` in `models/diffusion_models/hyperflow/`, from [VideoRebirthAI/hyperflow-h3-flash](https://huggingface.co/VideoRebirthAI/hyperflow-h3-flash). Its GGUF files don't load here. |
| MiniMax H3 | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` and `minimax_h3_fl2va_pruned_int8_convrot.safetensors` in `models/diffusion_models/` |
| MiniMax H3 Turbo | the same two, and `minimax_h3_turbo_v4_step600_ema.safetensors` in `models/loras/` |
| Singularity v2, DaSiWa, 10Eros, PRISM | their checkpoint in `models/diffusion_models/` (names in `h3_graph.py`) |

The text encoder and video VAE in other precisions (`..._int8_convrot`, `..._bf16`) work too.
Optional: `taeh3.safetensors` in `models/vae_approx/`, from [OzzyGT/taeh3](https://huggingface.co/OzzyGT/taeh3),
for the moving live preview.

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

To move a video together with its reference images, open it and click **Export .zip** (or
right-click it). The `.zip` holds the video and the pictures its `@images` name, and downloads. Import it
with **Import** or by dropping it on the page, here or on another machine: the video joins the grid and
its pictures join **Add image**, renamed with a number if the name is taken, just like a project. The
Edit tab's **Import project** takes these zips too, and adds the video to the media panel.

## Where things are kept

In ComfyUI's output folder, under `minimax_flow/`:

- `project.json`: your images, clips and last-used settings
- `images/`: uploaded reference images
- `clips/`: finished videos
- `stills/`: finished images
- `exports/`: exported edits
- `strips/`: the timeline's filmstrips and waveforms (made again when deleted)
- `preview/`: live previews while rendering (cleaned up as it goes)

## Files

- `__init__.py`: tells ComfyUI about the plugin and its nodes
- `flow_server.py`: the plugin's server: its API and storage
- `h3_graph.py`: video graphs: the MiniMax H3 model families and the prompt H3 expects
- `vision_nodes.py`: the pack's graph nodes, with `h3_lora.py` (LoRAs), `hyperflow.py` (HyperFlow H3 Flash)
  and `live_preview.py` (the moving preview)
- `moss_bridge.py`: image graphs, with MOSS-Image
- `edit_media.py`: the Edit tab's filmstrips, waveforms and export, with PyAV
- `edit_project.py`: edit projects as zip files (export and import)
- `clip_info.py`: the prompt and settings saved in each file, and reading them back on import
- `comfy_link.py`: sending jobs to ComfyUI's queue, following and stopping them
- `web/minimax_flow_launcher.js`: the sidebar button
- `app/index.html`, `app/app.js`: the app itself, and `app/edit.js`: its Edit tab

## License

GPL-3.0 (`LICENSE`). The video code is adapted from the
[H3 Editor](https://github.com/igorls/comfyui-h3-editor) by Igor Lins e Silva, which includes work
from ComfyUI-HyperFlow-Flash and ComfyUI-MiniMax-H3-Turbo. `NOTICE` lists what came from where.
