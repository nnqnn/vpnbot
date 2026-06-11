#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANAGED_PREFIX = "user-"
MANAGED_SUFFIX = "@vpn.local"


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile server2 Xray runtime users from one payload.")
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    summary = reconcile(payload)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))


def reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    config_path = Path(payload["xray_config_path"])
    inbound_tag = str(payload["xray_inbound_tag"])
    extra_inbound_tags = [str(tag) for tag in (payload.get("xray_extra_inbound_tags") or []) if str(tag)]
    inbound_tags = [inbound_tag] + [tag for tag in extra_inbound_tags if tag != inbound_tag]
    xray_bin = str(payload.get("xray_bin_path") or "xray")
    api_server = str(payload.get("xray_api_server") or "127.0.0.1:10085")
    api_timeout = int(payload.get("xray_api_timeout_seconds") or 5)
    command_timeout = int(payload.get("command_timeout_seconds") or max(api_timeout + 10, 30))
    flow = str(payload.get("vless_flow") or "")
    raw_flow_tags = payload.get("xray_flow_inbound_tags")
    if isinstance(raw_flow_tags, list):
        flow_inbound_tags = {str(tag) for tag in raw_flow_tags if str(tag)}
    else:
        flow_inbound_tags = {inbound_tag}
    persist_users_in_config = bool(payload.get("persist_users_in_config", False))
    expected = {str(email): str(user_uuid) for email, user_uuid in (payload.get("expected") or {}).items()}
    managed_emails = {str(email) for email in (payload.get("managed_emails") or [])}

    config = read_json(config_path)
    config_changed = False
    for tag in inbound_tags:
        tag_flow = flow if tag in flow_inbound_tags else ""
        if persist_users_in_config:
            changed = sync_managed_clients_in_config(config, inbound_tag=tag, expected=expected, flow=tag_flow)
        else:
            changed = strip_managed_clients_from_config(config, inbound_tag=tag)
        config_changed = changed or config_changed
    if config_changed:
        write_config_atomic(config_path, config, xray_bin=xray_bin, timeout=command_timeout)

    to_remove = sorted((managed_emails or set(expected)) - set(expected))
    summary = {
        "expected": len(expected),
        "managed": len(managed_emails),
        "inbounds": inbound_tags,
        "persist_users_in_config": persist_users_in_config,
        "config_changed": config_changed,
        "removed": 0,
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    for tag in inbound_tags:
        tag_flow = flow if tag in flow_inbound_tags else ""
        for email in to_remove:
            ok, detail = remove_runtime_user(
                xray_bin=xray_bin,
                api_server=api_server,
                api_timeout=api_timeout,
                command_timeout=command_timeout,
                inbound_tag=tag,
                email=email,
            )
            if ok:
                summary["removed"] += 1
            else:
                summary["errors"].append({"inbound": tag, "email": email, "operation": "remove", "detail": detail})

        for email, user_uuid in sorted(expected.items()):
            state = get_runtime_user(
                xray_bin=xray_bin,
                api_server=api_server,
                api_timeout=api_timeout,
                command_timeout=command_timeout,
                inbound_tag=tag,
                email=email,
            )
            if state["present"] and runtime_matches(state["stdout"], email=email, user_uuid=user_uuid, flow=tag_flow):
                summary["skipped"] += 1
                continue

            if state["present"]:
                ok, detail = remove_runtime_user(
                    xray_bin=xray_bin,
                    api_server=api_server,
                    api_timeout=api_timeout,
                    command_timeout=command_timeout,
                    inbound_tag=tag,
                    email=email,
                )
                if not ok:
                    summary["errors"].append({"inbound": tag, "email": email, "operation": "replace-remove", "detail": detail})
                    continue
                operation = "updated"
            else:
                operation = "added"

            ok, detail = add_runtime_user(
                config=config,
                xray_bin=xray_bin,
                api_server=api_server,
                api_timeout=api_timeout,
                command_timeout=command_timeout,
                inbound_tag=tag,
                email=email,
                user_uuid=user_uuid,
                flow=tag_flow,
            )
            if ok:
                summary[operation] += 1
            else:
                summary["errors"].append({"inbound": tag, "email": email, "operation": operation, "detail": detail})

    if summary["errors"]:
        raise RuntimeError(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return summary


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_config_atomic(path: Path, data: dict[str, Any], *, xray_bin: str, timeout: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent), prefix=".config-", suffix=".json") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        tmp_path = Path(f.name)
    try:
        result = subprocess.run(
            [xray_bin, "run", "-test", "-c", str(tmp_path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "xray config test failed")
        backup = path.with_suffix(path.suffix + f".bak.tgvpn-users.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        if path.exists():
            shutil.copy2(path, backup)
        tmp_path.replace(path)
        path.chmod(0o644)
    finally:
        tmp_path.unlink(missing_ok=True)


def find_inbound(config: dict[str, Any], inbound_tag: str) -> dict[str, Any]:
    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list):
        raise ValueError("Invalid Xray config: inbounds is missing")
    for inbound in inbounds:
        if isinstance(inbound, dict) and inbound.get("tag") == inbound_tag:
            return inbound
    raise ValueError(f"Inbound tag was not found: {inbound_tag}")


def build_client(user_uuid: str, email: str, flow: str) -> dict[str, Any]:
    client: dict[str, Any] = {"id": str(user_uuid), "email": email}
    if flow:
        client["flow"] = flow
    return client


def is_managed_email(email: str | None) -> bool:
    return bool(email and email.startswith(MANAGED_PREFIX) and email.endswith(MANAGED_SUFFIX))


def sync_managed_clients_in_config(
    config: dict[str, Any],
    *,
    inbound_tag: str,
    expected: dict[str, str],
    flow: str,
) -> bool:
    inbound = find_inbound(config, inbound_tag)
    settings = inbound.setdefault("settings", {})
    clients = settings.setdefault("clients", [])
    if not isinstance(clients, list):
        settings["clients"] = []
        clients = settings["clients"]

    non_managed = [
        client
        for client in clients
        if not (isinstance(client, dict) and is_managed_email(client.get("email")))
    ]
    expected_clients = [build_client(expected[email], email, flow) for email in sorted(expected)]
    updated = non_managed + expected_clients
    if clients == updated:
        return False
    settings["clients"] = updated
    settings.setdefault("decryption", "none")
    return True


def strip_managed_clients_from_config(config: dict[str, Any], *, inbound_tag: str) -> bool:
    inbound = find_inbound(config, inbound_tag)
    settings = inbound.setdefault("settings", {})
    clients = settings.setdefault("clients", [])
    if not isinstance(clients, list):
        settings["clients"] = []
        settings.setdefault("decryption", "none")
        return True

    updated = [
        client
        for client in clients
        if not (isinstance(client, dict) and is_managed_email(client.get("email")))
    ]
    if clients == updated:
        settings.setdefault("decryption", "none")
        return False
    settings["clients"] = updated
    settings.setdefault("decryption", "none")
    return True


def api_base(xray_bin: str, api_server: str, api_timeout: int, subcommand: str) -> list[str]:
    return [
        xray_bin,
        "api",
        subcommand,
        f"--server={api_server}",
        f"--timeout={api_timeout}",
    ]


def run(args: list[str], *, timeout: int) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return 124, stdout, stderr or f"command timed out after {timeout}s"
    return result.returncode, result.stdout, result.stderr


def get_runtime_user(
    *,
    xray_bin: str,
    api_server: str,
    api_timeout: int,
    command_timeout: int,
    inbound_tag: str,
    email: str,
) -> dict[str, Any]:
    args = api_base(xray_bin, api_server, api_timeout, "inbounduser")
    args.extend([f"-tag={inbound_tag}", f"-email={email}", "--json"])
    code, stdout, stderr = run(args, timeout=command_timeout)
    if code == 0 and email in stdout:
        return {"present": True, "stdout": stdout, "stderr": stderr}
    if looks_like_missing(stdout, stderr) or code == 0:
        return {"present": False, "stdout": stdout, "stderr": stderr}
    raise RuntimeError(f"inbounduser failed for {email}: {stderr.strip() or stdout.strip()}")


def runtime_matches(stdout: str, *, email: str, user_uuid: str, flow: str) -> bool:
    try:
        normalized = json.dumps(json.loads(stdout), ensure_ascii=False, separators=(",", ":"))
    except json.JSONDecodeError:
        normalized = stdout
    if email not in normalized or user_uuid not in normalized:
        return False
    if flow and '"flow"' in normalized and flow not in normalized:
        return False
    return True


def remove_runtime_user(
    *,
    xray_bin: str,
    api_server: str,
    api_timeout: int,
    command_timeout: int,
    inbound_tag: str,
    email: str,
) -> tuple[bool, str]:
    args = api_base(xray_bin, api_server, api_timeout, "rmu")
    args.extend([f"-tag={inbound_tag}", email])
    code, stdout, stderr = run(args, timeout=command_timeout)
    if code == 0 or looks_like_missing(stdout, stderr):
        return True, stdout.strip() or stderr.strip()
    return False, stderr.strip() or stdout.strip()


def add_runtime_user(
    *,
    config: dict[str, Any],
    xray_bin: str,
    api_server: str,
    api_timeout: int,
    command_timeout: int,
    inbound_tag: str,
    email: str,
    user_uuid: str,
    flow: str,
) -> tuple[bool, str]:
    payload = build_adu_payload(config, inbound_tag=inbound_tag, email=email, user_uuid=user_uuid, flow=flow)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", prefix="tgvpn-adu-", suffix=".json") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        payload_path = Path(f.name)
    try:
        args = api_base(xray_bin, api_server, api_timeout, "adu")
        args.append(str(payload_path))
        code, stdout, stderr = run(args, timeout=command_timeout)
    finally:
        payload_path.unlink(missing_ok=True)
    if code == 0 and (adu_added_users_count(stdout) > 0 or "added" in stdout.lower()):
        return True, stdout.strip()
    if looks_like_exists(stdout, stderr):
        return True, stdout.strip() or stderr.strip()
    return False, stderr.strip() or stdout.strip()


def build_adu_payload(
    config: dict[str, Any],
    *,
    inbound_tag: str,
    email: str,
    user_uuid: str,
    flow: str,
) -> dict[str, Any]:
    inbound = json.loads(json.dumps(find_inbound(config, inbound_tag), ensure_ascii=False))
    inbound.setdefault("settings", {})
    inbound["settings"]["clients"] = [build_client(user_uuid, email, flow)]
    inbound["settings"].setdefault("decryption", "none")
    return {"inbounds": [inbound]}


def looks_like_missing(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in ("not found", "not exist", "removed 0 user", "no such"))


def looks_like_exists(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in ("already exists", "already exist", "duplicate", "existed"))


def adu_added_users_count(stdout: str) -> int:
    match = re.search(r"Added\s+(\d+)\s+user\(s\)\s+in total", stdout)
    if not match:
        return 0
    return int(match.group(1))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
