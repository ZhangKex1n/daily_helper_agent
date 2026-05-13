"""
Weixin latency probe (production-safe, opt-in).

Enable by setting DEBUG_MODE=true in .env (or environment).
When disabled, function calls are near no-op.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.log import logger
from config import get_appdata_dir


def _read_debug_mode_from_env_file() -> str:
    """Read DEBUG_MODE from project config/.env (preferred) or root .env."""
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        project_root / "config" / ".env",
        project_root / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                key, value = s.split("=", 1)
                if key.strip() == "DEBUG_MODE":
                    return value.strip().strip("\"'").lower()
        except Exception:
            continue
    return ""


def _is_debug_mode() -> bool:
    # Prefer .env so local debugging does not get shadowed by shell env leftovers.
    v = _read_debug_mode_from_env_file()
    if not v:
        v = (os.getenv("DEBUG_MODE", "") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


@dataclass
class _DelayRecord:
    channel: str
    msg_id: str
    user_id: str
    official_ts_ms: int
    recv_ts_ms: int
    llm_done_ts_ms: int = 0
    sent_ts_ms: int = 0


class WeixinDelayProbe:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _DelayRecord] = {}
        self._log_path = Path(get_appdata_dir()) / "weixin_delay_metrics.jsonl"

    @property
    def enabled(self) -> bool:
        return _is_debug_mode()

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _msg_id_from_context(context: Any) -> str:
        if not context:
            return ""
        msg = context.get("msg")
        return str(getattr(msg, "msg_id", "") or context.get("msg_id", "") or "")

    @staticmethod
    def _channel_from_context(context: Any) -> str:
        if not context:
            return ""
        return str(context.get("channel_type", "") or "")

    def _append(self, payload: dict[str, Any]) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def mark_received(self, raw_msg: dict[str, Any]) -> None:
        if not self.enabled:
            return
        msg_id = str(raw_msg.get("message_id", raw_msg.get("seq", "")))
        if not msg_id:
            return
        official_ts_ms = int(raw_msg.get("create_time_ms") or 0)
        recv_ts_ms = self._now_ms()
        rec = _DelayRecord(
            channel="weixin",
            msg_id=msg_id,
            user_id=str(raw_msg.get("from_user_id", "")),
            official_ts_ms=official_ts_ms,
            recv_ts_ms=recv_ts_ms,
        )
        with self._lock:
            self._records[msg_id] = rec

    def mark_llm_done(self, context: Any) -> None:
        if not self.enabled:
            return
        msg_id = self._msg_id_from_context(context)
        if not msg_id:
            return
        with self._lock:
            rec = self._records.get(msg_id)
            if not rec:
                return
            rec.llm_done_ts_ms = self._now_ms()

    def mark_sent(self, context: Any) -> None:
        if not self.enabled:
            return
        msg_id = self._msg_id_from_context(context)
        if not msg_id:
            return
        with self._lock:
            rec = self._records.get(msg_id)
            if not rec:
                return
            rec.sent_ts_ms = self._now_ms()
            payload = {
                "channel": rec.channel,
                "msg_id": rec.msg_id,
                "user_id": rec.user_id,
                "official_ts_ms": rec.official_ts_ms,
                "recv_ts_ms": rec.recv_ts_ms,
                "llm_done_ts_ms": rec.llm_done_ts_ms,
                "sent_ts_ms": rec.sent_ts_ms,
                "official_to_recv_ms": (rec.recv_ts_ms - rec.official_ts_ms) if rec.official_ts_ms else None,
                "recv_to_llm_done_ms": (rec.llm_done_ts_ms - rec.recv_ts_ms) if rec.llm_done_ts_ms else None,
                "llm_done_to_send_ms": (rec.sent_ts_ms - rec.llm_done_ts_ms) if rec.llm_done_ts_ms else None,
                "recv_to_send_ms": rec.sent_ts_ms - rec.recv_ts_ms,
            }
            try:
                self._append(payload)
            except Exception as exc:
                logger.warning("[WeixinDelayProbe] write metrics failed: %s", exc)
            finally:
                self._records.pop(msg_id, None)


probe = WeixinDelayProbe()
