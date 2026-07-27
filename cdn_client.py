from __future__ import annotations

import hashlib
import math
import os
import tempfile
from typing import Optional

import aiohttp

from utils.aes_utils import encrypt_aes_ecb, decrypt_aes_ecb, parse_aes_key


async def download_and_decrypt(
    encrypt_query_param: str,
    aes_key: bytes,
    cdn_base_url: str,
    filekey: Optional[str] = None,
) -> bytes:
    url = f"{cdn_base_url}/download?encrypted_query_param={encrypt_query_param}"
    if filekey:
        url += f"&filekey={filekey}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if not resp.ok:
                raise RuntimeError(f"CDN download failed: HTTP {resp.status}")
            ciphertext = await resp.read()

    return decrypt_aes_ecb(ciphertext, aes_key)


async def get_upload_url(
    base_url: str,
    token: str,
    filekey: str,
    media_type: int,
    to_user_id: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey: str,
    no_need_thumb: bool = True,
) -> dict:
    import base64
    import random

    def _random_uin() -> str:
        return base64.b64encode(str(random.getrandbits(32)).encode()).decode()

    payload = {
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "no_need_thumb": no_need_thumb,
        "aeskey": aeskey,
        "base_info": {"channel_version": "2.0.0"},
    }

    import json
    body_str = json.dumps(payload)
    headers = {
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
        "Content-Type": "application/json",
        "X-WECHAT-UIN": _random_uin(),
        "Content-Length": str(len(body_str.encode("utf-8"))),
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base_url}/ilink/bot/getuploadurl",
            headers=headers,
            data=body_str,
        ) as resp:
            text = await resp.text()
            import json as _json
            return _json.loads(text)


async def upload_to_cdn(
    buffer: bytes,
    upload_param: str,
    aes_key: bytes,
    filekey: str,
    cdn_base_url: str,
    upload_url: Optional[str] = None,
    max_retries: int = 3,
) -> str:
    encrypted = encrypt_aes_ecb(buffer, aes_key)
    url = upload_url or (
        f"{cdn_base_url}/upload"
        f"?encrypted_query_param={upload_param}"
        f"&filekey={filekey}"
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"Content-Type": "application/octet-stream"},
                    data=encrypted,
                ) as resp:
                    if 400 <= resp.status < 500:
                        err_msg = resp.headers.get("x-error-message", await resp.text())
                        raise RuntimeError(f"CDN upload client error {resp.status}: {err_msg}")
                    if resp.status != 200:
                        err_msg = resp.headers.get("x-error-message", f"status {resp.status}")
                        raise RuntimeError(f"CDN upload server error: {err_msg}")

                    download_param = resp.headers.get("x-encrypted-param")
                    if not download_param:
                        body = await resp.text()
                        raise RuntimeError(f"CDN upload: missing x-encrypted-param. Body: {body}")
                    return download_param
        except RuntimeError as e:
            last_error = e
            if "client error" in str(e):
                raise
            if attempt < max_retries:
                continue

    raise last_error or RuntimeError(f"CDN upload failed after {max_retries} attempts")


async def download_media(
    media: dict,
    cdn_base_url: str,
) -> bytes:
    aes_key = parse_aes_key(media)
    if not aes_key:
        raise RuntimeError("No AES key in media reference")

    encrypt_query = media.get("encrypt_query_param", "")
    if not encrypt_query:
        raise RuntimeError("No encrypt_query_param in media reference")

    return await download_and_decrypt(encrypt_query, aes_key, cdn_base_url)


def save_to_temp(buffer: bytes, filename: str) -> str:
    temp_dir = os.path.join(tempfile.gettempdir(), "ilink-bridge-media")
    os.makedirs(temp_dir, exist_ok=True)

    import time
    import random
    safe_name = f"{int(time.time())}-{random.randint(100000, 999999)}-{filename}"
    filepath = os.path.join(temp_dir, safe_name)
    with open(filepath, "wb") as f:
        f.write(buffer)
    return filepath


def is_text_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in {
        "txt", "md", "json", "js", "ts", "py", "java", "c", "cpp", "h",
        "css", "html", "xml", "yaml", "yml", "toml", "ini", "cfg", "sh",
        "bash", "rs", "go", "rb", "php", "sql", "csv", "log", "env",
    }
