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
import json as _json
import os
import sys
import time
import urllib.request

# On Windows the default console encoding (cp1252) can't print Unicode arrows
# like → that we use in status messages. Force UTF-8 on stdout/stderr so the
# script runs cleanly regardless of $env:PYTHONUTF8.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

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
REPO_RAW = "https://raw.githubusercontent.com/PastaGringo/wan2gp-runpod-install/main"

STACKS = {
    "wan2gp": {
        "install_url": f"{REPO_RAW}/install-wan2gp.sh",
        "launch_dir": "/workspace/Wan2GP",
        "launch_cmd": "python wgp.py --listen --server-port 7860",
        "ui_port": 7860,
        "label": "WanGP (Gradio UI, simple)",
    },
    "comfyui": {
        "install_url": f"{REPO_RAW}/install-comfyui.sh",
        "launch_dir": "/workspace/ComfyUI",
        "launch_cmd": "python main.py --listen 0.0.0.0 --port 8188",
        "ui_port": 8188,
        "label": "ComfyUI (node graph, advanced pipelines)",
    },
}


def auth() -> None:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        print("ERROR: RUNPOD_API_KEY environment variable is not set.", file=sys.stderr)
        print("  PowerShell:  $env:RUNPOD_API_KEY = 'rpa_...'", file=sys.stderr)
        print("  Persistent:  setx RUNPOD_API_KEY 'rpa_...'  (restart shell after)", file=sys.stderr)
        print("  bash/zsh:    export RUNPOD_API_KEY='rpa_...'", file=sys.stderr)
        sys.exit(1)
    runpod.api_key = key


def discord_notify(message: str) -> None:
    """POST a message to Discord webhook if DISCORD_WEBHOOK_URL is set. Silent on failure."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=_json.dumps({"username": "RunPod Deployer", "content": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def build_docker_args(stack: str) -> str:
    """Build the auto-install docker_args chain for the given stack.

    Single-line bash -c chain to avoid newlines, apostrophes, or any character
    that breaks GraphQL serialization on RunPod's side.
    """
    s = STACKS[stack]
    install_url = s["install_url"]
    launch_dir = s["launch_dir"]
    launch_cmd = s["launch_cmd"]
    # No apostrophes anywhere in the chain. Single-quote-safe.
    # Idempotent: skip install if already done (sentinel file), skip relaunch
    # if a process is already listening on the UI port. Avoids restart loops
    # if Docker bounces the container after the user manually launched the UI.
    port = STACKS[stack]["ui_port"]
    sentinel = f"/workspace/.{stack}-installed"
    chain = (
        f"/start.sh > /workspace/runpod-start.log 2>&1 & "
        f"sleep 5 && "
        f"if [ ! -f {sentinel} ]; then "
        f"  (echo === Auto-install {stack} started === && "
        f"   curl -fsSL {install_url} | bash && "
        f"   touch {sentinel}) > /workspace/{stack}-install.log 2>&1; "
        f"fi && "
        f"if ss -ltn | grep -q :{port}; then "
        f"  echo === Port {port} already busy, skipping {stack} launch === >> /workspace/{stack}-run.log; "
        f"  tail -f /dev/null; "
        f"else "
        f"  cd {launch_dir} && source venv/bin/activate && "
        f"  echo === Launching {stack} === >> /workspace/{stack}-run.log && "
        f"  exec {launch_cmd} >> /workspace/{stack}-run.log 2>&1; "
        f"fi"
    )
    return f"bash -c '{chain}'"


def deploy(args: argparse.Namespace) -> None:
    auth()
    gpu_id = GPU_TYPES.get(args.gpu, args.gpu)
    auto = not args.no_auto_install
    stack = STACKS[args.stack]

    # Always expose 7860 + 8188 + 8888 + 22 so swapping stacks later is friction-free
    ports = "7860/http,8188/http,8888/http,22/tcp"

    print(f"→ Creating pod '{args.name}' on {gpu_id} ({args.cloud})")
    print(f"  Image:           {args.image}")
    print(f"  Container disk:  {args.container_disk} GB")
    print(f"  Volume:          {args.volume} GB  →  /workspace")
    print(f"  Ports:           7860/http, 8188/http, 8888/http (Jupyter), 22/tcp (SSH)")
    print(f"  Stack:           {args.stack} — {stack['label']}")
    mode = f"ON — {args.stack} boots automatically (~6-8 min for Wan2GP, ~10-15 min for ComfyUI with model DL)" if auto else "OFF — paste the install commands manually"
    print(f"  Auto-install:    {mode}")
    print()

    # Forward the Discord webhook URL into the pod env so install scripts can ping it.
    env_dict = {}
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord_url:
        env_dict["DISCORD_WEBHOOK_URL"] = discord_url

    create_kwargs = dict(
        name=args.name,
        image_name=args.image,
        gpu_type_id=gpu_id,
        gpu_count=1,
        container_disk_in_gb=args.container_disk,
        volume_in_gb=args.volume,
        volume_mount_path="/workspace",
        ports=ports,
        cloud_type=args.cloud,
    )
    if env_dict:
        create_kwargs["env"] = env_dict
    if auto:
        create_kwargs["docker_args"] = build_docker_args(args.stack)

    pod = runpod.create_pod(**create_kwargs)

    pod_id = pod["id"]
    print(f"✅ Pod created: {pod_id}")
    print(f"   Dashboard:  https://www.runpod.io/console/pods/{pod_id}")
    print()

    discord_notify(
        f"🚀 **Pod created** — `{pod_id}` ({args.gpu}, {args.stack})\n"
        f"Dashboard: https://www.runpod.io/console/pods/{pod_id}\n"
        f"UI (once {args.stack} is up): https://{pod_id}-{stack['ui_port']}.proxy.runpod.net"
    )

    print("Waiting for pod to come online (up to 5 min)…", flush=True)
    online = False
    for i in range(60):
        info = runpod.get_pod(pod_id) or {}
        runtime = info.get("runtime") or {}
        desired = info.get("desiredStatus") or ""
        # Pod is considered online if either: runtime has uptime, OR desiredStatus says RUNNING
        if runtime.get("uptimeInSeconds", 0) > 0 or desired == "RUNNING":
            online = True
            print(f"✅ Pod RUNNING (status={desired or 'runtime'})")
            break
        time.sleep(5)
        if i % 6 == 5:
            print(f"  …still waiting ({(i + 1) * 5}s, desiredStatus={desired or '?'})", flush=True)
    if not online:
        print("⚠️  Pod did not report RUNNING within 5 min — it might still be booting. Check the dashboard.")
        discord_notify(f"⚠️ Pod `{pod_id}` did not report RUNNING within 5 min — check the dashboard")
        return

    discord_notify(f"✅ Pod `{pod_id}` is RUNNING. Auto-install starting (will ping again when ready).")

    ui_url = f"https://{pod_id}-{stack['ui_port']}.proxy.runpod.net"
    print()
    print("─" * 60)
    print(f"  Jupyter:   https://{pod_id}-8888.proxy.runpod.net")
    print(f"  UI ({args.stack}):  {ui_url}")
    print("─" * 60)
    print()
    if auto:
        print(f"⏳ Auto-install ({args.stack}) is running INSIDE the pod.")
        print(f"   The UI URL above will start responding once {args.stack} is launched.")
        print()
        print("   To watch progress, open Jupyter → New → Terminal and run:")
        print(f"     tail -f /workspace/{args.stack}-install.log   # install progress")
        print(f"     tail -f /workspace/{args.stack}-run.log       # boot logs")
    else:
        print("Manual install — open Jupyter terminal and run:")
        print()
        print(f"  curl -fsSL {stack['install_url']} | bash")
        print(f"  cd {stack['launch_dir']} && source venv/bin/activate \\")
        print(f"    && {stack['launch_cmd']}")
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
        "--stack",
        choices=list(STACKS),
        default="wan2gp",
        help="Which UI to install + auto-launch (default: wan2gp). 'comfyui' for node-graph workflows.",
    )
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
