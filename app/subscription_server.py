from __future__ import annotations

import json
import logging
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from dotenv import load_dotenv

from app.services.subscription_builder import (
    SubscriptionProfile,
    build_subscription_response,
    build_happ_link,
    build_subscription_url,
    build_xray_json_subscription_response,
)

logger = logging.getLogger(__name__)


class RuntimeConfig:
    def __init__(self) -> None:
        load_dotenv()
        self.listen_host = _env("SUBSCRIPTION_LISTEN_HOST", "127.0.0.1")
        self.listen_port = int(_env("SUBSCRIPTION_LISTEN_PORT", "8088"))
        self.snapshot_path = Path(_env("SUBSCRIPTION_SNAPSHOT_PATH", "/var/lib/tgvpn/subscription_snapshot.json"))
        self.origin_secret = _env("SUBSCRIPTION_ORIGIN_SECRET", "")
        self.response_format = _env("SUBSCRIPTION_RESPONSE_FORMAT", "xray_json").strip().lower()
        self.whitelist_source_url = _env(
            "WHITELIST_SOURCE_URL",
            "https://raw.githubusercontent.com/zieng2/wl/main/vless_universal.txt",
        )
        self.whitelist_profile_url = _env("WHITELIST_PROFILE_URL", "https://vpn.nnqnn.tech/")
        self.whitelist_cache_seconds = int(_env("WHITELIST_CACHE_SECONDS", "300"))
        self.whitelist_fetch_timeout_seconds = float(_env("WHITELIST_FETCH_TIMEOUT_SECONDS", "4"))
        self.whitelist_profile_cache_path = Path(
            _env("WHITELIST_PROFILE_CACHE_PATH", "/var/lib/tgvpn/whitelist_profile_cache.json")
        )
        self.profile = SubscriptionProfile(
            product=_env("SUBSCRIPTION_PRODUCT", "kVPN"),
            public_base_url=_env("SUBSCRIPTION_PUBLIC_BASE_URL", "https://vpn.nnqnn.tech"),
            profile_title=_env("SUBSCRIPTION_PROFILE_TITLE", "kVPN @kkVPNrobot"),
            update_interval_hours=int(_env("SUBSCRIPTION_UPDATE_INTERVAL_HOURS", "1")),
            traffic_total_bytes=int(_env("SUBSCRIPTION_TRAFFIC_TOTAL_BYTES", "0")),
            support_url=_env("SUPPORT_URL", "https://t.me/kvpn_support"),
            announce_url=_env("SUBSCRIPTION_ANNOUNCE_URL", "https://t.me/kvpn_public"),
            announce_text=_env("SUBSCRIPTION_ANNOUNCE_TEXT", "kVPN: subscription auto-updates."),
            profile_web_page_url=_env("SUBSCRIPTION_PROFILE_WEB_PAGE_URL", ""),
            vless_public_host=_env("VLESS_PUBLIC_HOST", "89.125.50.96"),
            vless_public_port=int(_env("VLESS_PUBLIC_PORT", "443")),
            vless_security=_env("VLESS_SECURITY", "reality"),
            vless_type=_env("VLESS_TYPE", "tcp"),
            vless_sni=_env("VLESS_SNI", "yandex.ru"),
            vless_flow=_env("VLESS_FLOW", "xtls-rprx-vision"),
            vless_fp=_env("VLESS_FP", "chrome"),
            vless_pbk=_env("VLESS_PBK", ""),
            vless_sid=_env("VLESS_SID", ""),
            vless_path=_env("VLESS_PATH", ""),
            vless_xhttp_mode=_env("VLESS_XHTTP_MODE", "packet-up"),
            vless_header_type=_env("VLESS_HEADER_TYPE", ""),
            vless_remark_prefix=_env("VLESS_REMARK_PREFIX", "kVPN"),
            whitelist_max_nodes=int(_env("WHITELIST_MAX_NODES", "300")),
            main_bridge_enabled=_env_bool("MAIN_VPN_BRIDGE_ENABLED", False),
            main_bridge_max_nodes=int(_env("MAIN_VPN_BRIDGE_MAX_NODES", "8")),
            fallback_vless_public_host=_env("VLESS_FALLBACK_PUBLIC_HOST", ""),
            fallback_vless_public_port=int(_env("VLESS_FALLBACK_PUBLIC_PORT", "443")),
            fallback_vless_security=_env("VLESS_FALLBACK_SECURITY", "reality"),
            fallback_vless_type=_env("VLESS_FALLBACK_TYPE", "tcp"),
            fallback_vless_sni=_env("VLESS_FALLBACK_SNI", "yandex.ru"),
            fallback_vless_flow=_env("VLESS_FALLBACK_FLOW", "xtls-rprx-vision"),
            fallback_vless_fp=_env("VLESS_FALLBACK_FP", "chrome"),
            fallback_vless_pbk=_env("VLESS_FALLBACK_PBK", ""),
            fallback_vless_sid=_env("VLESS_FALLBACK_SID", "a1b2c3d4e5f6a7b8"),
            fallback_vless_path=_env("VLESS_FALLBACK_PATH", ""),
            fallback_vless_xhttp_mode=_env("VLESS_FALLBACK_XHTTP_MODE", "packet-up"),
            legacy_vless_public_host=_env("VLESS_LEGACY_PUBLIC_HOST", ""),
            legacy_vless_public_port=int(_env("VLESS_LEGACY_PUBLIC_PORT", "8443")),
            legacy_vless_security=_env("VLESS_LEGACY_SECURITY", "reality"),
            legacy_vless_type=_env("VLESS_LEGACY_TYPE", "tcp"),
            legacy_vless_sni=_env("VLESS_LEGACY_SNI", "yandex.ru"),
            legacy_vless_flow=_env("VLESS_LEGACY_FLOW", "xtls-rprx-vision"),
            legacy_vless_fp=_env("VLESS_LEGACY_FP", "chrome"),
            legacy_vless_pbk=_env("VLESS_LEGACY_PBK", ""),
            legacy_vless_sid=_env("VLESS_LEGACY_SID", ""),
            legacy_vless_path=_env("VLESS_LEGACY_PATH", ""),
            legacy_vless_xhttp_mode=_env("VLESS_LEGACY_XHTTP_MODE", "packet-up"),
        )


class SubscriptionState:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._whitelist_text = ""
        self._whitelist_profile: dict | None = None
        self._whitelist_text_loaded_at = 0.0
        self._whitelist_profile_loaded_at = 0.0
        self._whitelist_refresh_lock = threading.Lock()

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
        if self._whitelist_text and now - self._whitelist_text_loaded_at < self.config.whitelist_cache_seconds:
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
        self._whitelist_text_loaded_at = now
        return self._whitelist_text

    def whitelist_profile(self) -> dict | None:
        now = time.monotonic()
        if self._whitelist_profile is not None and now - self._whitelist_profile_loaded_at < self.config.whitelist_cache_seconds:
            return self._whitelist_profile

        cached = self._load_whitelist_profile_cache()
        if cached is not None:
            self._whitelist_profile = cached
            self._whitelist_profile_loaded_at = now
            self._refresh_whitelist_profile_async()
            return cached

        return self._fetch_whitelist_profile()

    def _fetch_whitelist_profile(self) -> dict | None:
        try:
            response = httpx.get(
                self.config.whitelist_profile_url,
                headers={"User-Agent": "tgvpn-subscription-server"},
                timeout=self.config.whitelist_fetch_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("Cannot fetch whitelist profile")
            return self._whitelist_profile

        if not isinstance(payload, dict):
            logger.warning("Whitelist profile is not a JSON object")
            return self._whitelist_profile

        self._whitelist_profile = payload
        self._whitelist_profile_loaded_at = time.monotonic()
        self._write_whitelist_profile_cache(payload)
        return self._whitelist_profile

    def _load_whitelist_profile_cache(self) -> dict | None:
        try:
            payload = json.loads(self.config.whitelist_profile_cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            logger.warning("Whitelist profile cache is not valid JSON: %s", self.config.whitelist_profile_cache_path)
            return None
        except OSError:
            logger.exception("Cannot read whitelist profile cache")
            return None
        return payload if isinstance(payload, dict) else None

    def _write_whitelist_profile_cache(self, payload: dict) -> None:
        try:
            self.config.whitelist_profile_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.config.whitelist_profile_cache_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp_path.replace(self.config.whitelist_profile_cache_path)
        except OSError:
            logger.exception("Cannot write whitelist profile cache")

    def _refresh_whitelist_profile_async(self) -> None:
        if not self._whitelist_refresh_lock.acquire(blocking=False):
            return

        def refresh() -> None:
            try:
                self._fetch_whitelist_profile()
            finally:
                self._whitelist_refresh_lock.release()

        threading.Thread(target=refresh, name="whitelist-profile-refresh", daemon=True).start()


class SubscriptionHandler(BaseHTTPRequestHandler):
    server_version = "TGVPNSubscription/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_health()
            return

        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 3 and parts[0] == "add":
            self._send_happ_redirect(parts[1], parts[2])
            return

        if self.server.state.config.origin_secret:
            provided = self.headers.get("x-tgvpn-origin-secret", "")
            if provided != self.server.state.config.origin_secret:
                self._send_text(HTTPStatus.NOT_FOUND, "not found")
                return

        if len(parts) != 3 or parts[0] != "sub":
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return

        _, product, token = parts
        snapshot = self.server.state.load_snapshot()
        raw_user = snapshot.get("users", {}).get(token)
        if product != self.server.state.config.profile.product or not isinstance(raw_user, dict):
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return

        if not _is_raw_subscription_request(query) and _is_browser_navigation(self.headers.get("accept", "")):
            self._send_happ_redirect(product, token)
            return

        if self.server.state.config.response_format == "base64_links":
            response = build_subscription_response(
                snapshot=snapshot,
                product=product,
                token=token,
                profile=self.server.state.config.profile,
                whitelist_source_text=self.server.state.whitelist_text() if raw_user.get("whitelist_enabled") else "",
            )
        else:
            needs_whitelist_profile = bool(
                raw_user.get("whitelist_enabled")
                or (
                    raw_user.get("main_vpn_active")
                    and self.server.state.config.profile.main_bridge_enabled
                )
            )
            response = build_xray_json_subscription_response(
                snapshot=snapshot,
                product=product,
                token=token,
                profile=self.server.state.config.profile,
                whitelist_profile=self.server.state.whitelist_profile() if needs_whitelist_profile else None,
            )
        if response is None:
            self._send_text(HTTPStatus.NOT_FOUND, "not found")
            return

        body = response.body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        message = format % args
        logger.info("%s - %s", self.address_string(), self._redact_tokens(message))

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_health(self) -> None:
        snapshot = self.server.state.load_snapshot()
        users = snapshot.get("users", {})
        payload = {
            "ok": True,
            "snapshot_version": snapshot.get("version"),
            "generated_at": snapshot.get("generated_at"),
            "users": len(users) if isinstance(users, dict) else 0,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_happ_redirect(self, product: str, token: str) -> None:
        https_url = _raw_subscription_url(self.server.state.config.profile.public_base_url, product, token)
        happ_url = build_happ_link(https_url)
        html = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="0; url={_escape_html(happ_url)}">'
            "<title>Open Happ</title></head><body>"
            f'<a href="{_escape_html(happ_url)}">Open Happ</a>'
            "</body></html>"
        )
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _raw_subscription_url(base_url: str, product: str, token: str) -> str:
    return f"{build_subscription_url(base_url, product, token)}?format=raw"


def _is_raw_subscription_request(query: dict[str, list[str]]) -> bool:
    values = query.get("format", []) + query.get("raw", [])
    return any(str(value).strip().lower() in {"1", "true", "yes", "raw", "json"} for value in values)


def _is_browser_navigation(accept_header: str) -> bool:
    accept = accept_header.lower()
    return "text/html" in accept


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


if __name__ == "__main__":
    main()
