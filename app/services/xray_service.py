from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from app.config import Settings

logger = logging.getLogger(__name__)


class XrayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()

    def user_email(self, telegram_id: int) -> str:
        return f"user-{telegram_id}@vpn.local"

    def build_vless_link(self, user_uuid: str, telegram_id: int) -> str:
        params: dict[str, str] = {"encryption": "none", "type": self.settings.vless_type}
        optional = {
            "security": self.settings.vless_security,
            "sni": self.settings.vless_sni,
            "flow": self.settings.vless_flow,
            "fp": self.settings.vless_fp,
            "pbk": self.settings.vless_pbk,
            "sid": self.settings.vless_sid,
            "path": self.settings.vless_path,
            "headerType": self.settings.vless_header_type,
        }
        for key, value in optional.items():
            if value:
                params[key] = value

        query = urlencode(params, doseq=False)
        remark = quote(f"{self.settings.vless_remark_prefix}-{telegram_id}")
        return f"vless://{user_uuid}@{self.settings.vless_public_host}:{self.settings.vless_public_port}?{query}#{remark}"

    async def enable_user(self, telegram_id: int, user_uuid: str) -> None:
        email = self.user_email(telegram_id)
        if self._is_api_mode():
            await self._add_user_via_api(email=email, user_uuid=user_uuid)
            return
        await self._upsert_client(email=email, user_uuid=user_uuid)

    async def disable_user(self, telegram_id: int) -> None:
        email = self.user_email(telegram_id)
        if self._is_api_mode():
            await self._remove_user_via_api(email=email)
            return
        await self._remove_client(email=email)

    async def sync_enabled_users(
        self,
        enabled_users: list[tuple[int, str]],
        all_managed_telegram_ids: list[int] | None = None,
    ) -> None:
        if self._is_api_mode():
            await self._sync_enabled_users_api(enabled_users, all_managed_telegram_ids=all_managed_telegram_ids)
            return
        await self._sync_enabled_users_config(enabled_users)

    async def _sync_enabled_users_config(self, enabled_users: list[tuple[int, str]]) -> None:
        async with self._lock:
            config = await asyncio.to_thread(self._read_config)
            inbound = self._find_inbound(config)
            clients: list[dict[str, Any]] = inbound.setdefault("settings", {}).setdefault("clients", [])
            expected = {self.user_email(tg_id): str(user_uuid) for tg_id, user_uuid in enabled_users}

            changed = False
            current_by_email = {
                client.get("email"): client
                for client in clients
                if self._is_managed_email(client.get("email"))
            }

            for email, uid in expected.items():
                existing = current_by_email.get(email)
                if existing is None:
                    clients.append(self._build_client(uid, email))
                    changed = True
                    continue
                if existing.get("id") != uid:
                    existing["id"] = uid
                    changed = True
                if self.settings.vless_flow and existing.get("flow") != self.settings.vless_flow:
                    existing["flow"] = self.settings.vless_flow
                    changed = True

            filtered = [
                client
                for client in clients
                if not self._is_managed_email(client.get("email")) or client.get("email") in expected
            ]
            if len(filtered) != len(clients):
                changed = True
                inbound["settings"]["clients"] = filtered

            if changed:
                await asyncio.to_thread(self._write_config, config)
                await self.reload_xray()

    async def _sync_enabled_users_api(
        self,
        enabled_users: list[tuple[int, str]],
        all_managed_telegram_ids: list[int] | None = None,
    ) -> None:
        expected_by_email = {self.user_email(tg_id): str(user_uuid) for tg_id, user_uuid in enabled_users}
        expected_ids = {tg_id for tg_id, _ in enabled_users}
        managed_ids = set(all_managed_telegram_ids or expected_ids)
        to_remove = [self.user_email(tg_id) for tg_id in sorted(managed_ids - expected_ids)]

        async with self._lock:
            for email in to_remove:
                await self._remove_user_via_api_unlocked(email=email)
            for email, user_uuid in expected_by_email.items():
                await self._add_user_via_api_unlocked(email=email, user_uuid=user_uuid)

    async def get_user_traffic(self, telegram_id: int) -> tuple[int, int] | None:
        if not self.settings.xray_api_enabled:
            return None

        email = self.user_email(telegram_id)
        args = self._api_command("statsquery")
        args.append(f"--pattern=user>>>{email}>>>traffic>>>")
        code, stdout, stderr = await self._run_args_command(args)
        if code != 0:
            logger.debug("Traffic query failed for %s: %s", email, stderr.strip())
            return None

        up = self._extract_stat_value(stdout, "uplink")
        down = self._extract_stat_value(stdout, "downlink")
        if up is None and down is None:
            return None
        return (up or 0, down or 0)

    async def get_user_online_ips(self, telegram_id: int) -> set[str] | None:
        if not self.settings.xray_api_enabled:
            return None

        email = self.user_email(telegram_id)
        args = self._api_command("statsonlineiplist")
        args.extend([f"-email={email}", "--json"])
        code, stdout, stderr = await self._run_args_command(args)
        if code != 0:
            if self._looks_like_not_found_error(stdout, stderr):
                return set()
            logger.debug("Online IP query failed for %s: %s", email, stderr.strip() or stdout.strip())
            return None

        try:
            payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            logger.debug("Online IP query returned non-JSON for %s: %s", email, stdout.strip())
            return None

        raw_ips = payload.get("ips", {})
        if isinstance(raw_ips, dict):
            return {str(ip) for ip in raw_ips.keys()}
        if isinstance(raw_ips, list):
            normalized: set[str] = set()
            for item in raw_ips:
                if isinstance(item, str):
                    normalized.add(item)
                elif isinstance(item, dict) and "ip" in item:
                    normalized.add(str(item["ip"]))
            return normalized
        return set()

    async def reload_xray(self) -> None:
        primary_command = self.settings.xray_reload_command.strip()
        code, stdout, stderr = await self._run_shell_command(primary_command)
        if code == 0:
            logger.info("Xray apply command succeeded (%s): %s", primary_command, stdout.strip())
            return

        logger.warning(
            "Primary Xray apply command failed (%s): %s",
            code,
            stderr.strip(),
        )

        fallback_command = self.settings.xray_restart_command.strip()
        if not fallback_command or fallback_command == primary_command:
            raise RuntimeError("Failed to apply Xray config")

        fallback_code, fallback_stdout, fallback_stderr = await self._run_shell_command(fallback_command)
        if fallback_code != 0:
            logger.error(
                "Fallback Xray restart failed (%s): %s",
                fallback_code,
                fallback_stderr.strip(),
            )
            raise RuntimeError("Failed to apply Xray config")

        logger.warning(
            "Fallback Xray restart command succeeded (%s): %s",
            fallback_command,
            fallback_stdout.strip(),
        )

    async def _upsert_client(self, email: str, user_uuid: str) -> None:
        async with self._lock:
            config = await asyncio.to_thread(self._read_config)
            inbound = self._find_inbound(config)
            clients: list[dict[str, Any]] = inbound.setdefault("settings", {}).setdefault("clients", [])

            changed = False
            existing = next((client for client in clients if client.get("email") == email), None)
            if existing is None:
                clients.append(self._build_client(user_uuid, email))
                changed = True
            else:
                if existing.get("id") != str(user_uuid):
                    existing["id"] = str(user_uuid)
                    changed = True
                if self.settings.vless_flow and existing.get("flow") != self.settings.vless_flow:
                    existing["flow"] = self.settings.vless_flow
                    changed = True

            if changed:
                await asyncio.to_thread(self._write_config, config)
                await self.reload_xray()

    async def _remove_client(self, email: str) -> None:
        async with self._lock:
            config = await asyncio.to_thread(self._read_config)
            inbound = self._find_inbound(config)
            clients: list[dict[str, Any]] = inbound.setdefault("settings", {}).setdefault("clients", [])
            updated = [client for client in clients if client.get("email") != email]
            if len(updated) != len(clients):
                inbound["settings"]["clients"] = updated
                await asyncio.to_thread(self._write_config, config)
                await self.reload_xray()

    async def _add_user_via_api(self, email: str, user_uuid: str) -> None:
        async with self._lock:
            await self._add_user_via_api_unlocked(email=email, user_uuid=user_uuid)

    async def _add_user_via_api_unlocked(self, email: str, user_uuid: str) -> None:
        payload = await asyncio.to_thread(
            self._build_adu_payload_from_config,
            email,
            user_uuid,
        )
        code, stdout, stderr = await self._run_adu_with_payload(payload)
        if code == 0 and self._adu_added_users_count(stdout) > 0:
            return
        if self._looks_like_user_exists_error(stdout, stderr):
            # Replace stale runtime user to enforce DB UUID/flow after crashes or manual drift.
            logger.warning("Xray API user exists, replacing runtime user: %s", email)
            await self._remove_user_via_api_unlocked(email=email)
            retry_code, retry_stdout, retry_stderr = await self._run_adu_with_payload(payload)
            if retry_code == 0 and self._adu_added_users_count(retry_stdout) > 0:
                return
            logger.error(
                "Xray API replace user failed (%s): %s",
                retry_code,
                retry_stderr.strip() or retry_stdout.strip(),
            )
            raise RuntimeError("Failed to replace user via Xray API")
        logger.error(
            "Xray API add user failed (%s): %s | stdout=%s",
            code,
            stderr.strip() or stdout.strip(),
            stdout.strip(),
        )
        raise RuntimeError("Failed to add user via Xray API")

    async def _run_adu_with_payload(self, payload: dict[str, Any]) -> tuple[int, str, str]:
        config_file = await asyncio.to_thread(self._write_temp_json, payload)
        try:
            code, stdout, stderr = await self._run_args_command(
                self._api_command("adu") + [config_file]
            )
        finally:
            await asyncio.to_thread(self._safe_unlink, config_file)
        return code, stdout, stderr

    def _build_adu_payload_from_config(self, email: str, user_uuid: str) -> dict[str, Any]:
        config = self._read_config()
        inbound = copy.deepcopy(self._find_inbound(config))
        inbound.setdefault("settings", {})
        inbound["settings"]["clients"] = [self._build_client(user_uuid=user_uuid, email=email)]
        return {"inbounds": [inbound]}

    async def _remove_user_via_api(self, email: str) -> None:
        async with self._lock:
            await self._remove_user_via_api_unlocked(email=email)

    async def _remove_user_via_api_unlocked(self, email: str) -> None:
        code, stdout, stderr = await self._run_args_command(
            self._api_command("rmu") + [f"-tag={self.settings.xray_inbound_tag}", email]
        )
        if code == 0:
            return
        if self._looks_like_user_missing_error(stdout, stderr):
            logger.debug("Xray API remove user ignored missing user: %s", email)
            return
        logger.error("Xray API remove user failed (%s): %s", code, stderr.strip() or stdout.strip())
        raise RuntimeError("Failed to remove user via Xray API")

    def _build_client(self, user_uuid: str, email: str) -> dict[str, Any]:
        data: dict[str, Any] = {"id": str(user_uuid), "email": email}
        if self.settings.vless_flow:
            data["flow"] = self.settings.vless_flow
        return data

    def _read_config(self) -> dict[str, Any]:
        config_path = self.settings.xray_config_path
        if not config_path.exists():
            raise FileNotFoundError(f"Xray config not found: {config_path}")
        return json.loads(config_path.read_text(encoding="utf-8"))

    def _write_config(self, data: dict[str, Any]) -> None:
        config_path = self.settings.xray_config_path
        backup_path = config_path.with_suffix(".json.bak")
        if config_path.exists():
            backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_inbound(self, config: dict[str, Any]) -> dict[str, Any]:
        inbounds = config.get("inbounds")
        if not isinstance(inbounds, list):
            raise ValueError("Invalid Xray config: inbounds is missing")
        for inbound in inbounds:
            if inbound.get("tag") == self.settings.xray_inbound_tag:
                return inbound
        raise ValueError(f"Inbound tag '{self.settings.xray_inbound_tag}' was not found")

    @staticmethod
    def _extract_stat_value(stats_output: str, direction: str) -> int | None:
        pattern = rf"user>>>.*>>>traffic>>>{direction}\s+value:\s+(\d+)"
        match = re.search(pattern, stats_output)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _is_managed_email(email: str | None) -> bool:
        return bool(email and email.startswith("user-") and email.endswith("@vpn.local"))

    def _api_command(self, subcommand: str) -> list[str]:
        return [
            self.settings.xray_bin_path,
            "api",
            subcommand,
            f"--server={self.settings.xray_api_server}",
            f"--timeout={self.settings.xray_api_timeout_seconds}",
        ]

    @staticmethod
    def _looks_like_user_exists_error(stdout: str, stderr: str) -> bool:
        text = f"{stdout}\n{stderr}".lower()
        markers = ("already exists", "already exist", "duplicate", "existed")
        return any(marker in text for marker in markers)

    @staticmethod
    def _looks_like_user_missing_error(stdout: str, stderr: str) -> bool:
        text = f"{stdout}\n{stderr}".lower()
        markers = ("not found", "not exist", "removed 0 user", "no such")
        return any(marker in text for marker in markers)

    @staticmethod
    def _looks_like_not_found_error(stdout: str, stderr: str) -> bool:
        text = f"{stdout}\n{stderr}".lower()
        return "not found" in text

    @staticmethod
    def _adu_added_users_count(stdout: str) -> int:
        match = re.search(r"Added\s+(\d+)\s+user\(s\)\s+in total", stdout)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    def _is_api_mode(self) -> bool:
        return self.settings.xray_control_mode.strip().lower() == "api"

    @staticmethod
    def _write_temp_json(payload: dict[str, Any]) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            return f.name

    @staticmethod
    def _safe_unlink(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    @staticmethod
    async def _run_shell_command(command: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode,
            stdout.decode("utf-8", errors="ignore"),
            stderr.decode("utf-8", errors="ignore"),
        )

    @staticmethod
    async def _run_args_command(args: list[str]) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode,
            stdout.decode("utf-8", errors="ignore"),
            stderr.decode("utf-8", errors="ignore"),
        )
