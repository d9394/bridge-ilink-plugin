from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable, Awaitable, Optional


class WeChatReminderScheduler:
    def __init__(
        self,
        data_file: str = "wechat_reminders.json",
        on_send_reminder: Callable[[str, str], Awaitable[None]] = None,
        reminder_hours: list[int] = [22, 23],
        reminder_message: str = "⏰ 此对话已超过{hours}小时未活跃，请发送消息以维持通道。",
        check_interval: int = 60,
        log_fn: Callable[[str], None] = None,
    ):
        self.data_file = data_file
        self.on_send_reminder = on_send_reminder
        self.reminder_hours = sorted(reminder_hours)
        self.reminder_message = reminder_message
        self.check_interval = check_interval
        self.log = log_fn or (lambda msg: print(f"[reminder] {msg}"))
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._reminders: dict[str, dict] = {}
        self._load_data()

    def _load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self._reminders = json.load(f)
                self.log(f"Loaded {len(self._reminders)} reminders from {self.data_file}")
            except Exception as e:
                self.log(f"Failed to load reminders: {e}")
                self._reminders = {}

    def _save_data(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._reminders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Failed to save reminders: {e}")

    async def start(self):
        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())
        self.log("WeChat reminder scheduler started")

    async def stop(self):
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        self.log("WeChat reminder scheduler stopped")

    async def record_user_message(self, user_id: str, context_token: str = ""):
        now = time.time()
        if user_id not in self._reminders:
            self._reminders[user_id] = {
                "last_user_message_at": now,
                "sent_reminders": [],
                "context_token": context_token,
            }
        else:
            self._reminders[user_id]["last_user_message_at"] = now
            self._reminders[user_id]["sent_reminders"] = []
            if context_token:
                self._reminders[user_id]["context_token"] = context_token
        self._save_data()

    async def _check_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                await self._check_reminders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log(f"Error in reminder check loop: {e}")

    async def _check_reminders(self):
        now = time.time()
        changed = False
        
        for user_id, reminder in list(self._reminders.items()):
            last_user_message_at = reminder["last_user_message_at"]
            sent_reminders = reminder.get("sent_reminders", [])
            context_token = reminder.get("context_token", "")
            
            hours_since_last = (now - last_user_message_at) / 3600
            
            for hours in self.reminder_hours:
                if hours_since_last >= hours and hours not in sent_reminders:
                    message = self.reminder_message.replace("{hours}", str(hours))
                    await self._send_reminder(user_id, message, context_token)
                    cur = self._reminders.get(user_id)
                    if cur and cur["last_user_message_at"] == last_user_message_at \
                            and hours not in cur["sent_reminders"]:
                        cur["sent_reminders"].append(hours)
                        changed = True
        
        if changed:
            self._save_data()

    async def _send_reminder(self, user_id: str, message: str, context_token: str):
        if self.on_send_reminder:
            try:
                await self.on_send_reminder(user_id, message, context_token)
                self.log(f"Sent reminder to {user_id}")
            except Exception as e:
                self.log(f"Failed to send reminder to {user_id}: {e}")