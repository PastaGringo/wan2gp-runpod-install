#!/usr/bin/env bash
# Wan2GP turnkey installer for RunPod GPU pods
# Tested on template: runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
# Supports: RTX 30xx (SM86), RTX 40xx (SM89), RTX 50xx (SM120)
#
# Usage on a fresh pod (web terminal or SSH):
#   curl -fsSL https://raw.githubusercontent.com/PastaGringo/wan2gp-runpod-install/main/install-wan2gp.sh | bash
#
# After install, launch with:
#   cd /workspace/Wan2GP && source venv/bin/activate
#   python wgp.py --listen --server-port 7860 --share

set -euo pipefail

echo "=============================================="
echo " Wan2GP RunPod installer"
echo "=============================================="

# ── [0] Sanity check ────────────────────────────────────────────────
echo ""
echo "── [0] System check ──"
nvidia-smi --query-gpu=name,compute_cap,memory.total,driver_version --format=csv
python3.11 --version
echo "nvcc: $(nvcc --version 2>/dev/null | tail -1 || echo 'not in PATH (ok, torch ships its own CUDA)')"

# Detect GPU compute capability (8.6 = RTX 30xx, 8.9 = RTX 40xx, 12.0 = RTX 50xx)
SM=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
echo "Compute capability: $SM"

# ── [1] Clone Wan2GP ────────────────────────────────────────────────
echo ""
echo "── [1] Clone Wan2GP ──"
cd /workspace
if [ ! -d Wan2GP ]; then
  git clone https://github.com/deepbeepmeep/Wan2GP.git
else
  echo "Wan2GP already cloned, pulling latest..."
  (cd Wan2GP && git pull)
fi
cd /workspace/Wan2GP

# ── [2] Create venv (system python3.11) ─────────────────────────────
echo ""
echo "── [2] Create venv (Python 3.11) ──"
if [ ! -d venv ]; then
  python3.11 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip wheel

# ── [3] PyTorch 2.10 + cu128 ────────────────────────────────────────
# (override the template's torch 2.8 — Wan2GP docs warn about a RAM leak in 2.8)
echo ""
echo "── [3] PyTorch 2.10 + cu128 ──"
pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 \
  --index-url https://download.pytorch.org/whl/cu128

# ── [4] Wan2GP requirements ─────────────────────────────────────────
echo ""
echo "── [4] Wan2GP requirements (~150 packages) ──"
pip install -r requirements.txt

# ── [5] hf_transfer (RunPod sets HF_HUB_ENABLE_HF_TRANSFER=1) ───────
echo ""
echo "── [5] hf_transfer (fast HuggingFace downloads) ──"
pip install hf_transfer

# ── [6] SageAttention — version depends on GPU architecture ─────────
echo ""
echo "── [6] SageAttention ──"
pip install "setuptools<=75.8.2" --force-reinstall

case "$SM" in
  "8.6")
    echo "RTX 30xx detected (SM86) → SageAttention 1.0.6 prebuilt wheel"
    pip install sageattention==1.0.6
    ;;
  "8.9"|"9.0"|"12.0"|"10.0")
    echo "RTX 40xx/50xx/H100 detected (SM$SM) → SageAttention 2.x from source"
    if [ ! -d /tmp/SageAttention ]; then
      git clone https://github.com/thu-ml/SageAttention /tmp/SageAttention
    fi
    cd /tmp/SageAttention
    # --no-build-isolation is REQUIRED: setup.py imports torch, which is missing
    # in pip's isolated build env. Use the current venv's torch instead.
    pip install --no-build-isolation -e .
    cd /workspace/Wan2GP
    ;;
  *)
    echo "Unknown compute capability '$SM' — skipping SageAttention (Wan2GP works without, just slower)"
    ;;
esac

# ── [7] Final verification ──────────────────────────────────────────
echo ""
echo "── [7] Verify torch + CUDA ──"
python - <<'PY'
import torch
assert torch.cuda.is_available(), "CUDA not available!"
print(f"✅ torch {torch.__version__}")
print(f"✅ CUDA available: {torch.cuda.is_available()}")
print(f"✅ GPU: {torch.cuda.get_device_name(0)}")
print(f"✅ VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
try:
    import sageattention
    print(f"✅ SageAttention loaded")
except ImportError:
    print(f"⚠️  SageAttention not installed (Wan2GP will use slower fallback)")
try:
    import hf_transfer
    print(f"✅ hf_transfer loaded")
except ImportError:
    print(f"⚠️  hf_transfer not installed (downloads will be slower)")
PY

echo ""
echo "=============================================="
echo " ✅ Install complete!"
echo "=============================================="
echo ""
echo " Launch Wan2GP with:"
echo ""
echo "   cd /workspace/Wan2GP && source venv/bin/activate"
echo "   python wgp.py --listen --server-port 7860"
echo ""
echo " If port 7860 is exposed on the pod (via deploy-pod.py defaults or"
echo " RunPod UI), the UI is reachable at:"
echo "   https://\$RUNPOD_POD_ID-7860.proxy.runpod.net"
echo ""
echo " Add --share to also get a public gradio.live tunnel (valid 1 week,"
echo " useful if you didn't expose 7860 on the pod)."
echo ""
echo " RECOMMENDED MODEL FOR 50 GB VOLUME:"
echo "   Wan2.2 → TextImage2video 5B → Default"
echo "   (~10 GB download, supports both T2V and I2V)"
echo ""
echo "=============================================="
