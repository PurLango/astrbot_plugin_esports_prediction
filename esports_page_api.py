# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.web import request


PAGE_API_PREFIX = "/astrbot_plugin_point_system/esports"


class EsportsPredictionPageApi:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._settings_lock = asyncio.Lock()
        self._registered_routes: list[tuple[str, Any]] = []

    def register_routes(self) -> None:
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            return
        routes = [
            ("/overview", self.overview, ["GET"], "Esports prediction overview"),
            ("/sync", self.sync, ["POST"], "Sync esports matches"),
            ("/settings/save", self.save_settings, ["POST"], "Save esports settings"),
            ("/matches/add", self.add_match, ["POST"], "Add esports match"),
            ("/matches/action", self.match_action, ["POST"], "Manage esports match"),
            (
                "/candidates/action",
                self.candidate_action,
                ["POST"],
                "Manage esports match candidates",
            ),
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
    def _text(value: Any, limit: int = 200) -> str:
        return str(value or "").strip()[:limit]

    def _settings_view(self) -> dict[str, Any]:
        settings = self.plugin._get_esports_settings()
        return {
            "enabled": settings["enabled"],
            "sync_enabled": settings["sync_enabled"],
            "provider": settings["provider"],
            "token_configured": bool(settings["pandascore_token"]),
            "games": settings["games"],
            "tracked_competitions": settings["tracked_competitions"],
            "sync_interval_minutes": settings["sync_interval_minutes"],
            "min_bet": settings["min_bet"],
            "max_bet": settings["max_bet"],
            "switch_deadline_minutes": settings["switch_deadline_minutes"],
            "close_before_minutes": settings["close_before_minutes"],
            "odds_margin": settings["odds_margin"],
            "timezone_offset_hours": settings["timezone_offset_hours"],
        }

    def _overview_locked(self) -> dict[str, Any]:
        esports = self.plugin._get_esports_store()
        matches = esports.setdefault("matches", {})
        bets = esports.setdefault("bets", {})
        candidates = esports.setdefault("candidates", {})
        match_views = []
        for match in matches.values():
            if not isinstance(match, dict):
                continue
            teams = match.get("teams", [])
            if not isinstance(teams, list) or len(teams) != 2:
                continue
            pool = self.plugin._match_pool_locked(match["id"])
            match_views.append(
                {
                    "id": match["id"],
                    "display_id": match.get("display_id", ""),
                    "game": match.get("game", ""),
                    "competition": match.get("competition", ""),
                    "name": match.get("name", ""),
                    "start_time": match.get("start_time", ""),
                    "start_time_text": self.plugin._format_esports_time(match.get("start_time")),
                    "status": match.get("status", ""),
                    "visible": bool(match.get("visible", True)),
                    "odds_locked": bool(match.get("odds_locked", False)),
                    "winner_id": match.get("winner_id", ""),
                    "teams": [
                        {
                            "id": team.get("id", ""),
                            "name": team.get("name", ""),
                            "odds": match.get("odds", {}).get(team.get("id"), 1.0),
                            "probability": match.get("probabilities", {}).get(team.get("id"), 0.5),
                            "pool": pool.get(team.get("id"), 0),
                        }
                        for team in teams
                    ],
                }
            )
        match_views.sort(key=lambda item: item["start_time"], reverse=True)

        candidate_views = []
        for candidate in candidates.values():
            if not isinstance(candidate, dict):
                continue
            teams = candidate.get("teams", [])
            if not isinstance(teams, list) or len(teams) != 2:
                continue
            candidate_views.append(
                {
                    "id": candidate.get("id", ""),
                    "game": candidate.get("game", ""),
                    "competition": candidate.get("competition", ""),
                    "name": candidate.get("name", ""),
                    "start_time": candidate.get("start_time", ""),
                    "start_time_text": self.plugin._format_esports_time(candidate.get("start_time")),
                    "status": candidate.get("status", ""),
                    "dismissed": bool(candidate.get("dismissed", False)),
                    "teams": [{"name": team.get("name", "")} for team in teams],
                }
            )
        candidate_views.sort(key=lambda item: item["start_time"])

        bet_views = []
        for bet in bets.values():
            if not isinstance(bet, dict):
                continue
            match = matches.get(bet.get("match_id"), {})
            bet_views.append(
                {
                    "match_display_id": match.get("display_id", "?"),
                    "user_id": bet.get("user_id", ""),
                    "team_name": bet.get("team_name", ""),
                    "amount": bet.get("amount", 0),
                    "odds": bet.get("odds", 1.0),
                    "status": bet.get("status", ""),
                    "payout": bet.get("payout", 0),
                    "updated_at": bet.get("updated_at", ""),
                }
            )
        bet_views.sort(key=lambda item: item["updated_at"], reverse=True)
        return {
            "ok": True,
            "settings": self._settings_view(),
            "sync": dict(esports.setdefault("sync", {})),
            "summary": {
                "match_count": len(match_views),
                "open_match_count": sum(
                    item["status"] in {"not_started", "running"} for item in match_views
                ),
                "bet_count": len(bet_views),
                "pending_points": sum(
                    int(bet.get("amount", 0))
                    for bet in bets.values()
                    if isinstance(bet, dict) and bet.get("status") == "pending"
                ),
                "candidate_count": sum(
                    1 for item in candidate_views if not item["dismissed"]
                ),
                "dismissed_count": sum(1 for item in candidate_views if item["dismissed"]),
            },
            "matches": match_views[:200],
            "candidates": candidate_views[:400],
            "bets": bet_views[:300],
        }

    async def overview(self) -> dict[str, Any]:
        async with self.plugin._data_lock:
            return self._overview_locked()

    async def sync(self) -> dict[str, Any]:
        try:
            result = await self.plugin._sync_esports_once("管理页同步")
            return {"ok": True, **result}
        except Exception as exc:
            logger.warning(f"竞猜管理页同步失败：{exc}")
            return {"ok": False, "error": str(exc)}

    async def save_settings(self) -> dict[str, Any]:
        payload = await self._payload()
        async with self._settings_lock:
            current = self.plugin.config.get("esports_prediction_settings", {})
            if not isinstance(current, dict):
                current = {}
            updated = dict(current)
            for key in (
                "enabled",
                "sync_enabled",
                "sync_interval_minutes",
                "min_bet",
                "max_bet",
                "switch_deadline_minutes",
                "close_before_minutes",
                "odds_margin",
                "timezone_offset_hours",
            ):
                if key in payload:
                    updated[key] = payload[key]
            for key in ("games", "tracked_competitions"):
                value = payload.get(key)
                if isinstance(value, list):
                    updated[key] = [self._text(item, 120) for item in value if self._text(item, 120)]
            token = self._text(payload.get("pandascore_token"), 500)
            if token:
                updated["pandascore_token"] = token
            if payload.get("clear_token") is True:
                updated["pandascore_token"] = ""
            self.plugin.config["esports_prediction_settings"] = updated
            saver = getattr(self.plugin.config, "save_config", None)
            if callable(saver):
                result = saver()
                if asyncio.iscoroutine(result):
                    await result
        return {"ok": True, "settings": self._settings_view()}

    async def add_match(self) -> dict[str, Any]:
        payload = await self._payload()
        game = self._text(payload.get("game"), 20).lower()
        competition = self._text(payload.get("competition"), 120)
        team_a = self._text(payload.get("team_a"), 80)
        team_b = self._text(payload.get("team_b"), 80)
        start_time = self._text(payload.get("start_time"), 30)
        if game not in {"lol", "valorant"} or not all((competition, team_a, team_b, start_time)):
            return {"ok": False, "error": "请完整填写游戏、赛事、两支队伍和开赛时间。"}
        try:
            async with self.plugin._data_lock:
                match = self.plugin._create_manual_match_locked(
                    game, competition, team_a, team_b, start_time
                )
                await self.plugin._save_data_locked()
        except ValueError:
            return {"ok": False, "error": "时间格式应为 YYYY-MM-DD HH:MM。"}
        return {"ok": True, "display_id": match["display_id"]}

    async def match_action(self) -> dict[str, Any]:
        payload = await self._payload()
        action = self._text(payload.get("action"), 20).lower()
        token = self._text(payload.get("match_id"), 100)
        async with self.plugin._data_lock:
            match = self.plugin._resolve_match_locked(token)
            if not match:
                return {"ok": False, "error": "未找到比赛。"}
            if action == "settle":
                if match.get("settled_at"):
                    return {"ok": False, "error": "比赛已经处理过。"}
                team = self.plugin._resolve_match_team(match, self._text(payload.get("team_id"), 100))
                if not team:
                    return {"ok": False, "error": "请选择获胜队伍。"}
                match["winner_id"] = team["id"]
                match["status"] = "finished"
                settled, winners, paid = self.plugin._settle_match_locked(match)
                self.plugin._apply_rating_result_locked(match)
                message = f"结算 {settled} 注，命中 {winners} 注，返还 {paid}。"
            elif action == "refund":
                if match.get("settled_at"):
                    return {"ok": False, "error": "比赛已经处理过。"}
                count, points = self.plugin._refund_match_locked(match, "管理页退款")
                message = f"退款 {count} 人，共 {points} 积分。"
            elif action == "close":
                match["status"] = "closed"
                message = "已封盘。"
            elif action in {"hide", "show"}:
                match["visible"] = action == "show"
                message = "已更新显示状态。"
            else:
                return {"ok": False, "error": "不支持的操作。"}
            match["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            await self.plugin._save_data_locked()
        return {"ok": True, "message": message}

    async def candidate_action(self) -> dict[str, Any]:
        payload = await self._payload()
        action = self._text(payload.get("action"), 20).lower()
        raw_ids = payload.get("match_ids")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list):
            raw_ids = []
        match_ids = [self._text(item, 120) for item in raw_ids if self._text(item, 120)]
        if action not in {"include", "dismiss", "restore"}:
            return {"ok": False, "error": "不支持的操作。"}
        if not match_ids:
            return {"ok": False, "error": "请选择候选比赛。"}
        included = 0
        dismissed = 0
        restored = 0
        display_ids: list[str] = []
        async with self.plugin._data_lock:
            candidates = self.plugin._get_esports_store().setdefault("candidates", {})
            for match_id in match_ids:
                candidate = candidates.get(match_id)
                if not isinstance(candidate, dict):
                    continue
                if action == "include":
                    match = self.plugin._include_candidate_locked(match_id)
                    if match is not None:
                        included += 1
                        display_ids.append(str(match.get("display_id", "")))
                elif action == "dismiss":
                    candidate["dismissed"] = True
                    dismissed += 1
                else:
                    candidate["dismissed"] = False
                    restored += 1
            if not (included or dismissed or restored):
                return {"ok": False, "error": "未找到所选比赛。"}
            await self.plugin._save_data_locked()
        if action == "include":
            message = f"已加入竞猜 {included} 场：{'、'.join(display_ids[:8])}{'…' if len(display_ids) > 8 else ''}"
        elif action == "dismiss":
            message = f"已忽略 {dismissed} 场候选比赛。"
        else:
            message = f"已恢复 {restored} 场候选比赛。"
        return {"ok": True, "message": message}
