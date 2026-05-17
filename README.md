# Wan2GP RunPod Installer

**One command from your laptop → a Wan2GP UI live in ~8 minutes.** Turnkey installer + Python deployer for [Wan2GP](https://github.com/deepbeepmeep/Wan2GP) (Wan 2.1 / Wan 2.2 video generation, low-VRAM optimized UI by DeepBeepMeep) on RunPod GPU pods.

Handles all the gotchas I hit setting this up by hand:

- PyTorch **2.10 + cu128** (overrides the template's 2.8 — Wan2GP docs warn about a RAM leak)
- `hf_transfer` installed (RunPod sets `HF_HUB_ENABLE_HF_TRANSFER=1` by default — without the package, every model download throws)
- SageAttention built **with `--no-build-isolation`** (its `setup.py` imports torch — fails in pip's isolated build env)
- Auto-picks SageAttention version: **1.0.6 prebuilt for RTX 30xx**, **2.x source-built for RTX 40xx / 50xx / H100**
- **Auto-install on pod boot** via `docker_args`: pod spawn → install → wgp.py launched, all without SSHing in

## Quick start — deploy a pod from your terminal

If you have [uv](https://docs.astral.sh/uv/) installed, the `deploy-pod.py` script in this repo creates a Wan2GP-ready pod with one command (no Truss, no clicking in the RunPod UI):

```bash
# One-time: set your RunPod API key (get it at runpod.io/console/user/settings)
$env:RUNPOD_API_KEY = "rpa_..."     # PowerShell session
setx RUNPOD_API_KEY "rpa_..."        # Windows persistent (restart shell)
export RUNPOD_API_KEY="rpa_..."      # bash/zsh

# Deploy a RTX 5090 pod (default, ~0.99 $/hr)
# By default, auto-install runs inside the pod — Wan2GP boots automatically.
uv run deploy-pod.py

# Other commands
uv run deploy-pod.py deploy --gpu "RTX 4090"      # cheaper GPU
uv run deploy-pod.py deploy --no-auto-install     # manual install (paste curl one-liner yourself)
uv run deploy-pod.py list                         # list your pods
uv run deploy-pod.py stop <pod-id>                # stop (keep volume)
uv run deploy-pod.py destroy <pod-id>             # terminate (deletes pod + container disk)
```

The script provisions: RTX 5090, 80 GB container disk, 100 GB volume, ports 7860/8888/22 exposed, RunPod's PyTorch 2.8 + CUDA 12.8 Ubuntu 24.04 image. With auto-install ON (default), it overrides the container's `docker_args` to chain `/start.sh` (Jupyter+SSH) + `install-wan2gp.sh` + `python wgp.py --listen --server-port 7860` so the Gradio UI is reachable at `https://<podid>-7860.proxy.runpod.net` after ~6-8 min.

Watch progress from the pod's Jupyter terminal:

```bash
tail -f /workspace/wan2gp-install.log     # install progress
tail -f /workspace/wan2gp-run.log         # wgp.py boot logs
```

## Pod setup (manual via RunPod UI)

Or, deploy manually with this template:

- **Template**: `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` (or any PyTorch 2.8+ / CUDA 12.8+ / Python 3.11 image)
- **GPU**: RTX 4090 (24 GB) or RTX 5090 (32 GB) for Wan 2.2 14B fp16. RTX 30xx (12 GB) works too but only with the 5B model.
- **Container Disk**: 80 GB
- **Volume Disk**: **100 GB minimum** if you plan to stack multiple 14B models. 50 GB is too tight (you'll hit `Disk quota exceeded` mid-download).
- **Volume Mount Path**: `/workspace`
- **Expose HTTP Ports**: `7860,8888` (7860 for Gradio, 8888 for Jupyter)

## Install (one-liner)

In the pod's web terminal or Jupyter Lab terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/PastaGringo/wan2gp-runpod-install/main/install-wan2gp.sh | bash
```

Takes ~5-8 minutes (mostly downloading ~5 GB of wheels).

## Launch

```bash
cd /workspace/Wan2GP && source venv/bin/activate
python wgp.py --listen --server-port 7860 --share
```

Look for the line:

```
Running on public URL: https://xxxxxxxxxxxx.gradio.live
```

That's the URL to open in your browser.

## Model recommendations (50 GB volume budget)

| Need | Model | Disk | Speed on 5090 |
|---|---|---|---|
| Text-to-Video | **Wan2.2 → TextImage2video 5B → Default** + mode `Text Prompt Only` | ~10 GB | 30-90s |
| Image-to-Video | Same 5B model + mode `Start Video with Image` | ~10 GB | 30-90s |
| Best I2V quality | Wan2.2 → Image2video 14B → Default | ~15 GB | 1-3 min |
| Animation from control video | Wan2.2 → Animate 14B | ~14 GB | 2-5 min |

The **TextImage2video 5B** is the best starting point — supports both T2V and I2V from a single ~10 GB checkpoint, fastest iteration.

## Troubleshooting

### `Disk quota exceeded (os error 122)` mid-download

Your RunPod volume quota (default 50 GB) is full. Either:

- **Cleanup**: `rm -rf /workspace/Wan2GP/ckpts/*.incomplete /workspace/Wan2GP/ckpts/<unused-model>*`
- **Resize**: Stop the pod, edit Volume Disk Size in RunPod UI, restart (data preserved).

The `df -h /workspace` will show TBs available because `/workspace` is on MooseFS — but RunPod enforces a per-pod quota separately.

### `Fast download using 'hf_transfer' is enabled but 'hf_transfer' package is not available`

The installer handles this (`pip install hf_transfer`). If you skipped it, run `pip install hf_transfer` in the venv.

### `ModuleNotFoundError: No module named 'torch'` when building SageAttention

You ran `pip install -e .` without `--no-build-isolation`. The installer uses the right flag; if you build by hand, use:

```bash
cd /tmp/SageAttention && pip install --no-build-isolation -e .
```

### Generate button does nothing, no logs in terminal

The selected model likely requires inputs (control video + reference image) that you haven't uploaded. Check the model in the top-right dropdown — if it's `Animate 14B` or `Image2video 14B`, switch to `TextImage2video 5B` with mode `Text Prompt Only` for a purely text-driven test.

## License

The installer script is MIT. Wan2GP itself has its own license — check the [Wan2GP repo](https://github.com/deepbeepmeep/Wan2GP).
