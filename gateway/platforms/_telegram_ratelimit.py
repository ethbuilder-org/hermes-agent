"""ethbuilder permfix: bulletproof Telegram rate limiter.

Defense layers (in order):
  L1 — per-chat token bucket (1 msg/sec, burst 4); on empty, AWAIT briefly
       (bounded wait) instead of dropping so PTB pipeline never sees a
       broken response object.
  L2 — duplicate-message dedup (same text → same chat within 300s = drop)
  L3 — global rate (25 msg/sec across all chats; Telegram caps at 30)
  L4 — reactive lockout on RetryAfter (uses Telegram's retry_after with a
       tiny 1s clock-skew buffer; raises telegram.error.RetryAfter so the
       caller's existing error path handles it)
  L5 — per-message edit rate cap (1 edit/sec/message, burst 2) AND a hard
       ceiling on total edits (200/message). Soft-rate drops are silent —
       editMessageText is allowed to return True so PTB doesn't crash.
  L6 — Bot._do_post monkey-patch (belt-and-suspenders for any code
       path that bypasses TelegramAdapter)

2026-05-22 rewrite: previous version returned a _DroppedMessage sentinel
which crashed python-telegram-bot's Message.de_json (calls .copy() on the
result). That caused the "mobile gap" issue where the gateway would silently
fail every send/edit for the duration of a 60-72s lockout window. The new
version uses standard PTB exceptions and PTB-compatible return values so
drops integrate cleanly with the existing retry/backoff machinery.
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TelegramRateLimiter:
    # L1 — per-chat send
    PER_CHAT_RATE = 1.0          # tokens/sec
    PER_CHAT_BURST = 4.0
    # L3 — global
    GLOBAL_RATE = 25             # msg/sec total
    GLOBAL_WINDOW = 1.0          # seconds
    # L2 — dedup
    DEDUP_WINDOW_SEC = 300.0
    # L5 — edit rate + hard ceiling
    PER_MESSAGE_EDIT_RATE = 1.0  # edits/sec per message_id
    PER_MESSAGE_EDIT_BURST = 2.0
    MAX_EDITS_PER_MESSAGE = 200
    # L1/L3 bounded wait — how long acquire_send will await refill before
    # giving up and raising RetryAfter. Short enough to keep callers responsive,
    # long enough to absorb normal burst smoothing.
    MAX_SEND_WAIT_SEC = 3.0

    def __init__(self):
        self._chat_buckets: dict = {}              # chat_key -> (tokens, last_refill)
        self._msg_edit_buckets: dict = {}          # (chat_key, msg_id) -> (tokens, last_refill)
        self._global_window: deque = deque()
        self._dedup: dict = {}                     # (chat_key, hash) -> ts
        self._chat_locks: dict = {}                # chat_key -> until_ts
        self._edit_counts: dict = {}               # (chat_key, msg_id) -> count
        self._lock = asyncio.Lock()
        self._allowed_count = 0
        self._dropped_count = 0
        self._lockout_count = 0

    def _check_lockout(self, chat_key: str, now: float) -> float:
        """Return seconds remaining on L4 lockout, or 0 if not locked."""
        lock_until = self._chat_locks.get(chat_key, 0)
        return max(0.0, lock_until - now)

    async def acquire_send(self, chat_id, text: Optional[str] = None) -> bool:
        """Reserve a send slot for ``chat_id``.

        Returns True on success.  Returns False ONLY for L2 dedup hits
        (the same text within 300s — caller should silently treat as
        already-sent).  Raises ``telegram.error.RetryAfter`` for L4
        lockouts and after bounded waits exhaust on L1/L3 starvation —
        this lets PTB's standard error path handle the drop instead of
        polluting the response pipeline with a sentinel object.
        """
        chat_key = str(chat_id)
        deadline = time.monotonic() + self.MAX_SEND_WAIT_SEC
        try:
            from telegram.error import RetryAfter
        except Exception:  # pragma: no cover — defensive
            RetryAfter = None  # type: ignore[assignment]

        while True:
            async with self._lock:
                now = time.monotonic()

                # L4 reactive lockout — raise RetryAfter so PTB handles it
                remaining = self._check_lockout(chat_key, now)
                if remaining > 0:
                    self._dropped_count += 1
                    logger.debug(
                        "[ratelimit] L4 chat=%s locked for %.1fs more",
                        chat_key, remaining,
                    )
                    if RetryAfter is not None:
                        raise RetryAfter(retry_after=max(1, int(remaining) + 1))
                    return False

                # L2 dedup — silent skip
                if text:
                    k = (chat_key, hash(text[:200]))
                    last = self._dedup.get(k, 0)
                    if now - last < self.DEDUP_WINDOW_SEC:
                        self._dropped_count += 1
                        logger.debug(
                            "[ratelimit] L2 dedup chat=%s text=%r",
                            chat_key, text[:80],
                        )
                        return False

                # L3 global window cleanup
                while (
                    self._global_window
                    and now - self._global_window[0] >= self.GLOBAL_WINDOW
                ):
                    self._global_window.popleft()

                global_ok = len(self._global_window) < self.GLOBAL_RATE

                # L1 per-chat bucket
                tokens, last_refill = self._chat_buckets.get(
                    chat_key, (self.PER_CHAT_BURST, now)
                )
                tokens = min(
                    self.PER_CHAT_BURST,
                    tokens + (now - last_refill) * self.PER_CHAT_RATE,
                )

                if global_ok and tokens >= 1.0:
                    # Commit
                    self._chat_buckets[chat_key] = (tokens - 1.0, now)
                    self._global_window.append(now)
                    if text:
                        self._dedup[(chat_key, hash(text[:200]))] = now
                    self._allowed_count += 1
                    return True

                # Bucket empty or global cap hit — fall through to await below
                if not global_ok:
                    logger.debug(
                        "[ratelimit] L3 global cap chat=%s window=%d",
                        chat_key, len(self._global_window),
                    )
                else:
                    logger.debug(
                        "[ratelimit] L1 bucket empty chat=%s tokens=%.2f",
                        chat_key, tokens,
                    )

            # Bounded await for refill (outside the lock to allow others)
            if time.monotonic() >= deadline:
                self._dropped_count += 1
                if RetryAfter is not None:
                    raise RetryAfter(retry_after=1)
                return False
            await asyncio.sleep(0.1)

    async def acquire_edit(self, chat_id, message_id) -> bool:
        """Reserve an edit slot for (chat, message_id).

        Returns True when the edit may proceed. Returns False when the
        soft rate cap (1 edit/sec per message) is full — the caller
        should treat this as a silent skip (the stream consumer will
        edit again on the next cycle once the bucket refills).

        Raises ``telegram.error.RetryAfter`` on L4 chat-wide lockout.
        """
        chat_key = str(chat_id)
        key = (chat_key, str(message_id))
        try:
            from telegram.error import RetryAfter
        except Exception:  # pragma: no cover
            RetryAfter = None  # type: ignore[assignment]

        async with self._lock:
            now = time.monotonic()

            # L4 lockout
            remaining = self._check_lockout(chat_key, now)
            if remaining > 0:
                self._dropped_count += 1
                if RetryAfter is not None:
                    raise RetryAfter(retry_after=max(1, int(remaining) + 1))
                return False

            # L5a — hard ceiling
            count = self._edit_counts.get(key, 0)
            if count >= self.MAX_EDITS_PER_MESSAGE:
                self._dropped_count += 1
                logger.debug(
                    "[ratelimit] L5 hard cap chat=%s msg=%s count=%d",
                    chat_id, message_id, count,
                )
                return False

            # L5b — per-message edit rate (1/sec, burst 2). On empty bucket,
            # silently drop so caller skips this edit cycle (next iteration
            # naturally retries when the bucket has refilled). This prevents
            # gateway from hammering Telegram and tripping its per-message
            # 429 flood control — the original cause of the mobile-gap bug.
            tokens, last_refill = self._msg_edit_buckets.get(
                key, (self.PER_MESSAGE_EDIT_BURST, now),
            )
            tokens = min(
                self.PER_MESSAGE_EDIT_BURST,
                tokens + (now - last_refill) * self.PER_MESSAGE_EDIT_RATE,
            )
            if tokens < 1.0:
                self._dropped_count += 1
                logger.debug(
                    "[ratelimit] L5 edit rate chat=%s msg=%s tokens=%.2f",
                    chat_id, message_id, tokens,
                )
                return False

            self._msg_edit_buckets[key] = (tokens - 1.0, now)
            self._edit_counts[key] = count + 1
            self._allowed_count += 1
            return True

    async def report_retry_after(self, chat_id, retry_after_sec: float):
        """Telegram returned 429 — record the lockout window.

        2026-05-22 ethbuilder fix: trust Telegram's retry_after exactly
        (with a tiny 1s clock-skew buffer). The previous +60s buffer
        produced 60-72s gaps for what Telegram itself said was a 12s
        block, and during the buffered window every send/edit failed
        with the _DroppedMessage AttributeError that caused the mobile
        "gap" issue.
        """
        chat_key = str(chat_id)
        async with self._lock:
            now = time.monotonic()
            until = now + max(retry_after_sec + 1.0, 1.0)
            cur = self._chat_locks.get(chat_key, 0)
            if until > cur:
                self._chat_locks[chat_key] = until
                self._lockout_count += 1
                logger.warning(
                    "[ratelimit] L4 RetryAfter %.1fs from Telegram; "
                    "locking chat=%s for %.1fs",
                    retry_after_sec, chat_key, until - now,
                )

    def reset_edit_count(self, chat_id, message_id):
        key = (str(chat_id), str(message_id))
        self._edit_counts.pop(key, None)
        self._msg_edit_buckets.pop(key, None)

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            "allowed": self._allowed_count,
            "dropped": self._dropped_count,
            "lockouts_triggered": self._lockout_count,
            "chats_tracked": len(self._chat_buckets),
            "chats_locked_now": sum(
                1 for t in self._chat_locks.values() if t > now
            ),
        }


_INSTANCE: Optional[TelegramRateLimiter] = None


def get_limiter() -> TelegramRateLimiter:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TelegramRateLimiter()
    return _INSTANCE


def install_monkey_patch():
    """Layer 6 — hook Bot._do_post so EVERY outbound API call passes through
    the limiter. Idempotent; safe to call multiple times.

    2026-05-22 rewrite: drops now integrate with PTB's existing error
    machinery. Sends raise RetryAfter (caught by telegram.py's existing
    retry-after handler). Edits that are rate-capped return True — PTB
    interprets that as a boolean "ok" response for editMessageText/Caption/
    Media/ReplyMarkup, and the gateway's edit_message handler returns
    success without crashing. The stream consumer skips this cycle; the
    next edit_interval tick will deliver the latest accumulated content
    once the per-message edit bucket has refilled.
    """
    try:
        from telegram._bot import Bot
    except Exception as e:
        logger.error("[ratelimit] L6 install failed; telegram lib import: %s", e)
        return

    if getattr(Bot, "_ethbuilder_patched", False):
        return

    original_do_post = Bot._do_post

    _RATE_LIMITED_ENDPOINTS = {
        "sendMessage", "sendPhoto", "sendVoice", "sendAudio",
        "sendDocument", "sendVideo", "sendAnimation",
        "sendMediaGroup", "sendChatAction",
        "editMessageText", "editMessageCaption", "editMessageMedia",
        "editMessageReplyMarkup",
    }

    async def patched_do_post(self, endpoint, data=None, *args, **kwargs):
        if endpoint not in _RATE_LIMITED_ENDPOINTS:
            return await original_do_post(self, endpoint, data, *args, **kwargs)

        chat_id = data.get("chat_id") if data else None
        text = (data.get("text") or data.get("caption")) if data else None

        limiter = get_limiter()
        if endpoint.startswith("edit"):
            msg_id = data.get("message_id") if data else None
            if chat_id is not None and msg_id is not None:
                # acquire_edit may raise RetryAfter for L4 lockouts; that
                # propagates naturally up through PTB's error path.
                if not await limiter.acquire_edit(chat_id, msg_id):
                    # Soft drop (rate cap or hard cap): return True so PTB
                    # treats this as a successful no-op edit. The visible
                    # Telegram message stays at its previous text; the next
                    # accepted edit will catch it up to the latest content.
                    return True
        else:
            if chat_id is not None:
                # acquire_send may raise RetryAfter for L4 lockouts or
                # exhausted L1/L3 waits. Dedup hits return False — for
                # send_chat_action (typing indicators etc.) returning True
                # is safe; for sendMessage it suppresses a true duplicate.
                if not await limiter.acquire_send(chat_id, text):
                    return True

        try:
            return await original_do_post(self, endpoint, data, *args, **kwargs)
        except Exception as e:
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None and chat_id is not None:
                try:
                    sec = (retry_after.total_seconds()
                           if hasattr(retry_after, "total_seconds")
                           else float(retry_after))
                    await limiter.report_retry_after(chat_id, sec)
                except Exception:
                    pass
            raise

    Bot._do_post = patched_do_post
    Bot._ethbuilder_patched = True
    logger.info("[ratelimit] L6 monkey-patch installed (TelegramRateLimiter)")
