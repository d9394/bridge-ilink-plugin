from __future__ import annotations

import asyncio
import base64
import json
import random
from typing import Any, Callable, Awaitable, Optional

import aiohttp

from core.models import ILinkMessage


def _random_wechat_uin() -> str:
    return base64.b64encode(str(random.getrandbits(32)).encode()).decode()


class ILinkClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        poll_timeout: int = 38,
        log_fn: Callable[[str], None] = None,
        on_message: Callable[[ILinkMessage], Awaitable[None]] = None,
    ):
        self.base_url = base_url
        self.token = token
        self.poll_timeout = poll_timeout
        self.log = log_fn or (lambda msg: print(f"[ilink-client] {msg}"))
        self.on_message = on_message
        self._running = False
        self._get_updates_buf = ""

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

    async def start(self):
        self._running = True
        self.log("Starting long-poll message loop...")
        retry_count = 0
        while self._running:
            try:
                await self._poll_once()
                retry_count = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                retry_count += 1
                self.log(f"Poll error (attempt {retry_count}): {e}")
                if retry_count >= 3:
                    self.log("Too many consecutive errors, waiting 30s...")
                    await asyncio.sleep(30)
                    retry_count = 0

    async def stop(self):
        self._running = False

    async def _poll_once(self):
        payload = {"get_updates_buf": self._get_updates_buf, "timeout": self.poll_timeout}
        body_str = json.dumps(payload)
        headers = self._headers(body_str)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/ilink/bot/getupdates",
                    headers=headers,
                    data=body_str,
                    timeout=aiohttp.ClientTimeout(total=self.poll_timeout),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        self.log(f"HTTP error {resp.status}: {text[:200]}")
                        return

                    text = await resp.text(encoding="utf-8")
                    data = json.loads(text)
                    ret = data.get("ret", -1)
                    if ret != 0:
                        self.log(f"Poll returned ret={ret}")

                    new_buf = data.get("get_updates_buf", "")
                    if new_buf:
                        self._get_updates_buf = new_buf

                    msgs = data.get("msgs", [])
                    if msgs:
                        self.log(f"Received {len(msgs)} message(s)")
                    for raw_msg in msgs:
                        msg = self._parse_message(raw_msg)
                        if msg and self.on_message:
                            await self.on_message(msg)

        except asyncio.TimeoutError:
            pass
        except aiohttp.ClientError as e:
            self.log(f"HTTP error during poll: {e}")

    def _parse_message(self, raw: dict) -> Optional[ILinkMessage]:
        try:
            content = ""
            item_list = raw.get("item_list", [])
            for item in item_list:
                item_type = item.get("type")
                if item_type == 1:
                    text_item = item.get("text_item", {})
                    content = text_item.get("text", "")
                    break
                elif item_type == 2:
                    image_item = item.get("image_item", {})
                    content = image_item.get("text", "")
                    break
                elif item_type == 3:
                    file_item = item.get("file_item", {})
                    content = file_item.get("name", "")
                    break
            if not content:
                content = raw.get("content", "")
            return ILinkMessage(
                message_id=str(raw.get("message_id", raw.get("msg_id", ""))),
                from_user_id=raw.get("from_user_id", ""),
                context_token=raw.get("context_token", ""),
                message_type=raw.get("message_type", 1),
                text_content=content,
                group_id=raw.get("group_id", ""),
                raw=raw,
            )
        except Exception:
            return None
