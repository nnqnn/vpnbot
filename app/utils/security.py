from __future__ import annotations

import secrets
import string


def generate_referral_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_payment_label(prefix: str = "VPN") -> str:
    token = secrets.token_hex(8)
    return f"{prefix}-{token}"
