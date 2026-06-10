#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test direct VLESS Reality on server2 through local Xray client.")
    parser.add_argument("--snapshot", default="/var/lib/tgvpn/subscription_snapshot.json")
    parser.add_argument("--env", default="/home/tgvpn/.env.subscription")
    parser.add_argument("--xray-bin", default="xray")
    parser.add_argument("--test-url", default="https://www.gstatic.com/generate_204")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    env = read_env(Path(args.env))
    user = first_active_user(Path(args.snapshot))
    if user is None:
        print("direct VLESS smoke skipped: no active main VPN user in snapshot")
        return

    security = (env.get("VLESS_SECURITY") or "reality").lower()
    required = ["VLESS_PUBLIC_HOST", "VLESS_PUBLIC_PORT"]
    if security == "reality":
        required.extend(["VLESS_PBK", "VLESS_SID"])
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise SystemExit(f"direct VLESS smoke failed: missing env keys: {', '.join(missing)}")

    socks_port = free_port()
    config = build_client_config(user, env, socks_port=socks_port)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as f:
        json.dump(config, f, ensure_ascii=False)
        f.flush()
        config_path = Path(f.name)

    process = subprocess.Popen(
        [args.xray_bin, "run", "-c", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid,
    )
    try:
        time.sleep(2)
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(stderr.strip() or stdout.strip() or "temporary Xray client exited")

        curl = subprocess.run(
            [
                "curl",
                "-fsS",
                "--max-time",
                str(args.timeout),
                "--socks5-hostname",
                f"127.0.0.1:{socks_port}",
                args.test_url,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=args.timeout + 5,
        )
        if curl.returncode != 0:
            raise RuntimeError(curl.stderr.strip() or curl.stdout.strip() or "curl through VLESS failed")
        print(f"direct VLESS smoke ok: tg={user['telegram_id']} host={env['VLESS_PUBLIC_HOST']}:{env['VLESS_PUBLIC_PORT']}")
    finally:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        config_path.unlink(missing_ok=True)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def first_active_user(snapshot_path: Path) -> dict[str, Any] | None:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    users = snapshot.get("users", {})
    if not isinstance(users, dict):
        return None
    for user in users.values():
        if isinstance(user, dict) and user.get("main_vpn_active") and user.get("uuid"):
            return user
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_client_config(user: dict[str, Any], env: dict[str, str], *, socks_port: int) -> dict[str, Any]:
    client: dict[str, Any] = {"id": str(user["uuid"]), "encryption": "none"}
    if env.get("VLESS_FLOW"):
        client["flow"] = env["VLESS_FLOW"]

    stream: dict[str, Any] = {
        "network": env.get("VLESS_TYPE", "tcp"),
        "security": env.get("VLESS_SECURITY", "reality"),
    }
    if stream["network"] == "tcp":
        stream["tcpSettings"] = {}
    if stream["network"] == "ws":
        stream["wsSettings"] = {
            "path": env.get("VLESS_PATH") or "/",
            "headers": {"Host": env.get("VLESS_SNI") or env["VLESS_PUBLIC_HOST"]},
        }
    if stream["security"] == "tls":
        stream["tlsSettings"] = {
            "serverName": env.get("VLESS_SNI") or env["VLESS_PUBLIC_HOST"],
            "fingerprint": env.get("VLESS_FP") or "chrome",
        }
    if stream["security"] == "reality":
        stream["realitySettings"] = {
            "serverName": env.get("VLESS_SNI") or env["VLESS_PUBLIC_HOST"],
            "fingerprint": env.get("VLESS_FP") or "chrome",
            "publicKey": env["VLESS_PBK"],
            "shortId": env["VLESS_SID"],
            "spiderX": "/",
        }

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks",
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
            }
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": env["VLESS_PUBLIC_HOST"],
                            "port": int(env["VLESS_PUBLIC_PORT"]),
                            "users": [client],
                        }
                    ]
                },
                "streamSettings": stream,
            },
            {"tag": "direct", "protocol": "freedom"},
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "outboundTag": "proxy",
                }
            ]
        },
    }


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
