#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Query server2 Xray + Hysteria online devices.")
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    print(json.dumps(collect_online_devices(payload), ensure_ascii=False, separators=(",", ":")))


def collect_online_devices(payload: dict[str, Any]) -> dict[str, Any]:
    emails = [str(email) for email in payload.get("emails", []) if str(email)]
    kick_emails = [str(email) for email in payload.get("kick_emails", []) if str(email)]
    users = {email: {"xray_ips": [], "xray_count": 0, "hysteria_count": 0, "total": 0} for email in emails}
    errors: list[dict[str, str]] = []

    xray_bin = str(payload.get("xray_bin_path") or "xray")
    api_server = str(payload.get("xray_api_server") or "127.0.0.1:10085")
    api_timeout = int(payload.get("xray_api_timeout_seconds") or 5)
    command_timeout = int(payload.get("command_timeout_seconds") or max(api_timeout + 5, 15))

    for email in emails:
        code, stdout, stderr = run_xray_online_ips(
            xray_bin=xray_bin,
            api_server=api_server,
            api_timeout=api_timeout,
            command_timeout=command_timeout,
            email=email,
        )
        if code != 0:
            if looks_like_missing(stdout, stderr):
                ips: list[str] = []
            else:
                errors.append({"source": "xray", "email": email, "detail": stderr.strip() or stdout.strip()})
                continue
        else:
            ips = parse_xray_online_ips(stdout)
        users[email]["xray_ips"] = sorted(set(ips))
        users[email]["xray_count"] = len(users[email]["xray_ips"])

    stats_url = str(payload.get("hysteria_stats_url") or "").rstrip("/")
    if stats_url:
        secret = resolve_secret(payload)
        online, detail = get_hysteria_online(stats_url=stats_url, secret=secret, timeout=float(payload.get("hysteria_stats_timeout_seconds") or 5))
        if online is None:
            errors.append({"source": "hysteria", "email": "*", "detail": detail})
        else:
            for email in emails:
                try:
                    users[email]["hysteria_count"] = max(0, int(online.get(email, 0)))
                except (TypeError, ValueError):
                    users[email]["hysteria_count"] = 0

        if kick_emails:
            ok, detail = kick_hysteria_users(stats_url=stats_url, secret=secret, emails=kick_emails, timeout=float(payload.get("hysteria_stats_timeout_seconds") or 5))
            if not ok:
                errors.append({"source": "hysteria", "email": ",".join(kick_emails), "detail": detail})
    elif emails or kick_emails:
        errors.append({"source": "hysteria", "email": "*", "detail": "hysteria_stats_url is empty"})

    for data in users.values():
        data["total"] = int(data["xray_count"]) + int(data["hysteria_count"])

    return {
        "ok": not errors,
        "users": users,
        "kicked": kick_emails,
        "errors": errors,
    }


def run_xray_online_ips(
    *,
    xray_bin: str,
    api_server: str,
    api_timeout: int,
    command_timeout: int,
    email: str,
) -> tuple[int, str, str]:
    args = [
        xray_bin,
        "api",
        "statsonlineiplist",
        f"--server={api_server}",
        f"--timeout={api_timeout}",
        f"-email={email}",
        "--json",
    ]
    try:
        result = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=command_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return 124, stdout, stderr or f"command timed out after {command_timeout}s"
    return result.returncode, result.stdout, result.stderr


def parse_xray_online_ips(stdout: str) -> list[str]:
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        return []
    raw_ips = payload.get("ips", {})
    if isinstance(raw_ips, dict):
        return [str(ip) for ip in raw_ips.keys()]
    if isinstance(raw_ips, list):
        ips: list[str] = []
        for item in raw_ips:
            if isinstance(item, str):
                ips.append(item)
            elif isinstance(item, dict) and "ip" in item:
                ips.append(str(item["ip"]))
        return ips
    return []


def resolve_secret(payload: dict[str, Any]) -> str:
    secret = str(payload.get("hysteria_stats_secret") or "")
    if secret:
        return secret
    secret_file = str(payload.get("hysteria_stats_secret_file") or "")
    if not secret_file:
        return ""
    try:
        return Path(secret_file).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def get_hysteria_online(*, stats_url: str, secret: str, timeout: float) -> tuple[dict[str, Any] | None, str]:
    return request_hysteria_json(method="GET", url=f"{stats_url}/online", secret=secret, timeout=timeout)


def kick_hysteria_users(*, stats_url: str, secret: str, emails: list[str], timeout: float) -> tuple[bool, str]:
    _, detail = request_hysteria_json(
        method="POST",
        url=f"{stats_url}/kick",
        secret=secret,
        timeout=timeout,
        payload=emails,
    )
    return detail == "", detail


def request_hysteria_json(
    *,
    method: str,
    url: str,
    secret: str,
    timeout: float,
    payload: Any | None = None,
) -> tuple[dict[str, Any] | None, str]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if secret:
        headers["Authorization"] = secret
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        return None, f"request timed out after {timeout}s"
    if not raw.strip():
        return {}, ""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"non-JSON response: {raw[:200]}"
    return decoded if isinstance(decoded, dict) else {}, ""


def looks_like_missing(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in ("not found", "not exist", "no such"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise
