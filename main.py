
import asyncio
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@dataclass
class _TokenBucket:
    capacity: float
    refill_period_seconds: float
    tokens: float
    last_refill_ts: float

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self.last_refill_ts
        if elapsed <= 0:
            return
        refill_rate = self.capacity / self.refill_period_seconds
        self.tokens = min(self.capacity, self.tokens + elapsed * refill_rate)
        self.last_refill_ts = now

    def acquire(self, n: float = 1.0) -> bool:
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


@register(
    "chat",
    "cascade",
    "基础反应, 群空调",
    "1.0.0",
    ""
)
class ChatPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.context = context

        self._poke_buckets: Dict[int, _TokenBucket] = {}
        self._aircon_temp: Dict[int, int] = {}
        self._aircon_switch: Dict[int, bool] = {}

    def _get_bucket(self, gid: int) -> _TokenBucket:
        bucket = self._poke_buckets.get(gid)
        if bucket is None:
            bucket = _TokenBucket(
                capacity=8.0,
                refill_period_seconds=300.0,
                tokens=8.0,
                last_refill_ts=time.time(),
            )
            self._poke_buckets[gid] = bucket
        return bucket

    def _is_at_me(self, raw: Dict[str, Any], bot_id: str) -> bool:
        if raw.get("to_me") is True:
            return True
        msg: List[Dict[str, Any]] = raw.get("message", []) or []
        return any(
            seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == bot_id
            for seg in msg
        )

    def _strip_at_me(self, raw: Dict[str, Any], bot_id: str, text: str) -> str:
        msg: List[Dict[str, Any]] = raw.get("message", []) or []
        if msg and msg[0].get("type") == "at" and str(msg[0].get("data", {}).get("qq")) == bot_id:
            return text.replace(text.split()[0], "", 1).strip()
        return text.strip()

    async def _send_group_msg(self, event: AstrMessageEvent, gid: int, message: str) -> None:
        await event.bot.api.call_action("send_group_msg", group_id=gid, message=message)

    async def _handle_call_name(self, event: AstrMessageEvent, raw: Dict[str, Any]) -> bool:
        gid = raw.get("group_id")
        if gid is None:
            return False

        bot_id = str(event.get_self_id())
        if not self._is_at_me(raw, bot_id):
            return False

        content = self._strip_at_me(raw, bot_id, (event.message_str or "").strip())
        if content:
            return False

        await asyncio.sleep(1)
        replies = [
            "在此，有何贵干~",
            "(っ●ω●)っ在~",
            "这里是我(っ●ω●)っ",
            "不在呢~",
        ]
        await self._send_group_msg(event, int(gid), random.choice(replies))
        return True

    async def _handle_poke(self, event: AstrMessageEvent, raw: Dict[str, Any]) -> bool:
        gid = raw.get("group_id")
        if gid is None:
            return False

        if raw.get("post_type") != "notice":
            return False

        notice_type = raw.get("notice_type")
        sub_type = raw.get("sub_type")
        if not (
            (notice_type == "notify" and sub_type == "poke")
            or notice_type == "poke"
            or (notice_type == "notify" and raw.get("notify_type") == "poke")
        ):
            return False

        bot_id = str(event.get_self_id())
        target_id = raw.get("target_id")
        if target_id is not None and str(target_id) != bot_id:
            return False

        bucket = self._get_bucket(int(gid))
        await asyncio.sleep(1)
        if bucket.acquire(3.0):
            await self._send_group_msg(event, int(gid), "请不要戳我 >_<")
            return True
        if bucket.acquire(1.0):
            await self._send_group_msg(event, int(gid), "喂(#`O′) 戳我干嘛！")
            return True
        return False

    async def _handle_aircon(self, event: AstrMessageEvent, raw: Dict[str, Any]) -> bool:
        gid = raw.get("group_id")
        if gid is None:
            return False

        if raw.get("post_type") != "message" or raw.get("message_type") != "group":
            return False

        text = (event.message_str or "").strip()
        if not text:
            return False

        gid_int = int(gid)

        if text == "空调开":
            self._aircon_switch[gid_int] = True
            await self._send_group_msg(event, gid_int, "❄️哔~")
            return True

        if text == "空调关":
            self._aircon_switch[gid_int] = False
            self._aircon_temp.pop(gid_int, None)
            await self._send_group_msg(event, gid_int, "💤哔~")
            return True

        if text == "群温度":
            if gid_int not in self._aircon_temp:
                self._aircon_temp[gid_int] = 26
            temp = self._aircon_temp[gid_int]
            if self._aircon_switch.get(gid_int, False):
                await self._send_group_msg(event, gid_int, f"❄️风速中\n群温度 {temp}℃")
            else:
                await self._send_group_msg(event, gid_int, f"💤\n群温度 {temp}℃")
            return True

        m = re.fullmatch(r"设置温度(\d+)", text)
        if m:
            if gid_int not in self._aircon_temp:
                self._aircon_temp[gid_int] = 26
            new_temp = int(m.group(1))
            self._aircon_temp[gid_int] = new_temp
            if self._aircon_switch.get(gid_int, False):
                await self._send_group_msg(event, gid_int, f"❄️风速中\n群温度 {new_temp}℃")
            else:
                await self._send_group_msg(event, gid_int, f"💤\n群温度 {new_temp}℃")
            return True

        return False

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def handle_event(self, event: AstrMessageEvent):
        if event.get_platform_name() != "aiocqhttp":
            return

        raw = event.message_obj.raw_message

        if await self._handle_poke(event, raw):
            return

        if await self._handle_call_name(event, raw):
            event.stop_event()
            return

        if await self._handle_aircon(event, raw):
            event.stop_event()
            return
