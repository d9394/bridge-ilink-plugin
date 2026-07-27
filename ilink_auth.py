from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp


class TokenData:
    def __init__(
        self,
        token: str = "",
        account_id: str = "",
        user_id: str = "",
        base_url: str = "",
        saved_at: float = 0,
    ):
        self.token = token
        self.account_id = account_id
        self.user_id = user_id
        self.base_url = base_url
        self.saved_at = saved_at


def _token_path(storage_dir: str) -> Path:
    return Path(storage_dir) / "token.json"


def load_token(storage_dir: str) -> Optional[TokenData]:
    tp = _token_path(storage_dir)
    if not tp.exists():
        return None
    try:
        data = json.loads(tp.read_text(encoding="utf-8"))
        return TokenData(
            token=data.get("token", ""),
            account_id=data.get("account_id", ""),
            user_id=data.get("user_id", ""),
            base_url=data.get("base_url", ""),
            saved_at=data.get("saved_at", 0),
        )
    except Exception:
        return None


def save_token(storage_dir: str, token_data: TokenData):
    os.makedirs(storage_dir, exist_ok=True)
    tp = _token_path(storage_dir)
    tp.write_text(
        json.dumps(
            {
                "token": token_data.token,
                "account_id": token_data.account_id,
                "user_id": token_data.user_id,
                "base_url": token_data.base_url,
                "saved_at": token_data.saved_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


async def _get_qr_data(session: aiohttp.ClientSession, qr_url: str, log) -> tuple[str, str]:
    async with session.get(qr_url) as resp:
        text = await resp.text()
        try:
            data = json.loads(text)
            return data.get("qrcode", ""), data.get("qrcode_img_content", "")
        except Exception:
            log(f"QR parse error: {text[:200]}")
            return "", ""


def _display_qr(qrcode_img: str, log):
    log("Scan this QR code with WeChat:")
    try:
        with open("/tmp/last_qr_url.txt", "w") as f:
            f.write(qrcode_img)
    except Exception:
        pass
    try:
        import qrcode
        import io
        qr = qrcode.QRCode(version=1, box_size=1, border=0)
        qr.add_data(qrcode_img)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        for line in buf.getvalue().splitlines():
            log(line)
    except Exception:
        log(f"QR URL: {qrcode_img}")


async def login(
    base_url: str,
    bot_type: str,
    storage_dir: str,
    log_fn: Callable[[str], None] = None,
) -> TokenData:
    log = log_fn or (lambda msg: print(f"[ilink-auth] {msg}"))

    async with aiohttp.ClientSession() as session:
        log("Requesting QR code...")
        qr_url = f"{base_url}/ilink/bot/get_bot_qrcode?bot_type={bot_type}"
        qrcode, qrcode_img = await _get_qr_data(session, qr_url, log)

        if not qrcode:
            raise RuntimeError("No QR code returned")

        _display_qr(qrcode_img, log)

        deadline = time.time() + 5 * 60
        current_qrcode = qrcode
        refresh_count = 0

        while time.time() < deadline:
            await asyncio.sleep(1.5)

            status_url = f"{base_url}/ilink/bot/get_qrcode_status?qrcode={current_qrcode}"
            async with session.get(status_url) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    log(f"Status parse error: {text[:200]}")
                    continue
                status = data.get("status", "")

                if status == "wait":
                    continue
                elif status == "scaned":
                    log("QR scanned, please confirm in WeChat...")
                    continue
                elif status == "expired":
                    refresh_count += 1
                    if refresh_count > 3:
                        raise RuntimeError("QR code expired multiple times, please retry")
                    log(f"QR expired, refreshing ({refresh_count}/3)...")
                    current_qrcode, qrcode_img = await _get_qr_data(session, qr_url, log)
                    if current_qrcode:
                        _display_qr(qrcode_img, log)
                    continue
                elif status == "confirmed":
                    log("Login successful!")
                    token_data = TokenData(
                        token=data.get("bot_token", ""),
                        account_id=data.get("ilink_bot_id", ""),
                        user_id=data.get("ilink_user_id", ""),
                        base_url=data.get("baseurl", "") or base_url,
                        saved_at=time.time(),
                    )
                    save_token(storage_dir, token_data)
                    log(f"Bot ID: {token_data.account_id}")
                    log(f"Token saved to {_token_path(storage_dir)}")
                    return token_data
                else:
                    log(f"Unknown status: {status}")

        raise RuntimeError("Login timeout (5 minutes)")
