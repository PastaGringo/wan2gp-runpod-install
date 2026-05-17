#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "runpod>=1.7.0",
# ]
# ///
"""
Wan2GP pod deployer for RunPod.

Requires uv (https://docs.astral.sh/uv/). Inline deps via PEP 723 — no venv needed.

Setup (one time):
  PowerShell session:    $env:RUNPOD_API_KEY = "rpa_..."
  Persistent (Windows):  setx RUNPOD_API_KEY "rpa_..."  (then restart shell)
  bash/zsh:              export RUNPOD_API_KEY="rpa_..."

Usage:
  uv run deploy-pod.py                       # deploy a Wan2GP-ready RTX 5090 pod
  uv run deploy-pod.py deploy --gpu "RTX 4090"
  uv run deploy-pod.py list                  # list your pods
  uv run deploy-pod.py stop <pod-id>         # stop (volume preserved)
  uv run deploy-pod.py destroy <pod-id>      # terminate (pod + container disk gone)
"""

import argparse
import os
import sys
import time

import runpod

# Friendly GPU name → RunPod GPU type ID
GPU_TYPES = {
    "RTX 5090": "NVIDIA GeForce RTX 5090",
    "RTX 4090": "NVIDIA GeForce RTX 4090",
    "RTX 3090": "NVIDIA GeForce RTX 3090",
    "L40S": "NVIDIA L40S",
    "A100": "NVIDIA A100 80GB PCIe",
    "H100": "NVIDIA H100 80GB HBM3",
}

DEFAULT_IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/PastaGringo/wan2gp-runpod-install/main/install-wan2gp.sh"
)


def auth() -> None:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        print("ERROR: RUNPOD_API_KEY environment variable is not set.", file=sys.stderr)
        print("  PowerShell:  $env:RUNPOD_API_KEY = 'rpa_...'", file=sys.stderr)
        print("  Persistent:  setx RUNPOD_API_KEY 'rpa_...'  (restart shell after)", file=sys.stderr)
        print("  bash/zsh:    export RUNPOD_API_KEY='rpa_...'", file=sys.stderr)
        sys.exit(1)
    runpod.api_key = key


AUTO_INSTALL_DOCKER_ARGS = """bash -c '
# Run RunPod's default entrypoint (Jupyter, SSH) in background
/start.sh > /workspace/runpod-start.log 2>&1 &

# Wait a few seconds for /workspace to be mounted
sleep 5

# Run the Wan2GP installer
{{
  echo "=== Auto-install started at $(date) ==="
  curl -fsSL {INSTALL_URL} | bash
}} > /workspace/wan2gp-install.log 2>&1

# Launch wgp.py on port 7860 (RunPod proxy exposes it via https://<podid>-7860.proxy.runpod.net)
cd /workspace/Wan2GP && source venv/bin/activate
echo "=== Launching wgp.py at $(date) ===" >> /workspace/wan2gp-run.log
exec python wgp.py --listen --server-port 7860 >> /workspace/wan2gp-run.log 2>&1
'""".replace("{INSTALL_URL}", INSTALL_SCRIPT_URL)


def deploy(args: argparse.Namespace) -> None:
    auth()
    gpu_id = GPU_TYPES.get(args.gpu, args.gpu)
    auto = not args.no_auto_install
    print(f"→ Creating pod '{args.name}' on {gpu_id} ({args.cloud})")
    print(f"  Image:           {args.image}")
    print(f"  Container disk:  {args.container_disk} GB")
    print(f"  Volume:          {args.volume} GB  →  /workspace")
    print(f"  Ports:           7860/http (Gradio), 8888/http (Jupyter), 22/tcp (SSH)")
    mode = "ON — Wan2GP boots automatically (~6-8 min)" if auto else "OFF — paste the install commands manually"
    print(f"  Auto-install:    {mode}")
    print()

    create_kwargs = dict(
        name=args.name,
        image_name=args.image,
        gpu_type_id=gpu_id,
        gpu_count=1,
        container_disk_in_gb=args.container_disk,
        volume_in_gb=args.volume,
        volume_mount_path="/workspace",
        ports="7860/http,8888/http,22/tcp",
        cloud_type=args.cloud,
    )
    if auto:
        create_kwargs["docker_args"] = AUTO_INSTALL_DOCKER_ARGS

    pod = runpod.create_pod(**create_kwargs)

    pod_id = pod["id"]
    print(f"✅ Pod created: {pod_id}")
    print(f"   Dashboard:  https://www.runpod.io/console/pods/{pod_id}")
    print()

    print("Waiting for pod to come online (up to 5 min)…", flush=True)
    for i in range(60):
        info = runpod.get_pod(pod_id)
        runtime = info.get("runtime") or {}
        uptime = runtime.get("uptimeInSeconds") or 0
        if uptime > 0:
            print(f"✅ Pod RUNNING (uptime {uptime}s)")
            break
        time.sleep(5)
        if i % 6 == 5:
            print(f"  …still waiting ({(i + 1) * 5}s)", flush=True)
    else:
        print("⚠️  Pod still not RUNNING after 5 min — check the dashboard.")
        return

    print()
    print("─" * 60)
    print(f"  Jupyter:        https://{pod_id}-8888.proxy.runpod.net")
    print(f"  Wan2GP Gradio:  https://{pod_id}-7860.proxy.runpod.net")
    print("─" * 60)
    print()
    if auto:
        print("⏳ Auto-install is running INSIDE the pod (~6-8 min).")
        print("   The Gradio URL above will start responding once wgp.py is launched.")
        print()
        print("   To watch progress, open Jupyter → New → Terminal and run:")
        print("     tail -f /workspace/wan2gp-install.log     # install progress")
        print("     tail -f /workspace/wan2gp-run.log         # wgp.py boot logs")
    else:
        print("Manual install — open Jupyter terminal and run:")
        print()
        print(f"  curl -fsSL {INSTALL_SCRIPT_URL} | bash")
        print("  cd /workspace/Wan2GP && source venv/bin/activate \\")
        print("    && python wgp.py --listen --server-port 7860")
    print()
    print(f"Stop when done:     uv run deploy-pod.py stop {pod_id}")
    print(f"Destroy completely: uv run deploy-pod.py destroy {pod_id}")


def list_pods(args: argparse.Namespace) -> None:
    auth()
    pods = runpod.get_pods()
    if not pods:
        print("(no pods)")
        return
    print(f"{'ID':20s}  {'STATUS':10s}  {'GPU':28s}  NAME")
    for p in pods:
        runtime = p.get("runtime") or {}
        uptime = runtime.get("uptimeInSeconds") or 0
        status = "RUNNING" if uptime > 0 else (p.get("desiredStatus") or "UNKNOWN")
        gpu = (p.get("machine") or {}).get("gpuDisplayName") or "?"
        print(f"{p['id']:20s}  {status:10s}  {gpu:28s}  {p.get('name', '')}")


def stop_pod(args: argparse.Namespace) -> None:
    auth()
    print(f"→ Stopping {args.pod_id} (volume preserved, you can restart later)")
    runpod.stop_pod(args.pod_id)
    print("✅ Stopped.")


def destroy_pod(args: argparse.Namespace) -> None:
    auth()
    if not args.yes:
        confirm = input(f"⚠️  Terminate pod {args.pod_id}? Container disk will be deleted. (yes/no): ")
        if confirm.strip().lower() not in {"yes", "y"}:
            print("Aborted.")
            return
    print(f"→ Terminating {args.pod_id}…")
    runpod.terminate_pod(args.pod_id)
    print("✅ Terminated. Volume disk (if any) is kept and billed separately.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy and manage Wan2GP-ready RunPod GPU pods.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_deploy = sub.add_parser("deploy", help="Deploy a new Wan2GP-ready pod (default action)")
    p_deploy.add_argument("--name", default="wan2gp")
    p_deploy.add_argument(
        "--gpu",
        default="RTX 5090",
        help=f"GPU model. Friendly names: {', '.join(GPU_TYPES)}. Or pass a full RunPod GPU type ID.",
    )
    p_deploy.add_argument("--image", default=DEFAULT_IMAGE)
    p_deploy.add_argument("--container-disk", type=int, default=80, help="Container disk size in GB (default 80)")
    p_deploy.add_argument("--volume", type=int, default=100, help="Volume disk size in GB (default 100 — don't go below 80 if testing multiple 14B models)")
    p_deploy.add_argument("--cloud", choices=["SECURE", "COMMUNITY"], default="SECURE", help="Cloud type (default SECURE)")
    p_deploy.add_argument(
        "--no-auto-install",
        action="store_true",
        help="Skip the auto-install/auto-launch chain — pod boots empty, you SSH/Jupyter in and run the install manually.",
    )
    p_deploy.set_defaults(func=deploy)

    p_list = sub.add_parser("list", help="List your RunPod pods")
    p_list.set_defaults(func=list_pods)

    p_stop = sub.add_parser("stop", help="Stop a pod (keep volume)")
    p_stop.add_argument("pod_id")
    p_stop.set_defaults(func=stop_pod)

    p_destroy = sub.add_parser("destroy", help="Terminate a pod (deletes pod + container disk; volume kept)")
    p_destroy.add_argument("pod_id")
    p_destroy.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_destroy.set_defaults(func=destroy_pod)

    args = parser.parse_args()
    if not args.cmd:
        args = parser.parse_args(["deploy"])
    args.func(args)


if __name__ == "__main__":
    main()
