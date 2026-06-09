from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import User, UserStatus
from app.db.repositories import ProductPurchaseRepository, SubscriptionTokenRepository
from app.db.session import session_scope
from app.services.billing_service import WHITELIST_PRODUCT_CODE
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class SubscriptionSnapshotService:
    def __init__(self, settings: Settings, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_maker = session_maker
        self._lock = asyncio.Lock()

    async def sync_once(self) -> dict[str, Any]:
        async with self._lock:
            async with session_scope(self.session_maker) as session:
                snapshot = await self.build_snapshot(session)

            await asyncio.to_thread(self._write_local_snapshot, snapshot)
            if self.settings.xray_remote_host:
                await self._upload_remote_snapshot()

            logger.info(
                "Subscription snapshot synced: users=%s path=%s remote=%s",
                len(snapshot.get("users", {})),
                self.settings.subscription_snapshot_path,
                bool(self.settings.xray_remote_host),
            )
            return snapshot

    async def build_snapshot(self, session: AsyncSession) -> dict[str, Any]:
        now = utc_now()
        result = await session.execute(select(User).order_by(User.id.asc()))
        users = list(result.scalars().all())
        whitelist_user_ids = await ProductPurchaseRepository(session).user_ids_by_product_code(WHITELIST_PRODUCT_CODE)
        token_repo = SubscriptionTokenRepository(session)
        tokens_by_user_id = {}
        for user in users:
            token = await token_repo.get_or_create_for_user(user.id)
            tokens_by_user_id[user.id] = token.token

        return build_snapshot_payload(
            users=users,
            whitelist_user_ids=whitelist_user_ids,
            tokens_by_user_id=tokens_by_user_id,
            product=self.settings.subscription_product,
            now=now,
        )

    def _write_local_snapshot(self, snapshot: dict[str, Any]) -> None:
        path = self.settings.subscription_snapshot_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(path)

    async def _upload_remote_snapshot(self) -> None:
        local_path = self.settings.subscription_snapshot_path
        remote_path = self.settings.subscription_remote_snapshot_path
        remote_tmp_path = f"{remote_path}.tmp"
        remote_dir = str(Path(remote_path).parent)

        await self._run_remote_shell(f"mkdir -p {shlex.quote(remote_dir)}")
        await self._scp_to_remote(local_path, remote_tmp_path)
        await self._run_remote_shell(f"mv {shlex.quote(remote_tmp_path)} {shlex.quote(remote_path)}")

    async def _scp_to_remote(self, local_path: Path, remote_path: str) -> None:
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
                str(local_path),
                f"{self.settings.xray_remote_user}@{self.settings.xray_remote_host}:{remote_path}",
            ]
        )
        code, stdout, stderr = await self._run_process(
            args,
            env=self._ssh_env(),
            timeout=self.settings.xray_remote_command_timeout_seconds,
        )
        if code != 0:
            raise RuntimeError(f"Failed to upload subscription snapshot: {stderr.strip() or stdout.strip()}")

    async def _run_remote_shell(self, command: str) -> tuple[int, str, str]:
        args = self._ssh_command(command)
        code, stdout, stderr = await self._run_process(
            args,
            env=self._ssh_env(),
            timeout=self.settings.xray_remote_command_timeout_seconds,
        )
        if code != 0:
            raise RuntimeError(f"Remote subscription command failed: {stderr.strip() or stdout.strip()}")
        return code, stdout, stderr

    def _ssh_command(self, command: str) -> list[str]:
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
    async def _run_process(
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


def write_snapshot_to_file(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent)) as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        tmp_name = f.name
    Path(tmp_name).replace(path)


def build_snapshot_payload(
    *,
    users,
    whitelist_user_ids: set[int],
    tokens_by_user_id: dict[int, str],
    product: str,
    now,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "product": product,
        "generated_at": now.isoformat(),
        "users": {},
    }
    for user in users:
        token = tokens_by_user_id[user.id]
        expiration = user.expiration_date
        main_vpn_active = (
            user.status == UserStatus.active
            and not user.device_limit_blocked
            and expiration is not None
            and expiration > now
        )
        payload["users"][token] = {
            "user_id": user.id,
            "telegram_id": int(user.telegram_id),
            "uuid": str(user.uuid),
            "main_vpn_active": main_vpn_active,
            "whitelist_enabled": user.id in whitelist_user_ids,
            "expire": int(expiration.timestamp()) if expiration and main_vpn_active else 0,
            "status": user.status.value,
            "device_limit_blocked": bool(user.device_limit_blocked),
        }
    return payload
