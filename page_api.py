# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from typing import Any

from astrbot.api import logger
from astrbot.api.web import request


PAGE_API_PREFIX = "/astrbot_plugin_point_system/page"


class PointSystemPageApi:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._settings_lock = asyncio.Lock()
        self._registered_routes: list[tuple[str, Any]] = []

    def register_routes(self) -> None:
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            logger.warning("[PointSystem] 当前 AstrBot 不支持插件拓展页 API")
            return

        routes = [
            ("/overview", self.overview, ["GET"], "Point System exchange overview"),
            ("/items/save", self.save_items, ["POST"], "Point System save exchange items"),
        ]
        registered = getattr(self.plugin.context, "registered_web_apis", None)
        snapshot = list(registered) if isinstance(registered, list) else None
        try:
            for path, handler, methods, description in routes:
                route = f"{PAGE_API_PREFIX}{path}"
                register(route, handler, methods, description)
                self._registered_routes.append((route, handler))
        except Exception:
            if snapshot is not None:
                registered[:] = snapshot
                self._registered_routes.clear()
            else:
                self.unregister_routes()
            raise

    def unregister_routes(self) -> None:
        registered = getattr(self.plugin.context, "registered_web_apis", None)
        if not isinstance(registered, list) or not self._registered_routes:
            return
        owned = {(route, id(handler)) for route, handler in self._registered_routes}
        registered[:] = [
            item
            for item in registered
            if not (
                isinstance(item, tuple)
                and len(item) >= 2
                and (item[0], id(item[1])) in owned
            )
        ]
        self._registered_routes.clear()

    @staticmethod
    async def _payload() -> dict[str, Any]:
        value = await request.json(default={})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default

    @staticmethod
    def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(int(value), maximum))
        except (TypeError, ValueError):
            return default

    def _config_revision(self) -> str:
        raw_items = self.plugin.config.get("exchange_items", [])
        serialized = json.dumps(
            {
                "items": raw_items,
                "scope": self._scope_view(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]

    def _validate_scope(self, raw_scope: Any) -> tuple[dict[str, Any], str]:
        if not isinstance(raw_scope, dict):
            raw_scope = {}

        mode_value = str(raw_scope.get("mode") or "").strip().casefold()
        mode = (
            "whitelist"
            if mode_value in {"whitelist", "white", "allow", "白名单", "允许"}
            else "blacklist"
        )
        raw_values = raw_scope.get("scope", raw_scope.get("group_ids", []))
        if isinstance(raw_values, str):
            values = (
                raw_values.replace("，", ",")
                .replace("\r", "\n")
                .replace(",", "\n")
                .splitlines()
            )
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            values = []

        scope: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = str(raw_value or "").strip()[:120]
            if not value:
                continue
            normalized = value.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            scope.append(value)
        if len(scope) > 1000:
            return {}, "兑换适用范围最多配置 1000 个群号或账号"
        return {"mode": mode, "scope": scope}, ""

    def _scope_view(self) -> dict[str, Any]:
        scope, _ = self._validate_scope(
            self.plugin.config.get("exchange_scope", {})
        )
        return scope

    def _redemption_view(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        raw_status = self._text(value.get("delivery_status"), 32).casefold()
        return {
            "item_name": self._text(value.get("item_name"), 120),
            "user_id": self._text(value.get("user_id"), 120),
            "redeemed_at": self._text(value.get("redeemed_at"), 64),
            "cost": self._int(value.get("cost"), 0, 0, 1_000_000_000),
            "delivery_status": (
                "uncertain"
                if raw_status in {"pending", "uncertain"}
                else "delivered"
            ),
        }

    def _overview_locked(self) -> dict[str, Any]:
        redemptions = self.plugin.data.get("exchange_redemptions", [])
        if not isinstance(redemptions, list):
            redemptions = []
        redeemed_hashes = {
            record.get("content_hash")
            for record in redemptions
            if isinstance(record, dict)
        }

        item_views: list[dict[str, Any]] = []
        total_stock = 0
        enabled_count = 0
        for item in self.plugin._get_exchange_items():
            used_count = sum(
                1
                for content in item["contents"]
                if self.plugin._exchange_content_fingerprint(content)
                in redeemed_hashes
            )
            stock = max(len(item["contents"]) - used_count, 0)
            total_stock += stock
            enabled_count += int(item["enabled"])
            item_views.append(
                {
                    "name": item["name"],
                    "enabled": item["enabled"],
                    "cost": item["cost"],
                    "contents": item["contents"],
                    "private_only": item["private_only"],
                    "success_template": item["success_template"],
                    "stock": stock,
                    "used_count": used_count,
                    "total_count": len(item["contents"]),
                }
            )

        redemption_views = [
            view
            for view in (self._redemption_view(item) for item in reversed(redemptions))
            if view is not None
        ][:500]
        return {
            "points_name": self.plugin._get_points_name(),
            "exchange_scope": self._scope_view(),
            "items": item_views,
            "redemptions": redemption_views,
            "metrics": {
                "item_count": len(item_views),
                "enabled_count": enabled_count,
                "stock": total_stock,
                "redeemed_count": len(redemptions),
                "points_spent": sum(
                    self._int(item.get("cost"), 0, 0, 1_000_000_000)
                    for item in redemptions
                    if isinstance(item, dict)
                ),
            },
            "revision": self._config_revision(),
            "can_save": callable(getattr(self.plugin.config, "save_config", None)),
        }

    async def overview(self) -> dict[str, Any]:
        async with self.plugin._data_lock:
            data = self._overview_locked()
        return {"status": "ok", "data": data}

    def _validate_items(
        self, raw_items: Any
    ) -> tuple[list[dict[str, Any]], str]:
        if not isinstance(raw_items, list):
            return [], "兑换物数据格式不正确"
        if len(raw_items) > 100:
            return [], "兑换物最多配置 100 个"

        items: list[dict[str, Any]] = []
        names: set[str] = set()
        contents_seen: set[str] = set()
        for index, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, dict):
                return [], f"第 {index} 个兑换物格式不正确"
            name = self._text(raw_item.get("name"), 120)
            if not name:
                return [], f"第 {index} 个兑换物缺少名称"
            normalized_name = name.casefold()
            if normalized_name in names:
                return [], f"兑换物名称“{name}”重复"
            names.add(normalized_name)

            raw_contents = raw_item.get("contents", [])
            if not isinstance(raw_contents, list):
                return [], f"“{name}”的发放内容必须是列表"
            if len(raw_contents) > 10000:
                return [], f"“{name}”的发放内容最多 10000 条"
            contents: list[str] = []
            local_contents: set[str] = set()
            for raw_content in raw_contents:
                content = self._text(raw_content, 4000)
                if not content or content in local_contents:
                    continue
                if content in contents_seen:
                    return [], f"发放内容在多个兑换物中重复：{content[:40]}"
                local_contents.add(content)
                contents_seen.add(content)
                contents.append(content)

            template = self._text(raw_item.get("success_template"), 4000)
            if not template:
                template = (
                    "兑换成功！\n兑换物：{item}\n兑换内容：{content}\n"
                    "消耗 {cost} {points_name}，剩余 {remaining} {points_name}。"
                )
            items.append(
                {
                    "name": name,
                    "enabled": self._bool(raw_item.get("enabled"), True),
                    "cost": self._int(raw_item.get("cost"), 100, 1, 1_000_000_000),
                    "contents": contents,
                    "private_only": self._bool(raw_item.get("private_only"), True),
                    "success_template": template,
                }
            )
        return items, ""

    async def save_items(self) -> dict[str, Any]:
        payload = await self._payload()
        async with self._settings_lock:
            base_revision = self._text(payload.get("revision"), 64)
            current_revision = self._config_revision()
            if base_revision and base_revision != current_revision:
                return {
                    "status": "error",
                    "message": "兑换配置已在其他页面更新，请刷新后再保存",
                    "data": {"revision": current_revision},
                }

            items, error = self._validate_items(payload.get("items"))
            if error:
                return {"status": "error", "message": error}
            scope, scope_error = self._validate_scope(
                payload.get(
                    "exchange_scope",
                    self.plugin.config.get("exchange_scope", {}),
                )
            )
            if scope_error:
                return {"status": "error", "message": scope_error}

            config_snapshot = copy.deepcopy(dict(self.plugin.config))
            async with self.plugin._data_lock:
                self.plugin.config["exchange_items"] = items
                self.plugin.config["exchange_scope"] = scope
                saver = getattr(self.plugin.config, "save_config", None)
                if callable(saver):
                    try:
                        result = saver()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception as exc:
                        self.plugin.config.clear()
                        self.plugin.config.update(config_snapshot)
                        logger.warning("[PointSystem] 保存兑换配置失败: %s", exc)
                        return {
                            "status": "error",
                            "message": "AstrBot 配置保存失败，本次修改未生效",
                        }
                data = self._overview_locked()
            return {"status": "ok", "data": data}
