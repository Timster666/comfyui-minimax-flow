"""Edit projects as zip files: the Edit tab's timeline and the videos it uses, to move or keep.

A project zip holds:
    project.json   {"app": "MiniMax H3 Vision", "kind": "edit-project", "version": 1,
                    "edit": {name, ratio, muted, pieces: [{id, clip, in, out}]},
                    "clips": [the clip records (prompt and settings), each with "zip_file"]}
    clips/<file>   each video the timeline uses, whole, so it can be trimmed differently later

No ComfyUI imports, so it can be tested anywhere.
"""

import json
import os
import re
import shutil
import uuid
import zipfile

KIND = "edit-project"
VERSION = 1
# What a clip keeps when it travels in a project.
CLIP_FIELDS = ("id", "kind", "prompt", "model", "profile", "steps", "cfg", "res", "aspect", "len", "width", "height",
               "seed", "loras", "starred", "created", "imported", "found")


class Bad(Exception):
    """Not a project zip this app can open; the message says why."""


def pack(edit, clips, clip_path, out_path):
    """Write the edit and the videos it uses to out_path (a .zip). clips: id -> clip record."""
    used = []
    for piece in edit.get("pieces") or []:
        if piece["clip"] in clips and piece["clip"] not in used:
            used.append(piece["clip"])
    records, names = [], set()
    temp = out_path + ".part"
    try:
        # Videos are compressed already, so they are stored as they are.
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            for clip_id in used:
                clip = clips[clip_id]
                path = clip_path(clip)
                if not os.path.exists(path):
                    continue
                name = os.path.basename(clip["file"])
                stem, ext = os.path.splitext(name)
                n = 2
                while name in names:
                    name = "%s_%d%s" % (stem, n, ext)
                    n += 1
                names.add(name)
                z.write(path, "clips/" + name)
                record = {k: clip[k] for k in CLIP_FIELDS if k in clip}
                record["zip_file"] = "clips/" + name
                records.append(record)
            kept = {r["id"] for r in records}
            project = {"app": "MiniMax H3 Vision", "kind": KIND, "version": VERSION,
                       "edit": {"name": edit.get("name") or "Untitled edit", "ratio": edit.get("ratio") or "16:9",
                                "muted": bool(edit.get("muted")),
                                "pieces": [p for p in edit.get("pieces") or [] if p["clip"] in kept]},
                       "clips": records}
            z.writestr("project.json", json.dumps(project, indent=1))
        os.replace(temp, out_path)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
    return {"clips": len(records), "pieces": len(project["edit"]["pieces"])}


def read(zip_path):
    """The project.json of a project zip, checked."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            try:
                project = json.loads(z.read("project.json"))
            except KeyError:
                raise Bad("This zip isn't a MiniMax H3 Vision project: it has no project.json.")
            listed = set(z.namelist())
    except zipfile.BadZipFile:
        raise Bad("This file isn't a zip, or it is damaged.")
    except ValueError:
        raise Bad("The project.json in this zip can't be read.")
    if project.get("kind") != KIND:
        raise Bad("This zip isn't a MiniMax H3 Vision edit project.")
    if int(project.get("version", 0)) > VERSION:
        raise Bad("This project was saved by a newer MiniMax H3 Vision. Update the plugin to open it.")
    for record in project.get("clips") or []:
        if record.get("zip_file") not in listed:
            raise Bad("The project lists %s, but it isn't in the zip." % record.get("zip_file"))
    return project


def unpack(zip_path, existing, clips_dir, clip_path):
    """Bring a project's videos in. Returns (edit, new clip records, how many were already here).

    existing: id -> clip record already in the app. A video whose id is already here with a file of
    the same size is the same video, so it isn't copied again. Others get a new id if theirs is taken.
    """
    project = read(zip_path)
    os.makedirs(clips_dir, exist_ok=True)
    added, same, ids = [], 0, {}
    with zipfile.ZipFile(zip_path) as z:
        for record in project.get("clips") or []:
            info = z.getinfo(record["zip_file"])
            mine = existing.get(record.get("id"))
            if mine and mine.get("file") and os.path.exists(clip_path(mine)) and os.path.getsize(clip_path(mine)) == info.file_size:
                ids[record["id"]] = mine["id"]
                same += 1
                continue
            name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(record["zip_file"])) or "clip.mp4"
            stem, ext = os.path.splitext(name)
            n = 2
            while os.path.exists(os.path.join(clips_dir, name)):
                name = "%s_%d%s" % (stem, n, ext)
                n += 1
            with z.open(info) as src, open(os.path.join(clips_dir, name), "wb") as dst:
                shutil.copyfileobj(src, dst, 4 << 20)
            clip_id = record.get("id") if record.get("id") and record.get("id") not in existing else "c" + uuid.uuid4().hex[:10]
            clip = {k: record[k] for k in CLIP_FIELDS if k in record}
            clip.update({"id": clip_id, "kind": "video", "status": "done", "step": record.get("steps", 0), "file": name})
            clip.setdefault("prompt", "")
            clip.setdefault("starred", False)
            ids[record["id"]] = clip_id
            existing = dict(existing, **{clip_id: clip})
            added.append(clip)
    edit = project.get("edit") or {}
    edit = {"name": str(edit.get("name") or "Untitled edit")[:80], "ratio": edit.get("ratio") if edit.get("ratio") in ("16:9", "9:16", "1:1") else "16:9",
            "muted": bool(edit.get("muted")),
            "pieces": [{"id": str(p["id"])[:40], "clip": ids[p["clip"]], "in": float(p["in"]), "out": float(p["out"])}
                       for p in edit.get("pieces") or [] if p.get("clip") in ids]}
    return edit, added, same
