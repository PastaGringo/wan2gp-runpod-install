#!/usr/bin/env python3
"""Augment Kijai's wan22_14B_I2V workflow with RIFE 2x interpolation + longer scenes.

Takes:  wan22_14B_I2V.json   (Kijai's official Wan 2.2 14B I2V example)
Builds: wan22_14B_I2V_longscene.json

Changes:
  - Frame count   81  → 161  (≈10s @ 16fps base, 5s @ 32fps after RIFE)
  - Output fps    16  → 32   (matches RIFE 2x multiplier)
  - Inserts a RIFE VFI node between WanVideoDecode and VHS_VideoCombine

NOT done programmatically (sliding window context options): I'd need the exact
WanVideoContextOptions widget signature, and it changes between Kijai's revisions.
To add it: open the workflow in ComfyUI, right-click the empty `context_options`
input on either WanVideoSampler, choose "Add Node → WanVideoWrapper → Context
Options". Then click both sampler `context_options` inputs to connect them to it.
Defaults are sensible: context_frames=81, overlap=16, schedule=uniform_standard.

Usage:
  python build-longscene.py            # writes wan22_14B_I2V_longscene.json
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

# Force UTF-8 stdout on Windows (default cp1252 chokes on emoji/arrows)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = pathlib.Path(__file__).parent
SOURCE = HERE / "wan22_14B_I2V.json"
TARGET = HERE / "wan22_14B_I2V_longscene.json"


def main() -> None:
    wf = json.loads(SOURCE.read_text(encoding="utf-8"))

    next_node_id = wf["last_node_id"] + 1
    next_link_id = wf["last_link_id"] + 1

    # Find existing nodes we need to rewire
    decode_node = next(n for n in wf["nodes"] if n["type"] == "WanVideoDecode")
    combine_node = next(n for n in wf["nodes"] if n["type"] == "VHS_VideoCombine")
    encode_node = next(n for n in wf["nodes"] if n["type"] == "WanVideoImageToVideoEncode")
    # The workflow has GetImageSizeAndCount between Decode and VideoCombine.
    # We insert RIFE between it and VideoCombine.
    size_node = next(n for n in wf["nodes"] if n["type"] == "GetImageSizeAndCount")

    # ── Change frame count: encode_node.widgets_values[2] from 81 → 161
    if encode_node.get("widgets_values"):
        # widgets_values layout: [width, height, num_frames, ...]
        if len(encode_node["widgets_values"]) >= 3 and encode_node["widgets_values"][2] == 81:
            encode_node["widgets_values"][2] = 161

    # ── Change output fps in VHS_VideoCombine
    cv = combine_node.get("widgets_values") or {}
    if isinstance(cv, dict):
        if cv.get("frame_rate") in (16, 24):
            cv["frame_rate"] = cv["frame_rate"] * 2  # RIFE 2x
        if isinstance(cv.get("videopreview"), dict):
            params = cv["videopreview"].get("params") or {}
            if params.get("frame_rate") in (16, 24):
                params["frame_rate"] = params["frame_rate"] * 2

    # ── Find the link that goes GetImageSizeAndCount → VideoCombine
    images_link_id = None
    for link in wf["links"]:
        # link layout: [link_id, src_node, src_slot, dst_node, dst_slot, type]
        if link[1] == size_node["id"] and link[3] == combine_node["id"]:
            images_link_id = link[0]
            break

    if images_link_id is None:
        raise SystemExit(
            "Could not find link GetImageSizeAndCount -> VHS_VideoCombine -- workflow shape changed?"
        )

    # ── Add RIFE VFI node between GetImageSizeAndCount and VideoCombine
    rife_id = next_node_id
    next_node_id += 1
    rife_node = {
        "id": rife_id,
        "type": "RIFE VFI",
        "pos": [size_node["pos"][0] + 380, size_node["pos"][1]],
        "size": [320, 198],
        "flags": {},
        "order": 99,
        "mode": 0,
        "inputs": [
            {"name": "frames", "type": "IMAGE", "link": images_link_id, "label": "frames"},
            {"name": "optional_interpolation_states", "type": "INTERPOLATION_STATES", "link": None, "shape": 7},
        ],
        "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": [next_link_id], "slot_index": 0}
        ],
        "properties": {"Node name for S&R": "RIFE VFI", "cnr_id": "comfyui-frame-interpolation"},
        # Widget order from ComfyUI-Frame-Interpolation source:
        #   [ckpt_name, clear_cache_after_n_frames, multiplier, fast_mode, ensemble, scale_factor]
        "widgets_values": ["rife47.pth", 10, 2, True, True, 1.0],
    }
    wf["nodes"].append(rife_node)

    # ── Rewire: link `images_link_id` was GetImageSizeAndCount -> VideoCombine.
    # Re-target it to GetImageSizeAndCount -> RIFE.frames
    for link in wf["links"]:
        if link[0] == images_link_id:
            link[3] = rife_id
            link[4] = 0  # RIFE's "frames" input is at slot 0
            break

    # And add a new link RIFE -> VideoCombine.images
    new_link = [next_link_id, rife_id, 0, combine_node["id"], 0, "IMAGE"]
    wf["links"].append(new_link)
    next_link_id += 1

    # Update VideoCombine's input link reference
    for inp in combine_node["inputs"]:
        if inp.get("name") == "images":
            inp["link"] = new_link[0]
            break

    # ── Bookkeeping
    wf["last_node_id"] = next_node_id - 1
    wf["last_link_id"] = next_link_id - 1

    # Add a Note node telling the user about sliding window
    note_id = next_node_id
    next_node_id += 1
    wf["last_node_id"] = note_id
    note_node = {
        "id": note_id,
        "type": "Note",
        "pos": [decode_node["pos"][0] - 200, decode_node["pos"][1] - 250],
        "size": [400, 200],
        "flags": {},
        "order": 0,
        "mode": 0,
        "inputs": [],
        "outputs": [],
        "properties": {},
        "widgets_values": [
            "LONG SCENE WORKFLOW\n\n"
            "✓ RIFE 2x frame interpolation added (24→48fps smooth)\n"
            "✓ Frame count bumped to 161 (≈10s at 16fps source)\n\n"
            "TO ADD SLIDING WINDOW for 20-60s scenes:\n"
            "1. Right-click an empty area → Add Node →\n"
            "   WanVideoWrapper → Context Options\n"
            "2. Connect its output to BOTH WanVideoSampler's\n"
            "   `context_options` input\n"
            "3. Defaults are good: context_frames=81, overlap=16\n"
            "4. Bump frame count to 321+ for a 20s+ scene\n\n"
            "VRAM tip: enable 'vae_per_chunk' on the context node\n"
            "if you run out of memory."
        ],
        "color": "#432",
        "bgcolor": "#653",
    }
    wf["nodes"].append(note_node)
    wf["last_node_id"] = next_node_id - 1

    TARGET.write_text(json.dumps(wf, indent=2), encoding="utf-8")
    print(f"✅ Wrote {TARGET.name}")
    print(f"   nodes:  {len(wf['nodes'])} (added RIFE VFI + Note)")
    print(f"   links:  {len(wf['links'])}")


if __name__ == "__main__":
    main()
