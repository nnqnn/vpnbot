from __future__ import annotations

import json
import logging
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from dotenv import load_dotenv

from app.services.subscription_builder import (
    SubscriptionProfile,
    build_subscription_response,
)

logger = logging.getLogger(__name__)


class RuntimeConfig:
    def __init__(self) -> None:
        load_dotenv()
        self.listen_host = _env("SUBSCRIPTION_LISTEN_HOST", "127.0.0.1")
        self.listen_port = int(_env("SUBSCRIPTION_LISTEN_PORT", "8088"))
        self.snapshot_path = Path(_env("SUBSCRIPTION_SNAPSHOT_PATH", "/var/lib/tgvpn/subscription_snapshot.json"))
        self.origin_secret = _env("SUBSCRIPTION_ORIGIN_SECRET", "")
        self.whitelist_source_url = _env(
            "WHITELIST_SOURCE_URL",
            "https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt",
        )
        self.whitelist_cache_seconds = int(_env("WHITELIST_CACHE_SECONDS", "300"))
        self.profile = SubscriptionProfile(
            product=_env("SUBSCRIPTION_PRODUCT", "kVPN"),
            public_base_url=_env("SUBSCRIPTION_PUBLIC_BASE_URL", "https://vpn.nnqnn.tech"),
            profile_title=_env("SUBSCRIPTION_PROFILE_TITLE", "kVPN @kkVPNrobot"),
            update_interval_hours=int(_env("SUBSCRIPTION_UPDATE_INTERVAL_HOURS", "1")),
            traffic_total_bytes=int(_env("SUBSCRIPTION_TRAFFIC_TOTAL_BYTES", "0")),
            support_url=_env("SUPPORT_URL", "https://t.me/kvpn_support"),
            announce_url=_env("SUBSCRIPTION_ANNOUNCE_URL", "https://t.me/kvpnpublic"),
            announce_text=_env("SUBSCRIPTION_ANNOUNCE_TEXT", "kVPN: subscription auto-updates."),
            vless_public_host=_env("VLESS_PUBLIC_HOST", "s2.nnqnn.tech"),
            vless_public_port=int(_env("VLESS_PUBLIC_PORT", "9443")),
            vless_security=_env("VLESS_SECURITY", "reality"),
            vless_type=_env("VLESS_TYPE", "tcp"),
            vless_sni=_env("VLESS_SNI", "www.cloudflare.com"),
            vless_flow=_env("VLESS_FLOW", "xtls-rprx-vision"),
            vless_fp=_env("VLESS_FP", "chrome"),
            vless_pbk=_env("VLESS_PBK", ""),
            vless_sid=_env("VLESS_SID", "a1b2c3d4e5f6a7b8"),
            vless_path=_env("VLESS_PATH", ""),
            vless_header_type=_env("VLESS_HEADER_TYPE", ""),
            vless_remark_prefix=_env("VLESS_REMARK_PREFIX", "kVPN"),
            whitelist_max_nodes=int(_env("WHITELIST_MAX_NODES", "300")),
        )


class SubscriptionState:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._whitelist_text = ""
        self._whitelist_loaded_at = 0.0

    def load_snapshot(self) -> dict:
        try:
            return json.loads(self.config.snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning("Subscription snapshot not found: %s", self.config.snapshot_path)
            return {"version": 1, "users": {}}
        except json.JSONDecodeError:
            logger.exception("Subscription snapshot is not valid JSON: %s", self.config.snapshot_path)
            return {"version": 1, "users": {}}

    def whitelist_text(self) -> str:
        now = time.monotonic()
        if self._whitelist_text and now - self._whitelist_loaded_at < self.config.whitelist_cache_seconds:
            return self._whitelist_text

        try:
            response = httpx.get(
                self.config.whitelist_source_url,
                headers={"User-Agent": "tgvpn-subscription-server"},
                timeout=10,
            )
            response.raise_for_status()
        except Exception:
            logger.exception("Cannot fetch whitelist source")
            if self._whitelist_text:
                return self._whitelist_text
            return ""

        self._whitelist_text = response.text
        self._whitelist_loaded_at = now
        return self._whitelist_text


class SubscriptionHandler(BaseHTTPRequestHandler):
    server_version = "TGVPNSubscription/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_text(HTTPStatus.OK, "ok")
            return

        if self.server.state.config.origin_secret:
            provided = self.headers.get("x-tgvpn-origin-secret", "")
            if provided != self.server.state.config.origin_secret:
                self._send_text(HTTPStatus.NOT_FOUND, "not found")
                return

        path = urlsplit(self.path).path
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3 or parts[0] != "sub":
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return

        _, product, token = parts
        snapshot = self.server.state.load_snapshot()
        raw_user = snapshot.get("users", {}).get(token)
        if product != self.server.state.config.profile.product or not isinstance(raw_user, dict):
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return

        response = build_subscription_response(
            snapshot=snapshot,
            product=product,
            token=token,
            profile=self.server.state.config.profile,
            whitelist_source_text=self.server.state.whitelist_text() if raw_user.get("whitelist_enabled") else "",
        )
        if response is None:
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return

        self.send_response(HTTPStatus.OK)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response.body.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        message = format % args
        logger.info("%s - %s", self.address_string(), self._redact_tokens(message))

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        self.send_response(status)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    @staticmethod
    def _redact_tokens(message: str) -> str:
        parts = message.split(" ")
        redacted: list[str] = []
        for part in parts:
            if part.startswith("/sub/") or part.startswith("/add/"):
                bits = part.split("/")
                if len(bits) >= 4:
                    bits[3] = "***"
                    part = "/".join(bits)
            redacted.append(part)
        return " ".join(redacted)


class SubscriptionHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, request_handler, state: SubscriptionState) -> None:
        super().__init__(server_address, request_handler)
        self.state = state


def main() -> None:
    logging.basicConfig(
        level=_env("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    config = RuntimeConfig()
    state = SubscriptionState(config)
    server = SubscriptionHTTPServer((config.listen_host, config.listen_port), SubscriptionHandler, state)
    logger.info("Subscription server listening on %s:%s", config.listen_host, config.listen_port)
    server.serve_forever()


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


if __name__ == "__main__":
    main()
