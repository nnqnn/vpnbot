from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OnlineDeviceStats:
    email: str
    xray_ips: frozenset[str]
    hysteria_count: int

    @property
    def total(self) -> int:
        return len(self.xray_ips) + self.hysteria_count


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
        if self.settings.vless_type == "xhttp":
            optional["host"] = self.settings.vless_sni or self.settings.vless_public_host
            optional["mode"] = self.settings.vless_xhttp_mode or "packet-up"
        for key, value in optional.items():
            if value:
                params[key] = value

        query = urlencode(params, doseq=False)
        remark = quote(f"{self.settings.vless_remark_prefix}-{telegram_id}")
        return f"vless://{user_uuid}@{self.settings.vless_public_host}:{self.settings.vless_public_port}?{query}#{remark}"

    async def enable_user(self, telegram_id: int, user_uuid: str) -> None:
        email = self.user_email(telegram_id)
        if self._is_api_mode():
            async with self._lock:
                for inbound_tag in self._managed_inbound_tags():
                    await self._add_user_via_api_unlocked(email=email, user_uuid=user_uuid, inbound_tag=inbound_tag)
                if self._is_ssh_api_mode() and self._persist_users_in_config():
                    await self._persist_remote_config_upsert_unlocked(email=email, user_uuid=user_uuid)
            return
        await self._upsert_client(email=email, user_uuid=user_uuid)

    async def disable_user(self, telegram_id: int) -> None:
        email = self.user_email(telegram_id)
        if self._is_api_mode():
            async with self._lock:
                for inbound_tag in self._managed_inbound_tags():
                    await self._remove_user_via_api_unlocked(email=email, inbound_tag=inbound_tag)
                if self._is_ssh_api_mode() and self._persist_users_in_config():
                    await self._persist_remote_config_remove_unlocked(email=email)
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
            if self._is_ssh_api_mode():
                managed_emails = [self.user_email(tg_id) for tg_id in sorted(managed_ids)]
                await self._sync_enabled_users_remote_helper_unlocked(expected_by_email, managed_emails)
                return
            for email in to_remove:
                for inbound_tag in self._managed_inbound_tags():
                    await self._remove_user_via_api_unlocked(email=email, inbound_tag=inbound_tag)
            for email, user_uuid in expected_by_email.items():
                for inbound_tag in self._managed_inbound_tags():
                    await self._add_user_via_api_unlocked(email=email, user_uuid=user_uuid, inbound_tag=inbound_tag)

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

    async def get_users_online_device_stats(self, telegram_ids: list[int]) -> dict[int, OnlineDeviceStats] | None:
        if not self.settings.xray_api_enabled:
            return None
        if not telegram_ids:
            return {}

        if self._is_ssh_api_mode():
            return await self._get_remote_online_device_stats(telegram_ids)

        stats: dict[int, OnlineDeviceStats] = {}
        for telegram_id in telegram_ids:
            email = self.user_email(telegram_id)
            ips = await self.get_user_online_ips(telegram_id)
            if ips is None:
                return None
            stats[telegram_id] = OnlineDeviceStats(email=email, xray_ips=frozenset(ips), hysteria_count=0)
        return stats

    async def kick_hysteria_user(self, telegram_id: int) -> bool:
        if not self._is_ssh_api_mode():
            return False

        payload = self._build_online_devices_payload([], kick_emails=[self.user_email(telegram_id)])
        local_payload = await asyncio.to_thread(self._write_temp_json, payload)
        remote_payload = f"/tmp/tgvpn-online-devices-{os.getpid()}-{Path(local_payload).name}"
        try:
            upload_code, upload_stdout, upload_stderr = await self._scp_to_remote(local_payload, remote_payload)
            if upload_code != 0:
                logger.warning("Failed to upload Hysteria kick payload: %s", upload_stderr.strip() or upload_stdout.strip())
                return False

            command = (
                f"python3 {shlex.quote(self.settings.online_devices_remote_helper_path)} "
                f"--payload {shlex.quote(remote_payload)}"
            )
            code, stdout, stderr = await self._run_remote_shell_command(command)
            if code != 0:
                logger.warning("Hysteria kick helper failed: %s", stderr.strip() or stdout.strip())
                return False
            return True
        finally:
            await self._run_remote_shell_command(f"rm -f {shlex.quote(remote_payload)}")
            await asyncio.to_thread(self._safe_unlink, local_payload)

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

    async def _add_user_via_api_unlocked(
        self,
        email: str,
        user_uuid: str,
        inbound_tag: str | None = None,
    ) -> None:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        config = await self._read_config_async()
        payload = self._build_adu_payload_from_config(config, email, user_uuid, inbound_tag=inbound_tag)
        code, stdout, stderr = await self._run_adu_with_payload(payload)
        if code == 0 and self._adu_added_users_count(stdout) > 0:
            return
        if self._looks_like_user_exists_error(stdout, stderr):
            runtime_code, runtime_stdout, runtime_stderr = await self._get_user_via_api_unlocked(
                email=email,
                inbound_tag=inbound_tag,
            )
            if runtime_code == 0 and self._runtime_user_matches(
                runtime_stdout,
                email=email,
                user_uuid=user_uuid,
                flow=self._flow_for_inbound_tag(inbound_tag),
            ):
                logger.debug("Xray API user already present with expected UUID: %s", email)
                return

            logger.warning("Xray API user exists, replacing runtime user: %s", email)
            await self._remove_user_via_api_unlocked(email=email, inbound_tag=inbound_tag)
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

    async def _sync_enabled_users_remote_helper_unlocked(
        self,
        expected_by_email: dict[str, str],
        managed_emails: list[str],
    ) -> None:
        payload = {
            "xray_bin_path": self.settings.xray_bin_path,
            "xray_api_server": self.settings.xray_api_server,
            "xray_api_timeout_seconds": self.settings.xray_api_timeout_seconds,
            "command_timeout_seconds": self.settings.xray_remote_command_timeout_seconds,
            "xray_config_path": str(self.settings.xray_config_path),
            "xray_inbound_tag": self.settings.xray_inbound_tag,
            "xray_extra_inbound_tags": self._extra_inbound_tags(),
            "xray_flow_inbound_tags": self._flow_inbound_tags(),
            "persist_users_in_config": self._persist_users_in_config(),
            "vless_flow": self.settings.vless_flow,
            "expected": expected_by_email,
            "managed_emails": managed_emails,
        }
        local_payload = await asyncio.to_thread(self._write_temp_json, payload)
        remote_payload = f"/tmp/tgvpn-reconcile-{os.getpid()}-{Path(local_payload).name}"
        try:
            upload_code, upload_stdout, upload_stderr = await self._scp_to_remote(local_payload, remote_payload)
            if upload_code != 0:
                raise RuntimeError(
                    f"Failed to upload Xray reconcile payload: {upload_stderr.strip() or upload_stdout.strip()}"
                )

            command = (
                f"python3 {shlex.quote(self.settings.xray_remote_helper_path)} "
                f"--payload {shlex.quote(remote_payload)}"
            )
            code, stdout, stderr = await self._run_remote_shell_command(command)
            if code != 0:
                raise RuntimeError(f"Remote Xray reconcile failed: {stderr.strip() or stdout.strip()}")
            logger.info("Remote Xray reconcile summary: %s", stdout.strip())
        finally:
            await self._run_remote_shell_command(f"rm -f {shlex.quote(remote_payload)}")
            await asyncio.to_thread(self._safe_unlink, local_payload)

    async def _get_remote_online_device_stats(self, telegram_ids: list[int]) -> dict[int, OnlineDeviceStats] | None:
        emails_by_telegram_id = {telegram_id: self.user_email(telegram_id) for telegram_id in telegram_ids}
        payload = self._build_online_devices_payload(list(emails_by_telegram_id.values()), kick_emails=[])
        local_payload = await asyncio.to_thread(self._write_temp_json, payload)
        remote_payload = f"/tmp/tgvpn-online-devices-{os.getpid()}-{Path(local_payload).name}"
        try:
            upload_code, upload_stdout, upload_stderr = await self._scp_to_remote(local_payload, remote_payload)
            if upload_code != 0:
                logger.warning("Failed to upload online-device payload: %s", upload_stderr.strip() or upload_stdout.strip())
                return None

            command = (
                f"python3 {shlex.quote(self.settings.online_devices_remote_helper_path)} "
                f"--payload {shlex.quote(remote_payload)}"
            )
            code, stdout, stderr = await self._run_remote_shell_command(command)
            if code != 0:
                logger.warning("Remote online-device helper failed: %s", stderr.strip() or stdout.strip())
                return None
            return self._parse_online_device_stats(stdout, emails_by_telegram_id)
        finally:
            await self._run_remote_shell_command(f"rm -f {shlex.quote(remote_payload)}")
            await asyncio.to_thread(self._safe_unlink, local_payload)

    def _build_online_devices_payload(self, emails: list[str], *, kick_emails: list[str]) -> dict[str, Any]:
        return {
            "xray_bin_path": self.settings.xray_bin_path,
            "xray_api_server": self.settings.xray_api_server,
            "xray_api_timeout_seconds": self.settings.xray_api_timeout_seconds,
            "command_timeout_seconds": self.settings.xray_remote_command_timeout_seconds,
            "hysteria_stats_url": self.settings.hysteria2_stats_url,
            "hysteria_stats_secret": self.settings.hysteria2_stats_secret,
            "hysteria_stats_secret_file": self.settings.hysteria2_stats_secret_file,
            "hysteria_stats_timeout_seconds": self.settings.hysteria2_stats_timeout_seconds,
            "emails": emails,
            "kick_emails": kick_emails,
        }

    @staticmethod
    def _parse_online_device_stats(
        stdout: str,
        emails_by_telegram_id: dict[int, str],
    ) -> dict[int, OnlineDeviceStats] | None:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("Online-device helper returned non-JSON: %s", stdout.strip())
            return None
        if not isinstance(payload, dict) or not payload.get("ok"):
            logger.warning("Online-device helper returned errors: %s", payload)
            return None
        users = payload.get("users")
        if not isinstance(users, dict):
            logger.warning("Online-device helper returned invalid users payload: %s", payload)
            return None

        stats: dict[int, OnlineDeviceStats] = {}
        for telegram_id, email in emails_by_telegram_id.items():
            raw = users.get(email, {})
            raw_ips = raw.get("xray_ips", []) if isinstance(raw, dict) else []
            xray_ips = frozenset(str(ip) for ip in raw_ips if str(ip))
            raw_hysteria_count = raw.get("hysteria_count", 0) if isinstance(raw, dict) else 0
            try:
                hysteria_count = int(raw_hysteria_count)
            except (TypeError, ValueError):
                hysteria_count = 0
            stats[telegram_id] = OnlineDeviceStats(
                email=email,
                xray_ips=xray_ips,
                hysteria_count=max(0, hysteria_count),
            )
        return stats

    async def _run_adu_with_payload(self, payload: dict[str, Any]) -> tuple[int, str, str]:
        if self._is_ssh_api_mode():
            return await self._run_remote_adu_with_payload(payload)

        config_file = await asyncio.to_thread(self._write_temp_json, payload)
        try:
            code, stdout, stderr = await self._run_args_command(
                self._api_command("adu") + [config_file]
            )
        finally:
            await asyncio.to_thread(self._safe_unlink, config_file)
        return code, stdout, stderr

    def _build_adu_payload_from_config(
        self,
        config: dict[str, Any],
        email: str,
        user_uuid: str,
        inbound_tag: str | None = None,
    ) -> dict[str, Any]:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        inbound = copy.deepcopy(self._find_inbound(config, inbound_tag=inbound_tag))
        inbound.setdefault("settings", {})
        if self._is_hysteria_inbound(inbound):
            inbound["settings"]["users"] = [self._build_hysteria_user(user_uuid=user_uuid, email=email)]
            inbound["settings"].pop("clients", None)
            inbound["settings"].setdefault("version", 2)
        else:
            inbound["settings"]["clients"] = [
                self._build_client(user_uuid=user_uuid, email=email, flow=self._flow_for_inbound_tag(inbound_tag))
            ]
            inbound["settings"].setdefault("decryption", "none")
        return {"inbounds": [inbound]}

    async def _remove_user_via_api(self, email: str) -> None:
        async with self._lock:
            await self._remove_user_via_api_unlocked(email=email)

    async def _remove_user_via_api_unlocked(self, email: str, inbound_tag: str | None = None) -> None:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        code, stdout, stderr = await self._run_args_command(
            self._api_command("rmu") + [f"-tag={inbound_tag}", email]
        )
        if code == 0:
            return
        if self._looks_like_user_missing_error(stdout, stderr):
            logger.debug("Xray API remove user ignored missing user: %s", email)
            return
        logger.error("Xray API remove user failed (%s): %s", code, stderr.strip() or stdout.strip())
        raise RuntimeError("Failed to remove user via Xray API")

    async def _get_user_via_api_unlocked(self, email: str, inbound_tag: str | None = None) -> tuple[int, str, str]:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        return await self._run_args_command(
            self._api_command("inbounduser")
            + [f"-tag={inbound_tag}", f"-email={email}", "--json"]
        )

    def _build_client(self, user_uuid: str, email: str, flow: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"id": str(user_uuid), "email": email}
        client_flow = self.settings.vless_flow if flow is None else flow
        if client_flow:
            data["flow"] = client_flow
        return data

    @staticmethod
    def _build_hysteria_user(user_uuid: str, email: str) -> dict[str, Any]:
        return {"auth": str(user_uuid), "email": email, "level": 0}

    @staticmethod
    def _is_hysteria_inbound(inbound: dict[str, Any]) -> bool:
        return inbound.get("protocol") == "hysteria"

    def _read_config(self) -> dict[str, Any]:
        config_path = self.settings.xray_config_path
        if not config_path.exists():
            raise FileNotFoundError(f"Xray config not found: {config_path}")
        return json.loads(config_path.read_text(encoding="utf-8"))

    async def _read_config_async(self) -> dict[str, Any]:
        if not self._is_ssh_api_mode():
            return await asyncio.to_thread(self._read_config)

        code, stdout, stderr = await self._run_remote_shell_command(
            f"cat {shlex.quote(str(self.settings.xray_config_path))}"
        )
        if code != 0:
            raise RuntimeError(f"Failed to read remote Xray config: {stderr.strip() or stdout.strip()}")
        return json.loads(stdout)

    def _write_config(self, data: dict[str, Any]) -> None:
        config_path = self.settings.xray_config_path
        backup_path = config_path.with_suffix(".json.bak")
        if config_path.exists():
            backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _write_config_async(self, data: dict[str, Any]) -> None:
        if not self._is_ssh_api_mode():
            await asyncio.to_thread(self._write_config, data)
            return

        config_file = await asyncio.to_thread(self._write_temp_json, data)
        remote_config = str(self.settings.xray_config_path)
        remote_tmp = f"{remote_config}.tmp-{os.getpid()}-{Path(config_file).name}"
        remote_backup = f"{remote_config}.bak.tgvpn-sync"
        try:
            upload_code, upload_stdout, upload_stderr = await self._scp_to_remote(config_file, remote_tmp)
            if upload_code != 0:
                raise RuntimeError(f"Failed to upload remote Xray config: {upload_stderr.strip() or upload_stdout.strip()}")

            command = " && ".join(
                [
                    f"{shlex.quote(self.settings.xray_bin_path)} run -test -c {shlex.quote(remote_tmp)}",
                    f"cp {shlex.quote(remote_config)} {shlex.quote(remote_backup)}",
                    f"mv {shlex.quote(remote_tmp)} {shlex.quote(remote_config)}",
                ]
            )
            code, stdout, stderr = await self._run_remote_shell_command(command)
            if code != 0:
                raise RuntimeError(f"Failed to persist remote Xray config: {stderr.strip() or stdout.strip()}")
        finally:
            await self._run_remote_shell_command(f"rm -f {shlex.quote(remote_tmp)}")
            await asyncio.to_thread(self._safe_unlink, config_file)

    def _find_inbound(self, config: dict[str, Any], inbound_tag: str | None = None) -> dict[str, Any]:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        inbounds = config.get("inbounds")
        if not isinstance(inbounds, list):
            raise ValueError("Invalid Xray config: inbounds is missing")
        for inbound in inbounds:
            if inbound.get("tag") == inbound_tag:
                return inbound
        raise ValueError(f"Inbound tag '{inbound_tag}' was not found")

    async def _persist_remote_config_upsert_unlocked(self, email: str, user_uuid: str) -> None:
        config = await self._read_config_async()
        changed = False
        for inbound_tag in self._managed_inbound_tags():
            changed = self._upsert_managed_client_in_config(
                config,
                email=email,
                user_uuid=user_uuid,
                inbound_tag=inbound_tag,
            ) or changed
        if changed:
            await self._write_config_async(config)

    async def _persist_remote_config_remove_unlocked(self, email: str) -> None:
        config = await self._read_config_async()
        changed = False
        for inbound_tag in self._managed_inbound_tags():
            changed = self._remove_managed_client_from_config(
                config,
                email=email,
                inbound_tag=inbound_tag,
            ) or changed
        if changed:
            await self._write_config_async(config)

    async def _persist_remote_config_sync_unlocked(self, expected_by_email: dict[str, str]) -> None:
        config = await self._read_config_async()
        changed = False
        for inbound_tag in self._managed_inbound_tags():
            changed = self._sync_managed_clients_in_config(
                config,
                expected_by_email,
                inbound_tag=inbound_tag,
            ) or changed
        if changed:
            await self._write_config_async(config)

    def _upsert_managed_client_in_config(
        self,
        config: dict[str, Any],
        *,
        email: str,
        user_uuid: str,
        inbound_tag: str | None = None,
    ) -> bool:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        inbound = self._find_inbound(config, inbound_tag=inbound_tag)
        settings = inbound.setdefault("settings", {})
        key = "users" if self._is_hysteria_inbound(inbound) else "clients"
        users: list[dict[str, Any]] = settings.setdefault(key, [])
        next_user = (
            self._build_hysteria_user(user_uuid=user_uuid, email=email)
            if self._is_hysteria_inbound(inbound)
            else self._build_client(user_uuid=user_uuid, email=email, flow=self._flow_for_inbound_tag(inbound_tag))
        )
        for index, user in enumerate(users):
            if user.get("email") == email:
                if user == next_user:
                    return False
                users[index] = next_user
                return True
        users.append(next_user)
        return True

    def _remove_managed_client_from_config(
        self,
        config: dict[str, Any],
        *,
        email: str,
        inbound_tag: str | None = None,
    ) -> bool:
        inbound = self._find_inbound(config, inbound_tag=inbound_tag)
        settings = inbound.setdefault("settings", {})
        key = "users" if self._is_hysteria_inbound(inbound) else "clients"
        users: list[dict[str, Any]] = settings.setdefault(key, [])
        updated = [user for user in users if user.get("email") != email]
        if len(updated) == len(users):
            return False
        inbound["settings"][key] = updated
        return True

    def _sync_managed_clients_in_config(
        self,
        config: dict[str, Any],
        expected_by_email: dict[str, str],
        inbound_tag: str | None = None,
    ) -> bool:
        inbound_tag = inbound_tag or self.settings.xray_inbound_tag
        inbound = self._find_inbound(config, inbound_tag=inbound_tag)
        settings = inbound.setdefault("settings", {})
        key = "users" if self._is_hysteria_inbound(inbound) else "clients"
        users: list[dict[str, Any]] = settings.setdefault(key, [])
        non_managed_clients = [
            user
            for user in users
            if not self._is_managed_email(user.get("email") if isinstance(user, dict) else None)
        ]
        expected_clients = [
            (
                self._build_hysteria_user(user_uuid=expected_by_email[email], email=email)
                if self._is_hysteria_inbound(inbound)
                else self._build_client(
                    user_uuid=expected_by_email[email],
                    email=email,
                    flow=self._flow_for_inbound_tag(inbound_tag),
                )
            )
            for email in sorted(expected_by_email)
        ]
        updated = non_managed_clients + expected_clients
        if users == updated:
            return False
        inbound["settings"][key] = updated
        return True

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

    def _runtime_user_matches(
        self,
        stdout: str,
        *,
        email: str,
        user_uuid: str,
        flow: str | None = None,
    ) -> bool:
        try:
            normalized = json.dumps(json.loads(stdout), ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            normalized = stdout
        if email not in normalized or str(user_uuid) not in normalized:
            return False
        expected_flow = self.settings.vless_flow if flow is None else flow
        if expected_flow and '"flow"' in normalized and expected_flow not in normalized:
            return False
        return True

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
        return self.settings.xray_control_mode.strip().lower() in {"api", "ssh_api", "remote_api"}

    def _is_ssh_api_mode(self) -> bool:
        return self.settings.xray_control_mode.strip().lower() in {"ssh_api", "remote_api"}

    def _persist_users_in_config(self) -> bool:
        return bool(getattr(self.settings, "xray_persist_users_in_config", False))

    def _extra_inbound_tags(self) -> list[str]:
        raw_value = getattr(self.settings, "xray_extra_inbound_tags", "")
        return [tag.strip() for tag in str(raw_value).split(",") if tag.strip()]

    def _flow_inbound_tags(self) -> list[str]:
        raw_value = getattr(self.settings, "xray_flow_inbound_tags", "")
        tags = [tag.strip() for tag in str(raw_value).split(",") if tag.strip()]
        if tags:
            return tags
        return [self.settings.xray_inbound_tag]

    def _managed_inbound_tags(self) -> list[str]:
        tags = [self.settings.xray_inbound_tag]
        for tag in self._extra_inbound_tags():
            if tag not in tags:
                tags.append(tag)
        return tags

    def _flow_for_inbound_tag(self, inbound_tag: str) -> str:
        return self.settings.vless_flow if inbound_tag in set(self._flow_inbound_tags()) else ""

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

    async def _run_args_command(self, args: list[str]) -> tuple[int, str, str]:
        if self._is_ssh_api_mode():
            return await self._run_remote_args_command(args)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout = self.settings.xray_remote_command_timeout_seconds
        try:
            if timeout > 0:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            else:
                stdout, stderr = await process.communicate()
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return (
                124,
                stdout.decode("utf-8", errors="ignore"),
                (stderr.decode("utf-8", errors="ignore") + f"\ncommand timed out after {timeout}s").strip(),
            )
        return (
            process.returncode,
            stdout.decode("utf-8", errors="ignore"),
            stderr.decode("utf-8", errors="ignore"),
        )

    async def _run_remote_args_command(self, args: list[str]) -> tuple[int, str, str]:
        command = " ".join(shlex.quote(str(arg)) for arg in args)
        return await self._run_remote_shell_command(command)

    async def _run_remote_adu_with_payload(self, payload: dict[str, Any]) -> tuple[int, str, str]:
        config_file = await asyncio.to_thread(self._write_temp_json, payload)
        remote_file = f"/tmp/tgvpn-adu-{os.getpid()}-{Path(config_file).name}"
        try:
            upload_code, upload_stdout, upload_stderr = await self._scp_to_remote(config_file, remote_file)
            if upload_code != 0:
                return upload_code, upload_stdout, upload_stderr
            return await self._run_remote_args_command(self._api_command("adu") + [remote_file])
        finally:
            await self._run_remote_shell_command(f"rm -f {shlex.quote(remote_file)}")
            await asyncio.to_thread(self._safe_unlink, config_file)

    async def _run_remote_shell_command(self, command: str) -> tuple[int, str, str]:
        return await self._run_process_args(
            self._ssh_command(command),
            env=self._ssh_env(),
            timeout=self.settings.xray_remote_command_timeout_seconds,
        )

    async def _scp_to_remote(self, local_path: str, remote_path: str) -> tuple[int, str, str]:
        args = self._sshpass_prefix()
        args.extend(
            [
                "scp",
                "-P",
                str(self.settings.xray_remote_port),
                "-o",
                "StrictHostKeyChecking=accept-new",
            ]
        )
        if self.settings.xray_remote_key_path:
            args.extend(["-i", self.settings.xray_remote_key_path])
        args.extend(
            [
                local_path,
                f"{self.settings.xray_remote_user}@{self.settings.xray_remote_host}:{remote_path}",
            ]
        )
        return await self._run_process_args(
            args,
            env=self._ssh_env(),
            timeout=self.settings.xray_remote_command_timeout_seconds,
        )

    def _ssh_command(self, command: str) -> list[str]:
        if not self.settings.xray_remote_host:
            raise RuntimeError("XRAY_REMOTE_HOST is required for ssh_api mode")

        args = self._sshpass_prefix()
        args.extend(
            [
                "ssh",
                "-p",
                str(self.settings.xray_remote_port),
                "-o",
                "StrictHostKeyChecking=accept-new",
            ]
        )
        if self.settings.xray_remote_key_path:
            args.extend(["-i", self.settings.xray_remote_key_path])
        args.extend([f"{self.settings.xray_remote_user}@{self.settings.xray_remote_host}", command])
        return args

    def _sshpass_prefix(self) -> list[str]:
        return ["sshpass", "-e"] if self.settings.xray_remote_password else []

    def _ssh_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.settings.xray_remote_password:
            env["SSHPASS"] = self.settings.xray_remote_password
        return env

    @staticmethod
    async def _run_process_args(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            if timeout and timeout > 0:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            else:
                stdout, stderr = await process.communicate()
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return (
                124,
                stdout.decode("utf-8", errors="ignore"),
                (stderr.decode("utf-8", errors="ignore") + f"\ncommand timed out after {timeout}s").strip(),
            )
        return (
            process.returncode,
            stdout.decode("utf-8", errors="ignore"),
            stderr.decode("utf-8", errors="ignore"),
        )
