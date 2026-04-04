from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
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
        if self.settings.xray_control_mode != "config":
            logger.warning("XRAY_CONTROL_MODE=%s is not implemented, fallback to config mode", self.settings.xray_control_mode)
        await self._upsert_client(email=self.user_email(telegram_id), user_uuid=user_uuid)

    async def disable_user(self, telegram_id: int) -> None:
        if self.settings.xray_control_mode != "config":
            logger.warning("XRAY_CONTROL_MODE=%s is not implemented, fallback to config mode", self.settings.xray_control_mode)
        await self._remove_client(email=self.user_email(telegram_id))

    async def sync_enabled_users(self, enabled_users: list[tuple[int, str]]) -> None:
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

    async def get_user_traffic(self, telegram_id: int) -> tuple[int, int] | None:
        if not self.settings.xray_api_enabled:
            return None

        email = self.user_email(telegram_id)
        cmd = (
            f"{shlex.quote(self.settings.xray_bin_path)} api statsquery "
            f"--server={shlex.quote(self.settings.xray_api_server)} "
            f"--pattern 'user>>>{email}>>>traffic>>>'"
        )
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.debug("Traffic query failed for %s: %s", email, stderr.decode("utf-8", errors="ignore"))
            return None

        text = stdout.decode("utf-8", errors="ignore")
        up = self._extract_stat_value(text, "uplink")
        down = self._extract_stat_value(text, "downlink")
        if up is None and down is None:
            return None
        return (up or 0, down or 0)

    async def reload_xray(self) -> None:
        cmd = self.settings.xray_reload_command
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(
                "Xray reload failed (%s): %s",
                process.returncode,
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            raise RuntimeError("Failed to reload Xray")
        logger.info("Xray reloaded: %s", stdout.decode("utf-8", errors="ignore").strip())

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
