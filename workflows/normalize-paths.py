#!/usr/bin/env python3
"""Normalize Windows backslash paths in ComfyUI workflow JSONs to forward slashes.

Kijai's workflows reference model files with Windows-style backslashes
(`WanVideo\\2_2\\file.safetensors`). When loaded on a Linux pod, ComfyUI scans
folders and lists files with forward slashes, and the strict string comparison
fails → workflow reports the model as missing even though the file exists.

This script walks every node's `widgets_values` and converts any string that
looks like a model path (ends in `.safetensors`, `.ckpt`, `.pt`, etc.) from
`\\` to `/`. Idempotent — re-running is a no-op on already-normalized files.

Usage:
  python normalize-paths.py [json1.json json2.json ...]
  python normalize-paths.py            # processes all *.json in this folder
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

MODEL_SUFFIXES = (".safetensors", ".ckpt", ".pt", ".bin", ".pth", ".gguf", ".onnx")


def normalize_value(v):
    """Convert Windows path separators to forward slashes if string looks like a model ref."""
    if isinstance(v, str) and v.endswith(MODEL_SUFFIXES) and "\\" in v:
        return v.replace("\\", "/")
    if isinstance(v, list):
        return [normalize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: normalize_value(x) for k, x in v.items()}
    return v


def normalize_workflow(path: pathlib.Path) -> int:
    """Returns number of paths converted. Writes file in-place if changes happened."""
    wf = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for node in wf.get("nodes", []):
        wv = node.get("widgets_values")
        if wv is None:
            continue
        new_wv = normalize_value(wv)
        if new_wv != wv:
            # Count the diffs
            old_str = json.dumps(wv)
            new_str = json.dumps(new_wv)
            count += sum(1 for o, n in zip(old_str, new_str) if o != n and o == "\\")
            node["widgets_values"] = new_wv
    if count > 0:
        path.write_text(json.dumps(wf, indent=2), encoding="utf-8")
    return count


def main() -> None:
    here = pathlib.Path(__file__).parent
    args = sys.argv[1:] if len(sys.argv) > 1 else sorted(str(p) for p in here.glob("*.json"))
    total = 0
    for p in args:
        path = pathlib.Path(p)
        if not path.exists():
            print(f"  skip (not found): {p}")
            continue
        changed = normalize_workflow(path)
        status = f"normalized {changed} paths" if changed else "no changes"
        print(f"  {path.name}: {status}")
        total += changed
    print(f"\nTotal: {total} backslash paths converted to forward slashes")


if __name__ == "__main__":
    main()
