#!/usr/bin/env python3
"""Build wan22_14B_I2V_longscene_720p.json from the existing longscene workflow.

Modifies the 14B I2V longscene workflow to:
  - Use the new Kijai Wan22_Lightx2v HIGH/LOW step-distill LoRAs (720p-compatible)
    instead of the 480p Lightx2v that was breaking at non-480p resolutions
  - Set target resolution to 1280x720 (16:9 HD)
  - Enable save_output on VHS_VideoCombine so the mp4 lands in /workspace/.../output/
  - LoRA strengths back to 1.0 (the new Wan22 LoRAs use canonical strength)

LoRA chain mapping (verified by tracing links in source workflow):
  LoraSelect id=56 → SetLoRAs id=80 → Sampler id=27 = HIGH (first pass, sigma 1.0→~0.6)
  LoraSelect id=97 → SetLoRAs id=79 → Sampler id=90 = LOW (second pass, sigma ~0.6→0)

Usage:
  python build-longscene-720p.py     # writes wan22_14B_I2V_longscene_720p.json
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = pathlib.Path(__file__).parent
SOURCE = HERE / "wan22_14B_I2V_longscene.json"
TARGET = HERE / "wan22_14B_I2V_longscene_720p.json"

# New 720p-compatible LoRAs (Kijai Wan22_Lightx2v, separate experts for HIGH/LOW)
LORA_HIGH = "WanVideo\\Wan22_Lightx2v\\Wan_2_2_I2V_A14B_HIGH_lightx2v_4step_lora_260412_rank_64_fp16.safetensors"
LORA_LOW = "WanVideo\\Wan22_Lightx2v\\Wan_2_2_I2V_A14B_LOW_lightx2v_4step_lora_260412_rank_64_fp16.safetensors"


def main() -> None:
    wf = json.loads(SOURCE.read_text(encoding="utf-8"))

    changes = []

    for n in wf["nodes"]:
        nid, ntype = n["id"], n["type"]

        # 1. LoraSelect id=56 → HIGH sampler (id=27)
        if ntype == "WanVideoLoraSelect" and nid == 56:
            n["widgets_values"][0] = LORA_HIGH
            n["widgets_values"][1] = 1.0  # strength
            changes.append(f"LoraSelect id=56 (HIGH chain) → {LORA_HIGH.split('\\\\')[-1]} strength=1.0")

        # 2. LoraSelect id=97 → LOW sampler (id=90)
        elif ntype == "WanVideoLoraSelect" and nid == 97:
            n["widgets_values"][0] = LORA_LOW
            n["widgets_values"][1] = 1.0
            changes.append(f"LoraSelect id=97 (LOW chain) → {LORA_LOW.split('\\\\')[-1]} strength=1.0")

        # 3. ImageResizeKJv2: target resolution 1280x720 (was 720x720)
        elif ntype == "ImageResizeKJv2":
            old_w, old_h = n["widgets_values"][0], n["widgets_values"][1]
            n["widgets_values"][0] = 1280
            n["widgets_values"][1] = 720
            changes.append(f"ImageResizeKJv2 id={nid} → 1280x720 (was {old_w}x{old_h})")

        # 4. VHS_VideoCombine: save_output = true so the mp4 persists in output/
        elif ntype == "VHS_VideoCombine":
            wv = n.get("widgets_values") or {}
            if isinstance(wv, dict):
                old = wv.get("save_output")
                wv["save_output"] = True
                # Also set sensible filename prefix
                if not wv.get("filename_prefix") or wv["filename_prefix"] == "WanVideo2_2_I2V":
                    wv["filename_prefix"] = "Wan2_2_14B_I2V_720p"
                changes.append(f"VHS_VideoCombine id={nid} save_output {old} → True, prefix=Wan2_2_14B_I2V_720p")

    TARGET.write_text(json.dumps(wf, indent=2), encoding="utf-8")
    print(f"Wrote {TARGET.name}")
    print(f"Source: {SOURCE.name} ({len(wf['nodes'])} nodes, {len(wf['links'])} links)")
    print()
    print("Changes:")
    for c in changes:
        print(f"  - {c}")


if __name__ == "__main__":
    main()
