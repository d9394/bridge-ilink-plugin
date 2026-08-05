from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import yaml
import aiohttp

from plugins.ilink_plugin.client import ILinkClient
from plugins.ilink_plugin.send import ILinkSender
from plugins.ilink_plugin.reminder import WeChatReminderScheduler

_DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "config.yaml")
CONFIG_FILE = (
    os.environ.get("ILINK_CONFIG")
    or ("/app/ilink_config.yaml" if os.path.exists("/app/ilink_config.yaml") else None)
    or _DEFAULT_CONFIG
)


def load_plugin_config(path: str = CONFIG_FILE) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_logging(cfg: dict, app_id: str) -> logging.Logger:
    log_cfg = cfg.get("log", {})
    log_file = log_cfg.get("file", "ilink.log")
    log_level = log_cfg.get("level", "INFO")
    max_size_mb = log_cfg.get("max_size_mb", 10)
    backup_count = log_cfg.get("backup_count", 5)

    log_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", ".ilink-bridge", "logs"
    )
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(f"ilink-{app_id}")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, log_file),
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

        console = logging.StreamHandler()
        console.setFormatter(fmt)
        logger.addHandler(console)

    return logger


async def main():
    cfg = load_plugin_config()
    app_id = cfg.get("app_id", "ilink_main")

    logger = setup_logging(cfg, app_id)

    if not cfg.get("enabled", True):
        logger.info("Disabled by config")
        return

    bridge_url = cfg["bridge_url"].rstrip("/")
    mode = cfg.get("mode", "upstream")
    ws_url = f"{bridge_url}/ws/{mode}"
    app_secret = cfg["app_secret"]

    logger.info("Starting (app_id=%s, ws=%s)", app_id, ws_url)

    reminder_config = cfg.get("wechat_reminder", {})
    data_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", ".ilink-bridge", "data"
    )
    os.makedirs(data_dir, exist_ok=True)
    data_file = os.path.join(data_dir, f"{app_id}_reminders.json")

    async def send_reminder(user_id, message, context_token):
        if ilink_sender is None:
            logger.warning("Cannot send reminder: iLink sender not initialized")
            return
        try:
            logger.info("Sending reminder to %s: %.60s", user_id, message)
            ok = await ilink_sender.send_text(user_id, message, context_token)
            logger.info("Send reminder to %s result: %s", user_id, ok)
        except Exception as e:
            logger.error("Send reminder error: %s", e)

    reminder_scheduler = WeChatReminderScheduler(
        data_file=data_file,
        reminder_hours=reminder_config.get("reminder_hours", [22, 23]),
        reminder_message=reminder_config.get("reminder_message", "⏰ 此对话已超过{hours}小时未活跃，请发送消息以维持通道。"),
        check_interval=reminder_config.get("check_interval", 60),
        on_send_reminder=send_reminder,
        log_fn=lambda m: logger.info("[reminder] %s", m),
    )

    token_data = None
    ilink_sender = None
    ilink_client = None
    running = True

    while running:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    logger.info("Connected to %s", ws_url)

                    await ws.send_json({
                        "type": "connect",
                        "app_id": app_id,
                        "app_secret": app_secret,
                    })

                    resp = await ws.receive(timeout=10)
                    if resp.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(resp.data)
                        if data.get("type") == "connect.error":
                            logger.warning("Auth failed: %s", data)
                            await asyncio.sleep(10)
                            continue
                        elif data.get("type") == "connect.ok":
                            logger.info("Authenticated: session=%s", data.get("session_id"))
                        else:
                            logger.warning("Unexpected response: %s", data)
                            continue
                    else:
                        logger.warning("WS error: %s", resp)
                        continue

                    auth_dir = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", ".ilink-bridge", "auth"
                    )
                    from plugins.ilink_plugin.ilink_auth import load_token, TokenData

                    if ilink_sender is None:
                        token_data = load_token(auth_dir)
                        if token_data is None:
                            from plugins.ilink_plugin.ilink_auth import login
                            logger.info("No saved token, starting QR login...")
                            bot_type = os.environ.get("ILINK_BOT_TYPE", "3")
                            base_url = os.environ.get("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")
                            token_data = await login(
                                base_url=base_url,
                                bot_type=bot_type,
                                storage_dir=auth_dir,
                                log_fn=lambda m: logger.info("[auth] %s", m),
                            )

                        cdn_base_url = os.environ.get(
                            "ILINK_CDN_URL",
                            "https://novac2c.cdn.weixin.qq.com/c2c",
                        )
                        ilink_sender = ILinkSender(
                            base_url=token_data.base_url or "https://ilinkai.weixin.qq.com",
                            cdn_base_url=cdn_base_url,
                            token=token_data.token,
                            logger=logger,
                        )

                    if ilink_client is None:
                        ilink_client = ILinkClient(
                            base_url=token_data.base_url or "https://ilinkai.weixin.qq.com",
                            token=token_data.token,
                            poll_timeout=38,
                            log_fn=lambda m: logger.info("[client] %s", m),
                            on_message=lambda msg: _on_ilink_message(ws, msg, logger, reminder_scheduler),
                        )

                    if not reminder_scheduler._running:
                        await reminder_scheduler.start()

                    poll_task = asyncio.create_task(ilink_client.start())

                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                msg_type = data.get("type", "")
                                if msg_type == "ping":
                                    await ws.send_json({"type": "pong", "request_id": data.get("request_id", "")})
                                elif msg_type == "pong":
                                    pass
                                elif msg_type == "message.send":
                                    asyncio.create_task(_handle_send(data, ilink_sender, logger))
                                elif msg_type == "file.send":
                                    asyncio.create_task(_handle_file_send(data, ilink_sender, logger))
                                elif msg_type == "message.typing":
                                    await _handle_typing(data, ilink_sender, logger)
                                else:
                                    logger.info("Unknown bridge msg: %s", msg_type)
                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break
                    except Exception as e:
                        logger.error("WS loop error: %s", e)
                    finally:
                        poll_task.cancel()
                        try:
                            await poll_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass

                    if ilink_client:
                        await ilink_client.stop()
                    ilink_client = None
                    ilink_sender = None
                    token_data = None

        except aiohttp.ClientError as e:
            logger.error("Connection error: %s", e)
        except asyncio.CancelledError:
            running = False
            break
        except Exception as e:
            logger.error("Unexpected error: %s", e)

        if running:
            logger.info("Reconnecting in 5s...")
            await asyncio.sleep(5)


async def _on_ilink_message(ws: aiohttp.ClientWebSocketResponse, msg, logger: logging.Logger, reminder_scheduler: WeChatReminderScheduler) -> None:
    if ws.closed:
        return
    logger.info("iLink msg: id=%s from=%s content=%s ctx=%s type=%s group=%s",
                 msg.message_id, msg.from_user_id, msg.text_content[:100],
                 msg.context_token, msg.message_type, msg.group_id)
    
    await reminder_scheduler.record_user_message(msg.from_user_id, msg.context_token)
    
    payload = {
        "type": "message.received",
        "request_id": f"ilink_{msg.message_id}",
        "from": msg.from_user_id,
        "sender_name": "",
        "content": msg.text_content,
        "message_type": "text",
        "context_token": msg.context_token,
    }
    if msg.group_id:
        payload["group_id"] = msg.group_id
    try:
        await ws.send_json(payload)
    except Exception as e:
        logger.error("Send to bridge error: %s", e)


async def _handle_send(data: dict, sender: ILinkSender, logger: logging.Logger) -> None:
    to_user = data.get("to", "")
    content = data.get("content", "")
    context_token = data.get("context_token", "")
    if to_user and content:
        logger.info("Sending text to %s: %.60s", to_user, content)
        ok = await sender.send_text(to_user, content, context_token)
        logger.info("Send to %s result: %s", to_user, ok)


async def _handle_file_send(data: dict, sender: ILinkSender, logger: logging.Logger) -> None:
    to_user = data.get("to", "")
    file_data_b64 = data.get("file_data", "")
    file_name = data.get("file_name", "file")
    context_token = data.get("context_token", "")
    if to_user and file_data_b64:
        import base64
        file_bytes = base64.b64decode(file_data_b64)
        logger.info("Sending file to %s: %s (%d bytes)", to_user, file_name, len(file_bytes))
        await sender.send_file(to_user, file_bytes, file_name, context_token)


async def _handle_typing(data: dict, sender: ILinkSender, logger: logging.Logger) -> None:
    to_user = data.get("to", "")
    if to_user:
        await sender.send_typing(to_user)


if __name__ == "__main__":
    asyncio.run(main())
