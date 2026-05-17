#!/usr/bin/env bash
# ComfyUI + Wan 2.2 turnkey installer for RunPod GPU pods
# Tested on template: runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
#
# Installs:
#   - ComfyUI (latest)
#   - ComfyUI-Manager (essential — manage nodes from inside the UI)
#   - ComfyUI-WanVideoWrapper (Kijai) — THE Wan 2.2 nodes + example workflows
#   - ComfyUI-Frame-Interpolation (RIFE/FILM for 24→48fps smoothness)
#   - ComfyUI-VideoHelperSuite (mp4 output, sliding window)
#   - Wan 2.2 TI2V 5B model files (~10 GB: model + VAE + T5 text encoder)
#
# Usage on a fresh pod:
#   curl -fsSL https://raw.githubusercontent.com/PastaGringo/wan2gp-runpod-install/main/install-comfyui.sh | bash
#
# After install, launch with:
#   cd /workspace/ComfyUI && source venv/bin/activate
#   python main.py --listen 0.0.0.0 --port 8188

set -euo pipefail

# ── Discord notification helper ──
# Calls DISCORD_WEBHOOK_URL (passed via docker env) with a message.
# Silent on failure so a webhook outage never breaks the install.
notify() {
  local step="$1"
  local total="${2:-9}"
  local label="$3"
  echo ""
  echo "▶ [${step}/${total}] ${label}"
  if [ -n "${DISCORD_WEBHOOK_URL:-}" ]; then
    local pod="${RUNPOD_POD_ID:-?}"
    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
  'username': 'ComfyUI installer',
  'content': f'\`${pod}\` [${step}/${total}] ${label}'
}))" 2>/dev/null || echo "{\"content\":\"[${step}/${total}] ${label}\"}")
    curl -fsS -X POST "$DISCORD_WEBHOOK_URL" \
      -H "Content-Type: application/json" \
      --data "$payload" \
      > /dev/null 2>&1 || true
  fi
}

echo "=============================================="
echo " ComfyUI + Wan 2.2 RunPod installer"
echo "=============================================="
notify 0 9 "Starting install"

# ── [0] System check ──────────────────────────────
notify 1 9 "System check"
echo ""
echo "── [0] System check ──"
nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv
python3.11 --version

# ── [1] Clone ComfyUI ─────────────────────────────
notify 2 9 "Cloning ComfyUI"
echo ""
echo "── [1] Clone ComfyUI ──"
cd /workspace
if [ ! -d ComfyUI ]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git
fi
cd /workspace/ComfyUI

# ── [2] Create venv ───────────────────────────────
notify 3 9 "Creating Python 3.11 venv"
echo ""
echo "── [2] Create venv (Python 3.11) ──"
if [ ! -d venv ]; then
  python3.11 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip wheel

# ── [3] PyTorch 2.10 + cu128 ──────────────────────
notify 4 9 "Installing PyTorch 2.10 + cu128 (~3 GB, 2-3 min)"
echo ""
echo "── [3] PyTorch 2.10 + cu128 ──"
pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 \
  --index-url https://download.pytorch.org/whl/cu128

# ── [4] ComfyUI base requirements ─────────────────
notify 5 9 "Installing ComfyUI base requirements (~2 min)"
echo ""
echo "── [4] ComfyUI base requirements ──"
pip install -r requirements.txt

# ── [5] hf_transfer (fast HF downloads) ───────────
notify 6 9 "Installing hf_transfer + huggingface_hub"
echo ""
echo "── [5] hf_transfer ──"
pip install hf_transfer huggingface_hub

# ── [6] Custom nodes ──────────────────────────────
notify 7 9 "Installing custom nodes (Manager, WanVideoWrapper, RIFE, VHS, KJNodes)"
echo ""
echo "── [6] Custom nodes ──"
cd /workspace/ComfyUI/custom_nodes

# 6.1 — ComfyUI-Manager (essential, UI inside ComfyUI to manage other nodes)
if [ ! -d ComfyUI-Manager ]; then
  git clone --depth 1 https://github.com/Comfy-Org/ComfyUI-Manager
fi
pip install -r ComfyUI-Manager/requirements.txt 2>/dev/null || true

# 6.2 — Kijai's WanVideoWrapper (THE Wan 2.2 nodes + tested example workflows)
if [ ! -d ComfyUI-WanVideoWrapper ]; then
  git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper
fi
pip install -r ComfyUI-WanVideoWrapper/requirements.txt 2>/dev/null || true

# 6.3 — Frame Interpolation (RIFE / FILM for smooth 24→48fps)
if [ ! -d ComfyUI-Frame-Interpolation ]; then
  git clone --depth 1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation
fi
# Use no-cupy variant when available (faster on most GPUs)
if [ -f ComfyUI-Frame-Interpolation/requirements-no-cupy.txt ]; then
  pip install -r ComfyUI-Frame-Interpolation/requirements-no-cupy.txt 2>/dev/null || true
else
  pip install -r ComfyUI-Frame-Interpolation/requirements.txt 2>/dev/null || true
fi

# 6.4 — Video Helper Suite (mp4/webm output, sliding window combine)
if [ ! -d ComfyUI-VideoHelperSuite ]; then
  git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
fi
pip install -r ComfyUI-VideoHelperSuite/requirements.txt 2>/dev/null || true

# 6.5 — KJNodes (useful helpers used by some Kijai workflows)
if [ ! -d ComfyUI-KJNodes ]; then
  git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes
fi
pip install -r ComfyUI-KJNodes/requirements.txt 2>/dev/null || true

cd /workspace/ComfyUI

# ── [7] Download Wan 2.2 TI2V 5B models ───────────
# Kijai's WanVideoWrapper expects specific filenames + folder layout that
# differ from Comfy-Org's repackaged versions. We download Kijai's files
# (Wan2_2-VAE_bf16, umt5-xxl-enc-bf16, wan2.2_ti2v_5B_fp16) at the paths
# his example workflows reference, so the workflows load without "missing
# model" errors.
notify 8 9 "Downloading Wan 2.2 TI2V 5B models (~22 GB total — Kijai format)"
echo ""
echo "── [7] Download Wan 2.2 TI2V 5B (~22 GB) ──"
mkdir -p models/diffusion_models/WanVideo/2_2 models/vae/wanvideo models/text_encoders

DIFFUSION="models/diffusion_models/WanVideo/2_2/wan2.2_ti2v_5B_fp16.safetensors"
if [ ! -f "$DIFFUSION" ]; then
  notify 8 9 "Downloading TI2V 5B diffusion model (~10 GB)"
  echo "  → Downloading TI2V 5B diffusion model from Comfy-Org (~10 GB)…"
  # Comfy-Org hosts the vanilla 5B; Kijai's HF repo only has the FastWan variant.
  # The file content is the same — we just place it where Kijai workflows expect it.
  curl -fL -o "$DIFFUSION" \
    "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"
fi

VAE="models/vae/wanvideo/Wan2_2_VAE_bf16.safetensors"
if [ ! -f "$VAE" ]; then
  notify 8 9 "Downloading Wan 2.2 VAE bf16 (~1.4 GB)"
  echo "  → Downloading Wan 2.2 VAE bf16 from Kijai (~1.4 GB)…"
  curl -fL -o "$VAE" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_2_VAE_bf16.safetensors"
fi

T5="models/text_encoders/umt5-xxl-enc-bf16.safetensors"
if [ ! -f "$T5" ]; then
  notify 8 9 "Downloading T5-XXL text encoder bf16 (~11 GB)"
  echo "  → Downloading T5-XXL text encoder bf16 (~11 GB)…"
  curl -fL -o "$T5" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors"
fi

# ── [8] Done ──────────────────────────────────────
notify 9 9 "Verifying torch/CUDA, finishing up"
echo ""
echo "── [8] Verify ──"
python -c "import torch; print(f'✅ torch {torch.__version__}, CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0)}')"
echo ""
ls -lh models/diffusion_models models/vae models/text_encoders

# Notify Discord webhook if configured (passed via docker env from deploy-pod.py)
if [ -n "${DISCORD_WEBHOOK_URL:-}" ]; then
  GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
  POD_ID="${RUNPOD_POD_ID:-unknown}"
  curl -fsS -X POST "$DISCORD_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"ComfyUI installer\",\"content\":\"ComfyUI + Wan 2.2 install complete on pod ${POD_ID} (${GPU_NAME}) - UI launching at https://${POD_ID}-8188.proxy.runpod.net\"}" \
    > /dev/null 2>&1 || true
fi

cat <<'EOF'

==============================================
 ✅ ComfyUI + Wan 2.2 install complete!
==============================================

 Launch ComfyUI:

   cd /workspace/ComfyUI && source venv/bin/activate
   python main.py --listen 0.0.0.0 --port 8188

 Then open in your browser:
   https://$RUNPOD_POD_ID-8188.proxy.runpod.net

 LOAD A WORKFLOW:
   1. In ComfyUI, click "Workflow" → "Open" (top-left)
   2. Or drag-drop a .json file onto the canvas
   3. The Kijai example workflows are on the pod at:
        /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/example_workflows/
      → look for "Wan2.2_TI2V_5B_*.json" or "Wan2.2_I2V_*.json"
   4. You can also browse them on GitHub:
        https://github.com/kijai/ComfyUI-WanVideoWrapper/tree/main/example_workflows

 RECOMMENDED FIRST WORKFLOW:
   Wan2.2_TI2V_5B_T2V_example.json   (text-to-video)
   Wan2.2_TI2V_5B_I2V_example.json   (image-to-video)

 NOTES:
   - Port 8188 must be exposed on the pod (deploy-pod.py does this)
   - First generation triggers JIT compile of attention kernels (~1 min extra)
   - For multi-shot scenes: enable "Context Options" in WanVideoSampler
     to use sliding window + last-frame seeding

==============================================
EOF
