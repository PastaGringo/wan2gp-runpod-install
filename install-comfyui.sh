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

# ── Discord notification helpers ──────────────────────────────────
# All calls use DISCORD_WEBHOOK_URL (passed via docker env from deploy-pod.py).
# Silent on failure so a webhook outage never breaks the install.
INSTALL_START_TS=$(date +%s)
SCRIPT_NAME="ComfyUI installer"

_discord_post() {
  # Args: $1=color (int), $2=title, $3=description
  [ -z "${DISCORD_WEBHOOK_URL:-}" ] && return 0
  local color="$1" title="$2" desc="$3"
  local pod="${RUNPOD_POD_ID:-?}"
  local elapsed=$(( $(date +%s) - INSTALL_START_TS ))
  local mm=$(( elapsed / 60 ))
  local ss=$(( elapsed % 60 ))
  # Build JSON in Python, reading args from env vars to dodge bash quoting hell.
  DISCORD_TITLE="$title" \
  DISCORD_DESC="$desc" \
  DISCORD_COLOR="$color" \
  DISCORD_FOOTER="pod $pod · elapsed ${mm}m${ss}s" \
  DISCORD_USERNAME="${SCRIPT_NAME:-Installer}" \
  python3 -c '
import json, os, urllib.request
url = os.environ["DISCORD_WEBHOOK_URL"]
body = json.dumps({
  "username": os.environ["DISCORD_USERNAME"],
  "embeds": [{
    "title": os.environ["DISCORD_TITLE"],
    "description": os.environ.get("DISCORD_DESC") or " ",
    "color": int(os.environ["DISCORD_COLOR"]),
    "footer": {"text": os.environ["DISCORD_FOOTER"]},
  }]
}).encode("utf-8")
req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
try:
  urllib.request.urlopen(req, timeout=5).read()
except Exception:
  pass
' > /dev/null 2>&1 || true
}

notify() {
  # Normal progress step. $1=step number, $2=total, $3=label
  local step="$1" total="${2:-9}" label="$3"
  echo ""
  echo "▶ [${step}/${total}] ${label}"
  # Blue for info (0x3498DB = 3447003)
  _discord_post 3447003 "[${step}/${total}] ${label}" " "
}

notify_done() {
  # Final success ping with the URL ready to click. $1=ui_url
  local url="$1"
  # Green for success (0x2ECC71 = 3066993)
  _discord_post 3066993 "✅ Install complete — UI ready" "Open in browser: ${url}"
}

notify_error() {
  # Trapped on any uncaught failure. $1=label of step that failed (best effort)
  local exit_code="$?"
  local where="${BASH_COMMAND:-unknown}"
  # Red for error (0xE74C3C = 15158332)
  _discord_post 15158332 "❌ Install FAILED (exit ${exit_code})" "Last command: \`${where}\`\nCheck \`tail -100 /workspace/comfyui-install.log\` on the pod."
  exit "$exit_code"
}

trap notify_error ERR

export SCRIPT_NAME

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

# ── [5b] SageAttention 2.x (RTX 40xx/50xx) for Kijai WanVideoWrapper ───
# Kijai's WanVideoModelLoader has attention_mode=sageattn by default in
# example workflows. Without sageattention, every loadmodel call crashes.
notify 6 9 "Installing SageAttention 2.x for GPU"
echo ""
echo "── [5b] SageAttention 2.x (build from source) ──"
SM=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
pip install "setuptools<=75.8.2" --force-reinstall
case "$SM" in
  "8.6")
    pip install sageattention==1.0.6
    ;;
  *)
    if [ ! -d /tmp/SageAttention ]; then
      git clone --depth 1 https://github.com/thu-ml/SageAttention /tmp/SageAttention
    fi
    (cd /tmp/SageAttention && pip install --no-build-isolation -e .)
    ;;
esac

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

# ── [7] Download all models used by the 3 example workflows ──────────
# Covers:
#   - wan22_5B_I2V.json         → TI2V 5B + Wan2.2 VAE + T5
#   - wan22_14B_I2V.json        → I2V-A14B HIGH/LOW fp8 + Wan2.1 VAE + T5
#   - wan22_14B_I2V_longscene.json → same as 14B + Lightx2v LoRA
# Total ~52 GB on disk (fits in the default 100 GB volume).
notify 8 9 "Downloading models for all workflows (~52 GB total — first time only)"
echo ""
echo "── [7] Download models for all 3 workflows (~52 GB) ──"
mkdir -p models/diffusion_models/WanVideo/2_2 \
         models/vae/wanvideo \
         models/text_encoders \
         models/loras/WanVideo/Lightx2v

# 7.1 — Text encoder (shared by all workflows)
T5="models/text_encoders/umt5-xxl-enc-bf16.safetensors"
if [ ! -f "$T5" ]; then
  notify 8 9 "T5-XXL text encoder bf16 (~11 GB)"
  echo "  → T5-XXL text encoder bf16 (~11 GB)…"
  curl -fL -o "$T5" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors"
fi

# 7.2 — VAEs (Wan 2.2 for 5B workflow, Wan 2.1 for 14B workflow)
VAE22="models/vae/wanvideo/Wan2_2_VAE_bf16.safetensors"
if [ ! -f "$VAE22" ]; then
  notify 8 9 "Wan 2.2 VAE bf16 (~1.4 GB)"
  echo "  → Wan 2.2 VAE bf16 (~1.4 GB)…"
  curl -fL -o "$VAE22" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_2_VAE_bf16.safetensors"
fi

VAE21="models/vae/wanvideo/Wan2_1_VAE_bf16.safetensors"
if [ ! -f "$VAE21" ]; then
  notify 8 9 "Wan 2.1 VAE bf16 (~250 MB)"
  echo "  → Wan 2.1 VAE bf16 (~250 MB)…"
  curl -fL -o "$VAE21" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors"
fi

# 7.3 — Wan 2.2 TI2V 5B (workflow 1) — vanilla 5B from Comfy-Org
DIFFUSION_5B="models/diffusion_models/WanVideo/2_2/wan2.2_ti2v_5B_fp16.safetensors"
if [ ! -f "$DIFFUSION_5B" ]; then
  notify 8 9 "Wan 2.2 TI2V 5B diffusion model (~10 GB)"
  echo "  → Wan 2.2 TI2V 5B fp16 (~10 GB)…"
  curl -fL -o "$DIFFUSION_5B" \
    "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"
fi

# 7.4 — Wan 2.2 I2V 14B HIGH fp8 (workflows 2 + 3) — Kijai's MoE high-noise expert
DIFFUSION_14B_HIGH="models/diffusion_models/WanVideo/2_2/Wan2_2-I2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors"
if [ ! -f "$DIFFUSION_14B_HIGH" ]; then
  notify 8 9 "Wan 2.2 I2V 14B HIGH fp8 e4m3fn (~15 GB)"
  echo "  → Wan 2.2 I2V 14B HIGH fp8 (~15 GB)…"
  curl -fL -o "$DIFFUSION_14B_HIGH" \
    "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/I2V/Wan2_2-I2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors"
fi

# 7.5 — Wan 2.2 I2V 14B LOW fp8 (workflows 2 + 3) — Kijai's MoE low-noise expert
DIFFUSION_14B_LOW="models/diffusion_models/WanVideo/2_2/Wan2_2-I2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors"
if [ ! -f "$DIFFUSION_14B_LOW" ]; then
  notify 8 9 "Wan 2.2 I2V 14B LOW fp8 e4m3fn (~15 GB)"
  echo "  → Wan 2.2 I2V 14B LOW fp8 (~15 GB)…"
  curl -fL -o "$DIFFUSION_14B_LOW" \
    "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/I2V/Wan2_2-I2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors"
fi

# 7.6 — Lightx2v step-distillation LoRA (workflow 3 longscene) — 4-step sampling ≈ 6× faster
LIGHTX2V="models/loras/WanVideo/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
if [ ! -f "$LIGHTX2V" ]; then
  notify 8 9 "Lightx2v step-distill LoRA rank64 bf16 (~740 MB)"
  echo "  → Lightx2v I2V 14B step-distill LoRA rank64 (~740 MB)…"
  curl -fL -o "$LIGHTX2V" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
fi

# ── [8] Done ──────────────────────────────────────
notify 9 9 "Verifying torch/CUDA, finishing up"
echo ""
echo "── [8] Verify ──"
python -c "import torch; print(f'✅ torch {torch.__version__}, CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0)}')"
echo ""
ls -lh models/diffusion_models models/vae models/text_encoders

# Final Discord ping with the click-ready UI URL.
notify_done "https://${RUNPOD_POD_ID:-unknown}-8188.proxy.runpod.net"

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
