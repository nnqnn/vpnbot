from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    super_admin_id: int = Field(alias="SUPER_ADMIN_ID")

    database_url: str = Field(alias="DATABASE_URL")
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")

    month_price_rub: int = Field(default=100, alias="MONTH_PRICE_RUB")
    trial_days: int = Field(default=1, alias="TRIAL_DAYS")
    referral_bonus_days: int = Field(default=2, alias="REFERRAL_BONUS_DAYS")
    max_devices: int = Field(default=4, alias="MAX_DEVICES")

    telegapay_base_url: str = Field(alias="TELEGAPAY_BASE_URL")
    telegapay_api_key: str = Field(alias="TELEGAPAY_API_KEY")
    telegapay_return_url: str = Field(default="", alias="TELEGAPAY_RETURN_URL")
    payment_min_amount: int = Field(default=100, alias="PAYMENT_MIN_AMOUNT")
    payment_ttl_minutes: int = Field(default=60, alias="PAYMENT_TTL_MINUTES")

    vless_public_host: str = Field(alias="VLESS_PUBLIC_HOST")
    vless_public_port: int = Field(default=443, alias="VLESS_PUBLIC_PORT")
    vless_security: str = Field(default="reality", alias="VLESS_SECURITY")
    vless_type: str = Field(default="tcp", alias="VLESS_TYPE")
    vless_sni: str = Field(default="", alias="VLESS_SNI")
    vless_flow: str = Field(default="", alias="VLESS_FLOW")
    vless_fp: str = Field(default="", alias="VLESS_FP")
    vless_pbk: str = Field(default="", alias="VLESS_PBK")
    vless_sid: str = Field(default="", alias="VLESS_SID")
    vless_path: str = Field(default="", alias="VLESS_PATH")
    vless_header_type: str = Field(default="", alias="VLESS_HEADER_TYPE")
    vless_remark_prefix: str = Field(default="VPN", alias="VLESS_REMARK_PREFIX")

    xray_control_mode: str = Field(default="config", alias="XRAY_CONTROL_MODE")
    xray_config_path: Path = Field(default=Path("/usr/local/etc/xray/config.json"), alias="XRAY_CONFIG_PATH")
    xray_inbound_tag: str = Field(default="vless-in", alias="XRAY_INBOUND_TAG")
    xray_reload_command: str = Field(default="systemctl reload xray", alias="XRAY_RELOAD_COMMAND")
    xray_restart_command: str = Field(default="systemctl restart xray", alias="XRAY_RESTART_COMMAND")
    xray_access_log_path: Path = Field(default=Path("/var/log/xray/access.log"), alias="XRAY_ACCESS_LOG_PATH")
    xray_api_enabled: bool = Field(default=False, alias="XRAY_API_ENABLED")
    xray_api_server: str = Field(default="127.0.0.1:10085", alias="XRAY_API_SERVER")
    xray_api_timeout_seconds: int = Field(default=5, alias="XRAY_API_TIMEOUT_SECONDS")
    xray_bin_path: str = Field(default="xray", alias="XRAY_BIN_PATH")

    auto_renew_interval_minutes: int = Field(default=30, alias="AUTO_RENEW_INTERVAL_MINUTES")
    payment_poll_interval_seconds: int = Field(default=60, alias="PAYMENT_POLL_INTERVAL_SECONDS")
    xray_sync_interval_minutes: int = Field(default=5, alias="XRAY_SYNC_INTERVAL_MINUTES")
    device_limit_interval_minutes: int = Field(default=10, alias="DEVICE_LIMIT_INTERVAL_MINUTES")
    notify_interval_minutes: int = Field(default=30, alias="NOTIFY_INTERVAL_MINUTES")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: Path = Field(default=Path("./logs"), alias="LOG_DIR")
    required_channel: str = Field(default="@kvpnpublic", alias="REQUIRED_CHANNEL")
    required_channel_url: str = Field(default="https://t.me/kvpnpublic", alias="REQUIRED_CHANNEL_URL")
    support_url: str = Field(default="https://t.me/kamilhateu", alias="SUPPORT_URL")
    rules_url: str = Field(
        default="https://telegra.ph/Pravila-servisa-kVPN-i-politika-konfidencialnosti-04-05",
        alias="RULES_URL",
    )

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id == self.super_admin_id


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
