# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain


MAX_IMAGE_RESPONSE_BYTES = 32 * 1024 * 1024


class ImageGenerationError(RuntimeError):
    pass


class ImageGenerationFeatureMixin:
    def _get_image_generation_settings(self) -> Dict[str, Any]:
        raw = self.config.get("image_generation_settings", {})
        if not isinstance(raw, dict):
            raw = {}

        return {
            "enabled": bool(raw.get("enabled", False)),
            "api_url": str(raw.get("api_url", "") or "").strip()[:2000],
            "api_key": str(raw.get("api_key", "") or "").strip()[:4000],
            "model": str(raw.get("model", "gpt-image-1") or "").strip()[:120],
            "cost": min(
                self._normalize_int(raw.get("cost"), 100, minimum=0),
                1_000_000_000,
            ),
            "daily_limit": min(
                self._normalize_int(raw.get("daily_limit"), 3, minimum=1),
                1000,
            ),
            "size": str(raw.get("size", "1024x1024") or "").strip()[:40],
            "timeout_seconds": min(
                self._normalize_int(raw.get("timeout_seconds"), 120, minimum=10),
                600,
            ),
            "max_prompt_length": min(
                self._normalize_int(raw.get("max_prompt_length"), 800, minimum=20),
                4000,
            ),
        }

    @staticmethod
    def _resolve_image_generation_api_url(raw_url: str) -> str:
        url = str(raw_url or "").strip()
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError as exc:
            raise ImageGenerationError("生图 API 地址格式不正确") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ImageGenerationError("生图 API 地址必须以 http:// 或 https:// 开头")

        path = parsed.path.rstrip("/")
        if not path:
            path = "/v1/images/generations"
        elif path.endswith("/v1"):
            path += "/images/generations"
        return urllib.parse.urlunsplit(parsed._replace(path=path))

    @staticmethod
    def _image_api_error_message(payload: bytes) -> str:
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ""
        if not isinstance(decoded, dict):
            return ""
        error = decoded.get("error", decoded)
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail")
        else:
            message = error
        return " ".join(str(message or "").split())[:300]

    @staticmethod
    def _extract_image_generation_result(payload: Any) -> Dict[str, str]:
        if not isinstance(payload, dict):
            raise ImageGenerationError("生图 API 返回的数据格式不正确")

        candidates: list[Any] = []
        data = payload.get("data")
        if isinstance(data, list):
            candidates.extend(data)
        elif isinstance(data, dict):
            candidates.append(data)
        output = payload.get("output")
        if isinstance(output, list):
            candidates.extend(output)
        candidates.append(payload)

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            image_url = candidate.get("url") or candidate.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            image_url = str(image_url or "").strip()
            if image_url.startswith(("http://", "https://")):
                return {"kind": "url", "value": image_url}

            image_base64 = str(
                candidate.get("b64_json")
                or candidate.get("base64")
                or candidate.get("image_base64")
                or ""
            ).strip()
            if image_base64.startswith("data:") and "," in image_base64:
                image_base64 = image_base64.split(",", 1)[1]
            if not image_base64:
                continue
            if len(image_base64) > MAX_IMAGE_RESPONSE_BYTES * 2:
                raise ImageGenerationError("生图 API 返回的图片过大")
            try:
                base64.b64decode(image_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImageGenerationError("生图 API 返回了无效的 Base64 图片") from exc
            return {"kind": "base64", "value": image_base64}

        raise ImageGenerationError("生图 API 没有返回可用图片")

    @classmethod
    def _request_image_generation_sync(
        cls, settings: Dict[str, Any], prompt: str
    ) -> Dict[str, str]:
        url = cls._resolve_image_generation_api_url(settings["api_url"])
        body: Dict[str, Any] = {"prompt": prompt, "n": 1}
        if settings["model"]:
            body["model"] = settings["model"]
        if settings["size"]:
            body["size"] = settings["size"]

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AstrBotPointSystem/2.8",
        }
        if settings["api_key"]:
            headers["Authorization"] = f"Bearer {settings['api_key']}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=settings["timeout_seconds"]
            ) as response:
                raw_payload = response.read(MAX_IMAGE_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = cls._image_api_error_message(exc.read(65536))
            suffix = f"：{detail}" if detail else ""
            raise ImageGenerationError(f"生图 API 请求失败（HTTP {exc.code}）{suffix}") from exc
        except urllib.error.URLError as exc:
            raise ImageGenerationError(f"生图 API 连接失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ImageGenerationError("生图 API 请求超时") from exc

        if len(raw_payload) > MAX_IMAGE_RESPONSE_BYTES:
            raise ImageGenerationError("生图 API 响应过大")
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageGenerationError("生图 API 返回了无法解析的数据") from exc
        return cls._extract_image_generation_result(payload)

    async def _request_image_generation(
        self, settings: Dict[str, Any], prompt: str
    ) -> Dict[str, str]:
        return await asyncio.to_thread(
            self._request_image_generation_sync, settings, prompt
        )

    async def _refund_failed_image_generation(
        self, user_id: str, cost: int, reserved_date: str
    ) -> None:
        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            user_info["points"] += cost
            if user_info.get("last_image_generation_date") == reserved_date:
                user_info["daily_image_generation_times"] = max(
                    0,
                    self._normalize_int(
                        user_info.get("daily_image_generation_times"), 0, minimum=0
                    )
                    - 1,
                )
            user_info["image_generation_count"] = max(
                0,
                self._normalize_int(
                    user_info.get("image_generation_count"), 0, minimum=0
                )
                - 1,
            )
            user_info["image_generation_points_spent"] = max(
                0,
                self._normalize_int(
                    user_info.get("image_generation_points_spent"), 0, minimum=0
                )
                - cost,
            )
            self._record_point_transaction_locked(
                user_id,
                cost,
                "AI 生图失败退款",
                balance=user_info["points"],
            )
            if not await self._save_data_locked():
                logger.error("AI 生图失败退款保存失败: user=%s", user_id)

    async def image_generation(self, event: AstrMessageEvent):
        settings = self._get_image_generation_settings()
        points_name = self._get_points_name()
        prompt = str(self._get_command_args(event) or "").strip()

        if not settings["enabled"]:
            yield self._plain_result(event, "AI 生图功能当前未开启。")
            return
        if not prompt:
            yield self._plain_result(
                event,
                "\n".join(
                    [
                        "【AI 生图】",
                        "用法：/生图 提示词",
                        f"每次消耗：{settings['cost']} {points_name}",
                        f"每日上限：{settings['daily_limit']} 次",
                    ]
                ),
            )
            return
        if len(prompt) > settings["max_prompt_length"]:
            yield self._plain_result(
                event,
                f"提示词最多 {settings['max_prompt_length']} 个字符，请精简后重试。",
            )
            return
        try:
            self._resolve_image_generation_api_url(settings["api_url"])
        except ImageGenerationError as exc:
            yield self._plain_result(event, f"AI 生图尚未配置好：{exc}")
            return

        user_id = self._normalize_user_id(event.get_sender_id())
        today = datetime.date.today().isoformat()
        reservation_error = ""
        remaining_points = 0
        daily_times = 0
        async with self._data_lock:
            user_info = self._get_user_record(user_id)
            before = {
                "points": user_info["points"],
                "last_image_generation_date": user_info.get(
                    "last_image_generation_date", ""
                ),
                "daily_image_generation_times": self._normalize_int(
                    user_info.get("daily_image_generation_times"), 0, minimum=0
                ),
                "image_generation_count": self._normalize_int(
                    user_info.get("image_generation_count"), 0, minimum=0
                ),
                "image_generation_points_spent": self._normalize_int(
                    user_info.get("image_generation_points_spent"), 0, minimum=0
                ),
            }
            self._touch_group_member(
                event, user_id, self._get_sender_display_name(event)
            )
            if user_info.get("last_image_generation_date") != today:
                user_info["last_image_generation_date"] = today
                user_info["daily_image_generation_times"] = 0
            daily_times = self._normalize_int(
                user_info.get("daily_image_generation_times"), 0, minimum=0
            )
            if daily_times >= settings["daily_limit"]:
                reservation_error = (
                    f"你今天已使用 {daily_times}/{settings['daily_limit']} 次 AI 生图，"
                    "请明天再来。"
                )
            elif user_info["points"] < settings["cost"]:
                reservation_error = (
                    f"积分不足，AI 生图需要 {settings['cost']} {points_name}，"
                    f"当前只有 {user_info['points']} {points_name}。"
                )
            else:
                user_info["points"] -= settings["cost"]
                user_info["daily_image_generation_times"] = daily_times + 1
                user_info["image_generation_count"] = (
                    before["image_generation_count"] + 1
                )
                user_info["image_generation_points_spent"] = (
                    before["image_generation_points_spent"] + settings["cost"]
                )
                transaction = self._record_point_transaction_locked(
                    user_id,
                    -settings["cost"],
                    "AI 生图",
                    balance=user_info["points"],
                )
                if not await self._save_data_locked():
                    user_info.update(before)
                    if transaction is not None:
                        transactions = self.data.setdefault("point_transactions", [])
                        if transaction in transactions:
                            transactions.remove(transaction)
                    reservation_error = "积分扣除记录保存失败，本次没有调用生图 API。"
                else:
                    remaining_points = user_info["points"]
                    daily_times += 1

        if reservation_error:
            yield self._plain_result(event, reservation_error)
            return

        try:
            image_result = await self._request_image_generation(settings, prompt)
            if image_result["kind"] == "url":
                image = Image.fromURL(image_result["value"])
            else:
                image = Image.fromBase64(image_result["value"])
        except Exception as exc:
            await self._refund_failed_image_generation(
                user_id, settings["cost"], today
            )
            if isinstance(exc, ImageGenerationError):
                error_text = str(exc)
            else:
                logger.warning("AI 生图失败: %s", exc)
                error_text = "图片生成失败"
            yield self._plain_result(
                event,
                f"{error_text}，已退还 {settings['cost']} {points_name}。",
            )
            return

        yield event.chain_result(
            [
                Plain(
                    "【AI 生图完成】\n"
                    f"消耗：{settings['cost']} {points_name}\n"
                    f"今日次数：{daily_times}/{settings['daily_limit']}\n"
                    f"余额：{remaining_points} {points_name}\n"
                ),
                image,
            ]
        )
