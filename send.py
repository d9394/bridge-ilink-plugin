from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import uuid
from typing import Any, Optional

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding


def _random_wechat_uin() -> str:
    return base64.b64encode(str(random.getrandbits(32)).encode()).decode()

def _encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()

def _generate_filekey() -> str:
    return os.urandom(16).hex()

def _hex_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class ILinkSender:
    def __init__(self, base_url: str, cdn_base_url: str, token: str, logger=None):
        self.base_url = base_url
        self.cdn_base_url = cdn_base_url
        self.token = token
        self.logger = logger

    def _headers(self, body: str = "") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "AuthorizationType": "ilink_bot_token",
            "Content-Type": "application/json",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        if body:
            headers["Content-Length"] = str(len(body.encode("utf-8")))
        return headers

    def _base_info(self) -> dict:
        return {"channel_version": "1.0.2"}

    def _build_msg(
        self, user_id: str, item_list: list[dict], context_token: str = ""
    ) -> dict:
        msg: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": user_id,
            "client_id": str(uuid.uuid4()),
            "message_type": 2,
            "message_state": 2,
            "item_list": item_list,
        }
        if context_token:
            msg["context_token"] = context_token
        return msg

    async def send_text(
        self, user_id: str, text: str, context_token: str = ""
    ) -> bool:
        item_list = [{"type": 1, "text_item": {"text": text}}]
        payload: dict[str, Any] = {
            "msg": self._build_msg(user_id, item_list, context_token),
            "base_info": self._base_info(),
        }
        return await self._send_message(payload)

    async def send_image(
        self,
        user_id: str,
        image_data: bytes,
        filename: str = "image.jpg",
        context_token: str = "",
    ) -> bool:
        return await self._send_media(
            to_user=user_id,
            media_type=1,
            buffer=image_data,
            file_name=filename,
            context_token=context_token,
        )

    async def send_file(
        self,
        user_id: str,
        file_data: bytes,
        filename: str = "file",
        context_token: str = "",
    ) -> bool:
        return await self._send_media(
            to_user=user_id,
            media_type=3,
            buffer=file_data,
            file_name=filename,
            context_token=context_token,
        )

    async def send_typing(self, user_id: str) -> bool:
        payload = {
            "to_user_id": user_id,
            "command": "typing",
            "base_info": self._base_info(),
        }
        return await self._send_command(payload)

    async def _send_media(
        self,
        to_user: str,
        media_type: int,
        buffer: bytes,
        file_name: str = "file",
        context_token: str = "",
    ) -> bool:
        try:
            filekey = _generate_filekey()
            raw_size = len(buffer)
            raw_md5 = _hex_md5(buffer)
            aes_key = os.urandom(16)
            aes_key_hex = aes_key.hex()
            encrypted_size = ((raw_size + 15) // 16) * 16

            payload = {
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": encrypted_size,
                "aeskey": aes_key_hex,
                "no_need_thumb": True,
                "base_info": self._base_info(),
            }

            body_str = json.dumps(payload)
            if self.logger:
                self.logger.info("[send] getuploadurl filekey=%s rawsize=%s media_type=%s", filekey, raw_size, media_type)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/ilink/bot/getuploadurl",
                    headers=self._headers(body_str),
                    data=body_str,
                ) as resp:
                    text = await resp.text(encoding="utf-8")
                    result = json.loads(text)
                    if self.logger:
                        self.logger.info("[send] getuploadurl result=%s", text[:500])
                    if result.get("ret") is not None and result.get("ret") != 0:
                        if self.logger:
                            self.logger.error("[send] getuploadurl ret=%s errmsg=%s", result.get("ret"), result.get("errmsg", ""))
                        return False

                    upload_param = result.get("upload_param", "")
                    upload_full_url = result.get("upload_full_url", "")

                    if not upload_param:
                        if self.logger:
                            self.logger.error("[send] getuploadurl: missing upload_param")
                        return False

                if upload_full_url:
                    upload_url = upload_full_url
                else:
                    upload_url = f"{self.cdn_base_url}/upload?encrypted_query_param={upload_param}&filekey={filekey}"

                encrypt_query_param = await self._upload_to_cdn(
                    buffer=buffer,
                    upload_param=upload_param,
                    aes_key=aes_key,
                    filekey=filekey,
                    upload_url=upload_url,
                )

                if not encrypt_query_param:
                    return False

                aes_key_b64 = base64.b64encode(aes_key_hex.encode()).decode()

                cdn_media = {
                    "encrypt_query_param": encrypt_query_param,
                    "aes_key": aes_key_b64,
                    "encrypt_type": 1,
                }

                if media_type == 1:
                    item_list = [{
                        "type": 2,
                        "image_item": {
                            "media": cdn_media,
                            "aeskey": aes_key_b64,
                            "url": encrypt_query_param,
                            "mid_size": encrypted_size,
                        },
                    }]
                elif media_type == 3:
                    item_list = [{
                        "type": 4,
                        "file_item": {
                            "media": cdn_media,
                            "file_name": file_name,
                            "len": str(raw_size),
                        },
                    }]
                else:
                    if self.logger:
                        self.logger.error("[send] unsupported media_type: %s", media_type)
                    return False

                payload = {
                    "msg": self._build_msg(to_user, item_list, context_token),
                    "base_info": self._base_info(),
                }
                return await self._send_message(payload)

        except Exception as e:
            if self.logger:
                self.logger.error("[send] _send_media exception: %s", e, exc_info=True)
            return False

    async def _upload_to_cdn(
        self,
        buffer: bytes,
        upload_param: str,
        aes_key: bytes,
        filekey: str,
        upload_url: str,
        max_retries: int = 3,
    ) -> Optional[str]:
        encrypted = _encrypt_aes_ecb(buffer, aes_key)
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        upload_url,
                        headers={"Content-Type": "application/octet-stream"},
                        data=encrypted,
                    ) as resp:
                        if 400 <= resp.status < 500:
                            err_msg = resp.headers.get("x-error-message", await resp.text())
                            if self.logger:
                                self.logger.error("[cdn] client error %s: %s", resp.status, err_msg)
                            raise RuntimeError(f"CDN upload client error {resp.status}: {err_msg}")
                        if resp.status != 200:
                            err_msg = resp.headers.get("x-error-message", f"status {resp.status}")
                            if self.logger:
                                self.logger.error("[cdn] server error: %s", err_msg)
                            raise RuntimeError(f"CDN upload server error: {err_msg}")

                        download_param = resp.headers.get("x-encrypted-param")
                        if not download_param:
                            body = await resp.text()
                            if self.logger:
                                self.logger.error("[cdn] missing x-encrypted-param. Body: %s", body[:200])
                            raise RuntimeError(f"CDN upload: missing x-encrypted-param. Body: {body[:200]}")
                        if self.logger:
                            self.logger.info("[cdn] CDN upload success, got x-encrypted-param")
                        return download_param
            except RuntimeError as e:
                last_error = e
                if "client error" in str(e):
                    raise
                if attempt < max_retries:
                    if self.logger:
                        self.logger.warning("[cdn] upload attempt %s failed: %s, retrying...", attempt, e)
                    continue
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    if self.logger:
                        self.logger.warning("[cdn] upload attempt %s exception: %s, retrying...", attempt, e)
                    continue

        if self.logger:
            self.logger.error("[cdn] CDN upload failed after %s attempts", max_retries)
        return None

    async def _send_message(self, payload: dict) -> bool:
        try:
            body_str = json.dumps(payload)
            if self.logger:
                self.logger.info("[send] POST /ilink/bot/sendmessage payload=%s", body_str[:500])
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/ilink/bot/sendmessage",
                    headers=self._headers(body_str),
                    data=body_str,
                ) as resp:
                    text = await resp.text(encoding="utf-8")
                    data = json.loads(text)
                    ret = data.get("ret", -1)
                    ok = ret == 0 or ret == -1
                    if self.logger:
                        self.logger.info("[send] sendmessage ret=%s ok=%s data=%s", ret, ok, text[:300])
                    return ok
        except Exception as e:
            if self.logger:
                self.logger.error("[send] sendmessage error: %s", e)
            return False

    async def _send_command(self, payload: dict) -> bool:
        try:
            body_str = json.dumps(payload)
            if self.logger:
                self.logger.info("[send] POST /ilink/bot/sendtyping payload=%s", body_str[:200])
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/ilink/bot/sendtyping",
                    headers=self._headers(body_str),
                    data=body_str,
                ) as resp:
                    text = await resp.text(encoding="utf-8")
                    data = json.loads(text)
                    if self.logger:
                        self.logger.info("[send] sendtyping ret=%s", text[:200])
                    return data.get("ret", -1) == 0
        except Exception as e:
            if self.logger:
                self.logger.error("[send] sendtyping error: %s", e)
            return False
