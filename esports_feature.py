# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
import math
import re
import uuid
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

try:
    from .esports_provider import EsportsProviderError, PandaScoreProvider
except ImportError:
    from esports_provider import EsportsProviderError, PandaScoreProvider


TIER_ONE_EXCLUSIONS = {
    "lol": (
        "academy",
        "challenger",
        "development",
        "lck cl",
        "secondary",
        "youth",
    ),
    "valorant": (
        "academy",
        "ascension",
        "challenger",
        "collegiate",
        "game changers",
    ),
}
FINISHED_BET_STATUSES = {"won", "lost", "refunded"}
OPEN_MATCH_STATUSES = {"not_started", "running"}
REFUND_MATCH_STATUSES = {"canceled", "cancelled", "postponed", "abandoned"}
TERMINAL_MATCH_STATUSES = REFUND_MATCH_STATUSES | {"finished", "settled", "refunded"}
MATCH_RESULT_RETENTION_HOURS = 24
ELO_CONFIDENCE_FULL_GAMES = 10.0
ELO_CONFIDENCE_PRIOR_GAMES = 2.0


class EsportsPredictionMixin:
    def _new_esports_store(self) -> Dict[str, Any]:
        return {
            "matches": {},
            "bets": {},
            "ratings": {},
            "rating_processed_match_ids": [],
            "display_sequences": {"lol": 0, "valorant": 0, "other": 0},
            "tier_one_league_ids": {"lol": [], "valorant": []},
            "sync": {
                "last_attempt_at": "",
                "last_success_at": "",
                "last_error": "",
                "last_summary": "",
            },
        }

    def _normalize_esports_store(self, raw: Any) -> Dict[str, Any]:
        store = self._new_esports_store()
        if not isinstance(raw, dict):
            return store

        raw_matches = raw.get("matches", {})
        if isinstance(raw_matches, dict):
            for raw_id, raw_match in raw_matches.items():
                if not isinstance(raw_match, dict):
                    continue
                normalized = self._normalize_esports_match_record(raw_match, raw_id)
                if normalized:
                    store["matches"][normalized["id"]] = normalized

        raw_sequences = raw.get("display_sequences")
        if isinstance(raw_sequences, dict):
            for scope in store["display_sequences"]:
                store["display_sequences"][scope] = self._normalize_int(
                    raw_sequences.get(scope), 0, 0
                )
            self._refresh_match_display_sequences(
                store["matches"], store["display_sequences"]
            )
            for match in self._sorted_matches_for_display_id(store["matches"]):
                if not match.get("display_id"):
                    match["display_id"] = self._allocate_match_display_id(
                        match, store["matches"], store["display_sequences"]
                    )
        else:
            self._renumber_match_display_ids(
                store["matches"], store["display_sequences"]
            )

        raw_bets = raw.get("bets", {})
        if isinstance(raw_bets, dict):
            for raw_key, raw_bet in raw_bets.items():
                if not isinstance(raw_bet, dict):
                    continue
                match_id = str(raw_bet.get("match_id", "") or "").strip()
                user_id = str(raw_bet.get("user_id", "") or "").strip()
                if not match_id or not user_id:
                    continue
                key = self._bet_key(match_id, user_id)
                history = raw_bet.get("history", [])
                store["bets"][key] = {
                    "id": str(raw_bet.get("id", raw_key) or raw_key).strip(),
                    "match_id": match_id,
                    "user_id": user_id,
                    "team_id": str(raw_bet.get("team_id", "") or "").strip(),
                    "team_name": str(raw_bet.get("team_name", "") or "").strip()[:80],
                    "amount": self._normalize_int(raw_bet.get("amount"), 0, 0),
                    "odds": self._normalize_float(raw_bet.get("odds"), 1.0, 0.0),
                    "possible_payout": self._normalize_int(raw_bet.get("possible_payout"), 0, 0),
                    "status": str(raw_bet.get("status", "pending") or "pending").strip().lower(),
                    "payout": self._normalize_int(raw_bet.get("payout"), 0, 0),
                    "profit": self._normalize_signed_int(raw_bet.get("profit"), 0),
                    "source_group_id": str(raw_bet.get("source_group_id", "") or "").strip(),
                    "placed_at": str(raw_bet.get("placed_at", "") or "").strip()[:40],
                    "updated_at": str(raw_bet.get("updated_at", "") or "").strip()[:40],
                    "settled_at": str(raw_bet.get("settled_at", "") or "").strip()[:40],
                    "history": history[-30:] if isinstance(history, list) else [],
                }

        raw_ratings = raw.get("ratings", {})
        if isinstance(raw_ratings, dict):
            for raw_key, raw_rating in raw_ratings.items():
                if not isinstance(raw_rating, dict):
                    continue
                key = str(raw_key or "").strip()
                if not key:
                    continue
                store["ratings"][key] = {
                    "team_id": str(raw_rating.get("team_id", "") or "").strip(),
                    "team_name": str(raw_rating.get("team_name", "") or "").strip()[:80],
                    "game": str(raw_rating.get("game", "") or "").strip().lower()[:20],
                    "rating": self._normalize_float(raw_rating.get("rating"), 1500.0, 100.0),
                    "games": self._normalize_int(raw_rating.get("games"), 0, 0),
                    "wins": self._normalize_int(raw_rating.get("wins"), 0, 0),
                    "updated_at": str(raw_rating.get("updated_at", "") or "").strip()[:40],
                }

        processed = raw.get("rating_processed_match_ids", [])
        if isinstance(processed, list):
            store["rating_processed_match_ids"] = [
                str(item) for item in processed[-5000:] if str(item).strip()
            ]
        raw_league_ids = raw.get("tier_one_league_ids", {})
        if isinstance(raw_league_ids, dict):
            for game in store["tier_one_league_ids"]:
                values = raw_league_ids.get(game, [])
                if isinstance(values, list):
                    store["tier_one_league_ids"][game] = list(
                        dict.fromkeys(
                            str(item).strip()
                            for item in values
                            if str(item).strip()
                        )
                    )
        raw_sync = raw.get("sync", {})
        if isinstance(raw_sync, dict):
            for key in store["sync"]:
                store["sync"][key] = str(raw_sync.get(key, "") or "")[:500]
        return store

    def _normalize_esports_match_record(
        self, raw_match: Dict[str, Any], raw_id: Any
    ) -> Dict[str, Any] | None:
        match_id = str(raw_match.get("id", raw_id) or "").strip()
        teams = raw_match.get("teams", [])
        if not match_id or not isinstance(teams, list) or len(teams) != 2:
            return None
        normalized_teams = []
        for index, team in enumerate(teams):
            if not isinstance(team, dict):
                team = {}
            team_id = str(team.get("id", f"{match_id}:team{index + 1}") or "").strip()
            normalized_teams.append(
                {
                    "id": team_id or f"{match_id}:team{index + 1}",
                    "name": str(team.get("name", f"队伍{index + 1}") or "").strip()[:80],
                    "code": str(team.get("code", "") or "").strip()[:20],
                    "image_url": str(team.get("image_url", "") or "").strip()[:500],
                    "score": self._normalize_int(team.get("score"), 0, 0),
                }
            )
        odds = raw_match.get("odds", {})
        probabilities = raw_match.get("probabilities", {})
        normalized = {
            "id": match_id,
            "display_id": str(raw_match.get("display_id", "") or "").strip()[:24],
            "legacy_display_ids": [
                str(item).strip()[:24]
                for item in raw_match.get("legacy_display_ids", [])
                if str(item).strip()
            ][-10:]
            if isinstance(raw_match.get("legacy_display_ids", []), list)
            else [],
            "source": str(raw_match.get("source", "manual") or "manual").strip()[:40],
            "source_id": str(raw_match.get("source_id", "") or "").strip()[:80],
            "league_id": str(raw_match.get("league_id", "") or "").strip()[:40],
            "game": str(raw_match.get("game", "") or "").strip().lower()[:20],
            "competition": str(raw_match.get("competition", "") or "").strip()[:120],
            "stage": str(raw_match.get("stage", "") or "").strip()[:120],
            "name": str(raw_match.get("name", "") or "").strip()[:180],
            "start_time": str(raw_match.get("start_time", "") or "").strip()[:40],
            "end_time": str(raw_match.get("end_time", "") or "").strip()[:40],
            "status": str(raw_match.get("status", "not_started") or "not_started").strip().lower(),
            "teams": normalized_teams,
            "winner_id": str(raw_match.get("winner_id", "") or "").strip(),
            "odds": {
                normalized_teams[0]["id"]: self._normalize_float(
                    odds.get(normalized_teams[0]["id"], 1.9) if isinstance(odds, dict) else 1.9,
                    1.9,
                    1.01,
                ),
                normalized_teams[1]["id"]: self._normalize_float(
                    odds.get(normalized_teams[1]["id"], 1.9) if isinstance(odds, dict) else 1.9,
                    1.9,
                    1.01,
                ),
            },
            "probabilities": {
                normalized_teams[0]["id"]: self._normalize_float(
                    probabilities.get(normalized_teams[0]["id"], 0.5)
                    if isinstance(probabilities, dict)
                    else 0.5,
                    0.5,
                    0.0,
                ),
                normalized_teams[1]["id"]: self._normalize_float(
                    probabilities.get(normalized_teams[1]["id"], 0.5)
                    if isinstance(probabilities, dict)
                    else 0.5,
                    0.5,
                    0.0,
                ),
            },
            "odds_locked": bool(raw_match.get("odds_locked", False)),
            "visible": bool(raw_match.get("visible", True)),
            "settled_at": str(raw_match.get("settled_at", "") or "").strip()[:40],
            "created_at": str(raw_match.get("created_at", "") or "").strip()[:40],
            "updated_at": str(raw_match.get("updated_at", "") or "").strip()[:40],
        }
        return normalized

    def _get_esports_settings(self) -> Dict[str, Any]:
        raw = self.config.get("esports_prediction_settings", {})
        if not isinstance(raw, dict):
            raw = {}
        games = raw.get("games", ["lol", "valorant"])
        if isinstance(games, str):
            games = re.split(r"[,，\s]+", games)
        normalized_games = [
            str(item).strip().lower()
            for item in (games if isinstance(games, list) else [])
            if str(item).strip().lower() in {"lol", "valorant"}
        ]
        min_bet = self._normalize_int(raw.get("min_bet"), 10, 1)
        max_bet = max(self._normalize_int(raw.get("max_bet"), 10000, 1), min_bet)
        close_before_minutes = self._normalize_int(
            raw.get("close_before_minutes"), 30, 0
        )
        switch_deadline_minutes = max(
            self._normalize_int(raw.get("switch_deadline_minutes"), 60, 1),
            close_before_minutes,
        )
        return {
            "enabled": bool(raw.get("enabled", True)),
            "sync_enabled": bool(raw.get("sync_enabled", True)),
            "provider": str(raw.get("provider", "pandascore") or "pandascore").strip().lower(),
            "pandascore_token": str(raw.get("pandascore_token", "") or "").strip(),
            "games": normalized_games or ["lol", "valorant"],
            "sync_interval_minutes": self._normalize_int(
                raw.get("sync_interval_minutes"), 10, 2
            ),
            "request_timeout_seconds": self._normalize_int(
                raw.get("request_timeout_seconds"), 20, 5
            ),
            "min_bet": min_bet,
            "max_bet": max_bet,
            "switch_deadline_minutes": switch_deadline_minutes,
            "close_before_minutes": close_before_minutes,
            "elo_k_factor": self._normalize_float(raw.get("elo_k_factor"), 32.0, 1.0),
            "odds_margin": min(
                self._normalize_float(raw.get("odds_margin"), 0.05, 0.0), 0.3
            ),
            "min_probability": min(
                max(self._normalize_float(raw.get("min_probability"), 0.08, 0.01), 0.01),
                0.49,
            ),
            "max_odds": max(
                self._normalize_float(raw.get("max_odds"), 10.0, 1.1), 1.1
            ),
            "timezone_offset_hours": max(
                -12, min(self._normalize_signed_int(raw.get("timezone_offset_hours"), 8), 14)
            ),
            "leaderboard_limit": self._normalize_int(raw.get("leaderboard_limit"), 10, 1),
            "hit_rate_min_bets": self._normalize_int(raw.get("hit_rate_min_bets"), 3, 1),
        }

    @staticmethod
    def _utcnow() -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def _parse_esports_datetime(value: Any) -> datetime.datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            result = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=datetime.timezone.utc)
        return result.astimezone(datetime.timezone.utc)

    def _format_esports_time(self, value: Any) -> str:
        parsed = self._parse_esports_datetime(value)
        if parsed is None:
            return "时间待定"
        offset = self._get_esports_settings()["timezone_offset_hours"]
        local = parsed.astimezone(datetime.timezone(datetime.timedelta(hours=offset)))
        return local.strftime("%m-%d %H:%M")

    @staticmethod
    def _bet_key(match_id: str, user_id: str) -> str:
        return f"{match_id}|{user_id}"

    def _get_esports_store(self) -> Dict[str, Any]:
        raw = self.data.setdefault("esports", self._new_esports_store())
        if not isinstance(raw, dict):
            raw = self._new_esports_store()
            self.data["esports"] = raw
        return raw

    @staticmethod
    def _match_display_scope(match: Dict[str, Any]) -> str:
        game = str(match.get("game", "") or "").strip().lower()
        return game if game in {"lol", "valorant"} else "other"

    @staticmethod
    def _match_display_prefix(scope: str) -> str:
        return {"lol": "L", "valorant": "V"}.get(scope, "M")

    @staticmethod
    def _sorted_matches_for_display_id(matches: Dict[str, Any]) -> list[Dict[str, Any]]:
        return sorted(
            (item for item in matches.values() if isinstance(item, dict)),
            key=lambda item: (
                str(item.get("start_time", "") or ""),
                str(item.get("created_at", "") or ""),
                str(item.get("id", "") or ""),
            ),
        )

    def _refresh_match_display_sequences(
        self, matches: Dict[str, Any], sequences: Dict[str, int]
    ) -> None:
        for match in matches.values():
            if not isinstance(match, dict):
                continue
            scope = self._match_display_scope(match)
            prefix = self._match_display_prefix(scope)
            display_id = str(match.get("display_id", "") or "").strip().upper()
            match_id = re.fullmatch(rf"{re.escape(prefix)}(\d+)", display_id)
            if match_id:
                sequences[scope] = max(
                    self._normalize_int(sequences.get(scope), 0, 0),
                    int(match_id.group(1)),
                )

    def _allocate_match_display_id(
        self,
        match: Dict[str, Any],
        matches: Dict[str, Any],
        sequences: Dict[str, int],
    ) -> str:
        scope = self._match_display_scope(match)
        prefix = self._match_display_prefix(scope)
        used = {
            str(item.get("display_id", "") or "").strip().upper()
            for item in matches.values()
            if isinstance(item, dict) and item is not match
        }
        number = self._normalize_int(sequences.get(scope), 0, 0) + 1
        candidate = f"{prefix}{number:03d}"
        while candidate in used:
            number += 1
            candidate = f"{prefix}{number:03d}"
        sequences[scope] = number
        return candidate

    def _renumber_match_display_ids(
        self, matches: Dict[str, Any], sequences: Dict[str, int]
    ) -> None:
        ordered = self._sorted_matches_for_display_id(matches)
        for match in ordered:
            old_id = str(match.get("display_id", "") or "").strip().upper()
            legacy_ids = match.setdefault("legacy_display_ids", [])
            if old_id and old_id not in legacy_ids:
                legacy_ids.append(old_id)
            match["display_id"] = ""
        for scope in sequences:
            sequences[scope] = 0
        for match in ordered:
            match["display_id"] = self._allocate_match_display_id(
                match, matches, sequences
            )
            match["legacy_display_ids"] = [
                item
                for item in match.get("legacy_display_ids", [])[-10:]
                if str(item).upper() != match["display_id"]
            ]

    def _ensure_unique_display_id(
        self, match: Dict[str, Any], matches: Dict[str, Any]
    ) -> str:
        esports = self._get_esports_store()
        sequences = esports.setdefault(
            "display_sequences", {"lol": 0, "valorant": 0, "other": 0}
        )
        return self._allocate_match_display_id(match, matches, sequences)

    @staticmethod
    def _pandascore_team(raw: Any, fallback: str) -> Dict[str, Any]:
        item = raw if isinstance(raw, dict) else {}
        opponent = item.get("opponent", item)
        if not isinstance(opponent, dict):
            opponent = {}
        team_id = str(opponent.get("id", "") or "").strip()
        return {
            "id": team_id or fallback,
            "name": str(opponent.get("name", fallback) or fallback).strip()[:80],
            "code": str(opponent.get("acronym", "") or "").strip()[:20],
            "image_url": str(opponent.get("image_url", "") or "").strip()[:500],
            "score": 0,
        }

    def _normalize_pandascore_match(self, game: str, raw: Dict[str, Any]) -> Dict[str, Any] | None:
        source_id = str(raw.get("id", "") or "").strip()
        opponents = raw.get("opponents", [])
        if not source_id or not isinstance(opponents, list) or len(opponents) != 2:
            return None
        teams = [
            self._pandascore_team(opponents[0], f"{source_id}:team1"),
            self._pandascore_team(opponents[1], f"{source_id}:team2"),
        ]
        if any(team["name"].casefold() in {"tbd", "to be determined"} for team in teams):
            return None
        score_by_team: Dict[str, int] = {}
        raw_results = raw.get("results", [])
        if isinstance(raw_results, list):
            for result in raw_results:
                if isinstance(result, dict):
                    score_by_team[str(result.get("team_id", ""))] = self._normalize_int(
                        result.get("score"), 0, 0
                    )
        for team in teams:
            team["score"] = score_by_team.get(team["id"], 0)

        league = raw.get("league", {}) if isinstance(raw.get("league"), dict) else {}
        tournament = raw.get("tournament", {}) if isinstance(raw.get("tournament"), dict) else {}
        serie = raw.get("serie", {}) if isinstance(raw.get("serie"), dict) else {}
        competition_parts = [
            str(league.get("name", "") or "").strip(),
            str(serie.get("full_name", serie.get("name", "")) or "").strip(),
            str(tournament.get("name", "") or "").strip(),
        ]
        competition = " · ".join(dict.fromkeys(item for item in competition_parts if item))
        status_map = {
            "not_started": "not_started",
            "running": "running",
            "finished": "finished",
            "canceled": "canceled",
            "cancelled": "canceled",
            "postponed": "postponed",
        }
        raw_status = str(raw.get("status", "not_started") or "not_started").strip().lower()
        winner_id = str(raw.get("winner_id", "") or "").strip()
        winner = raw.get("winner")
        if not winner_id and isinstance(winner, dict):
            winner_id = str(winner.get("id", "") or "").strip()
        match_id = f"pandascore:{game}:{source_id}"
        return {
            "id": match_id,
            "display_id": "",
            "source": "pandascore",
            "source_id": source_id,
            "league_id": str(league.get("id", "") or "").strip(),
            "game": game,
            "competition": competition or "未命名赛事",
            "stage": str(raw.get("name", "") or "").strip()[:120],
            "name": f"{teams[0]['name']} vs {teams[1]['name']}",
            "start_time": str(raw.get("begin_at", raw.get("scheduled_at", "")) or "").strip(),
            "end_time": str(raw.get("end_at", "") or "").strip(),
            "status": status_map.get(raw_status, raw_status),
            "teams": teams,
            "winner_id": winner_id,
            "odds": {},
            "probabilities": {},
            "odds_locked": False,
            "visible": True,
            "settled_at": "",
            "created_at": self._utcnow().isoformat(timespec="seconds"),
            "updated_at": self._utcnow().isoformat(timespec="seconds"),
            "_filter_text": " ".join(
                [
                    str(league.get("slug", "") or ""),
                    str(league.get("name", "") or ""),
                    str(serie.get("slug", "") or ""),
                    str(serie.get("name", "") or ""),
                    str(serie.get("full_name", "") or ""),
                    str(tournament.get("slug", "") or ""),
                    str(tournament.get("name", "") or ""),
                ]
            ).casefold(),
        }

    @staticmethod
    def _competition_filter_text(match: Dict[str, Any]) -> str:
        raw = str(match.get("_filter_text", match.get("competition", ""))).casefold()
        return re.sub(r"[^a-z0-9]+", " ", raw).strip()

    def _is_tier_one_match(self, match: Dict[str, Any]) -> bool:
        if str(match.get("source", "")).lower() == "manual":
            return True
        game = str(match.get("game", "")).lower()
        text = self._competition_filter_text(match)
        if not text or game not in TIER_ONE_EXCLUSIONS:
            return False
        if any(token in text for token in TIER_ONE_EXCLUSIONS[game]):
            return False
        if game == "lol":
            return bool(
                re.search(
                    r"\b(?:lpl|lck|league of legends pro league|"
                    r"league of legends champions korea|first stand|fst|"
                    r"mid season invitational|msi|world championship|worlds)\b",
                    text,
                )
            )
        has_vct = re.search(r"\b(?:vct|valorant champions tour)\b", text)
        has_main_scope = re.search(
            r"\b(?:americas|emea|pacific|china|cn|masters|champions)\b",
            text,
        )
        standalone_global = re.search(
            r"\bvalorant (?:masters|champions)(?! tour)\b", text
        )
        return bool((has_vct and has_main_scope) or standalone_global)

    def _is_tier_one_league(self, game: str, league: Dict[str, Any]) -> bool:
        text = self._competition_filter_text(
            {
                "_filter_text": " ".join(
                    [
                        str(league.get("slug", "") or ""),
                        str(league.get("name", "") or ""),
                    ]
                )
            }
        )
        if not text or any(token in text for token in TIER_ONE_EXCLUSIONS.get(game, ())):
            return False
        if game == "lol":
            return bool(
                re.search(
                    r"\b(?:lpl|lck|league of legends pro league|"
                    r"league of legends champions korea|first stand|fst|"
                    r"mid season invitational|msi|world championship|worlds)\b",
                    text,
                )
            )
        if game == "valorant":
            return bool(
                re.search(r"\b(?:vct|valorant champions tour)\b", text)
                or re.search(r"\bvalorant (?:masters|champions)\b", text)
            )
        return False

    def _is_recent_match_result(
        self, match: Dict[str, Any], now: datetime.datetime | None = None
    ) -> bool:
        if str(match.get("status", "")).lower() not in TERMINAL_MATCH_STATUSES:
            return True
        reference = next(
            (
                parsed
                for parsed in (
                    self._parse_esports_datetime(match.get("settled_at")),
                    self._parse_esports_datetime(match.get("end_time")),
                    self._parse_esports_datetime(match.get("start_time")),
                    self._parse_esports_datetime(match.get("updated_at")),
                )
                if parsed is not None
            ),
            None,
        )
        if reference is None:
            return False
        cutoff = (now or self._utcnow()) - datetime.timedelta(
            hours=MATCH_RESULT_RETENTION_HOURS
        )
        return reference >= cutoff

    @staticmethod
    def _rating_key(game: str, team_id: str) -> str:
        return f"{game}:{team_id}"

    def _get_team_rating_locked(self, game: str, team: Dict[str, Any]) -> Dict[str, Any]:
        esports = self._get_esports_store()
        ratings = esports.setdefault("ratings", {})
        key = self._rating_key(game, str(team.get("id", "")))
        rating = ratings.get(key)
        if not isinstance(rating, dict):
            rating = {
                "team_id": str(team.get("id", "")),
                "team_name": str(team.get("name", "")),
                "game": game,
                "rating": 1500.0,
                "games": 0,
                "wins": 0,
                "updated_at": "",
            }
            ratings[key] = rating
        elif team.get("name"):
            rating["team_name"] = str(team["name"])
        return rating

    def _calculate_match_odds_locked(self, match: Dict[str, Any]) -> None:
        teams = match.get("teams", [])
        if not isinstance(teams, list) or len(teams) != 2:
            return
        settings = self._get_esports_settings()
        first = self._get_team_rating_locked(match.get("game", ""), teams[0])
        second = self._get_team_rating_locked(match.get("game", ""), teams[1])
        rating_a = float(first.get("rating", 1500.0))
        rating_b = float(second.get("rating", 1500.0))
        probability_a = 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))
        games_a = self._normalize_int(first.get("games"), 0, 0)
        games_b = self._normalize_int(second.get("games"), 0, 0)
        confidence = min(
            1.0,
            math.sqrt(
                (games_a + ELO_CONFIDENCE_PRIOR_GAMES)
                * (games_b + ELO_CONFIDENCE_PRIOR_GAMES)
            )
            / ELO_CONFIDENCE_FULL_GAMES,
        )
        probability_a = 0.5 + (probability_a - 0.5) * confidence
        min_probability = settings["min_probability"]
        probability_a = min(max(probability_a, min_probability), 1.0 - min_probability)
        probability_b = 1.0 - probability_a
        margin_factor = 1.0 + settings["odds_margin"]
        max_odds = settings["max_odds"]
        odds_a = min(max(1.01, 1.0 / (probability_a * margin_factor)), max_odds)
        odds_b = min(max(1.01, 1.0 / (probability_b * margin_factor)), max_odds)
        match["probabilities"] = {
            teams[0]["id"]: round(probability_a, 4),
            teams[1]["id"]: round(probability_b, 4),
        }
        match["odds"] = {
            teams[0]["id"]: round(odds_a, 2),
            teams[1]["id"]: round(odds_b, 2),
        }

    def _apply_rating_result_locked(self, match: Dict[str, Any]) -> bool:
        winner_id = str(match.get("winner_id", "") or "")
        teams = match.get("teams", [])
        if not winner_id or not isinstance(teams, list) or len(teams) != 2:
            return False
        match_id = str(match.get("id", ""))
        esports = self._get_esports_store()
        processed = esports.setdefault("rating_processed_match_ids", [])
        if match_id in processed:
            return False
        team_ids = {str(team.get("id", "")) for team in teams}
        if winner_id not in team_ids:
            return False
        ratings = [
            self._get_team_rating_locked(str(match.get("game", "")), team)
            for team in teams
        ]
        rating_a = float(ratings[0].get("rating", 1500.0))
        rating_b = float(ratings[1].get("rating", 1500.0))
        expected_a = 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))
        actual_a = 1.0 if str(teams[0].get("id")) == winner_id else 0.0
        k_factor = self._get_esports_settings()["elo_k_factor"]
        now = self._utcnow().isoformat(timespec="seconds")
        ratings[0]["rating"] = round(rating_a + k_factor * (actual_a - expected_a), 2)
        ratings[1]["rating"] = round(rating_b + k_factor * ((1.0 - actual_a) - (1.0 - expected_a)), 2)
        for index, rating in enumerate(ratings):
            rating["games"] = self._normalize_int(rating.get("games"), 0, 0) + 1
            if str(teams[index].get("id")) == winner_id:
                rating["wins"] = self._normalize_int(rating.get("wins"), 0, 0) + 1
            rating["updated_at"] = now
        processed.append(match_id)
        esports["rating_processed_match_ids"] = processed[-5000:]
        return True

    def _upsert_synced_match_locked(self, incoming: Dict[str, Any]) -> tuple[bool, bool]:
        esports = self._get_esports_store()
        matches = esports.setdefault("matches", {})
        match_id = incoming["id"]
        existing = matches.get(match_id)
        created = not isinstance(existing, dict)
        if created:
            incoming["display_id"] = self._ensure_unique_display_id(incoming, matches)
            self._calculate_match_odds_locked(incoming)
            incoming.pop("_filter_text", None)
            matches[match_id] = incoming
            return True, True

        preserved = {
            "display_id": existing.get("display_id", ""),
            "legacy_display_ids": existing.get("legacy_display_ids", []),
            "odds": existing.get("odds", {}),
            "probabilities": existing.get("probabilities", {}),
            "odds_locked": bool(existing.get("odds_locked", False)),
            "visible": bool(existing.get("visible", True)),
            "created_at": existing.get("created_at", incoming.get("created_at", "")),
            "settled_at": existing.get("settled_at", ""),
        }
        incoming.update(preserved)
        incoming.pop("_filter_text", None)
        if not incoming["odds_locked"]:
            self._calculate_match_odds_locked(incoming)
        changed = incoming != existing
        matches[match_id] = incoming
        return changed, False

    def _resolve_match_locked(self, token: str) -> Dict[str, Any] | None:
        target = str(token or "").strip().casefold()
        if not target:
            return None
        matches = self._get_esports_store().setdefault("matches", {})
        direct = matches.get(str(token).strip())
        if isinstance(direct, dict):
            return direct
        candidates = []
        for match in matches.values():
            if not isinstance(match, dict):
                continue
            values = {
                str(match.get("display_id", "")).casefold(),
                str(match.get("source_id", "")).casefold(),
                *(
                    str(item).casefold()
                    for item in match.get("legacy_display_ids", [])
                    if str(item).strip()
                ),
            }
            if target in values:
                return match
            if target and target in str(match.get("name", "")).casefold():
                candidates.append(match)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _resolve_match_team(match: Dict[str, Any], token: str) -> Dict[str, Any] | None:
        teams = match.get("teams", [])
        if not isinstance(teams, list) or len(teams) != 2:
            return None
        target = str(token or "").strip().casefold()
        if target in {"1", "a", "主队", "左"}:
            return teams[0]
        if target in {"2", "b", "客队", "右"}:
            return teams[1]
        exact = [
            team
            for team in teams
            if target
            in {
                str(team.get("id", "")).casefold(),
                str(team.get("name", "")).casefold(),
                str(team.get("code", "")).casefold(),
            }
        ]
        if len(exact) == 1:
            return exact[0]
        partial = [
            team
            for team in teams
            if target and target in str(team.get("name", "")).casefold()
        ]
        return partial[0] if len(partial) == 1 else None

    @staticmethod
    def _team_display_name(team: Dict[str, Any] | None) -> str:
        if not isinstance(team, dict):
            return "未知队伍"
        code = str(team.get("code", "") or "").strip()
        name = str(team.get("name", "") or "").strip()
        return code or name or "未知队伍"

    def _match_deadlines(self, match: Dict[str, Any]) -> tuple[datetime.datetime | None, datetime.datetime | None]:
        start = self._parse_esports_datetime(match.get("start_time"))
        if start is None:
            return None, None
        settings = self._get_esports_settings()
        switch_deadline = start - datetime.timedelta(minutes=settings["switch_deadline_minutes"])
        close_deadline = start - datetime.timedelta(minutes=settings["close_before_minutes"])
        return switch_deadline, close_deadline

    def _match_pool_locked(self, match_id: str) -> Dict[str, int]:
        pool: Dict[str, int] = {}
        for bet in self._get_esports_store().setdefault("bets", {}).values():
            if not isinstance(bet, dict) or bet.get("match_id") != match_id or bet.get("status") != "pending":
                continue
            team_id = str(bet.get("team_id", ""))
            pool[team_id] = pool.get(team_id, 0) + self._normalize_int(bet.get("amount"), 0, 0)
        return pool

    def _refund_match_locked(self, match: Dict[str, Any], reason: str) -> tuple[int, int]:
        refunded_users = 0
        refunded_points = 0
        now = self._utcnow().isoformat(timespec="seconds")
        bets = self._get_esports_store().setdefault("bets", {})
        for bet in bets.values():
            if not isinstance(bet, dict) or bet.get("match_id") != match.get("id") or bet.get("status") != "pending":
                continue
            amount = self._normalize_int(bet.get("amount"), 0, 0)
            user = self._get_user_record(str(bet.get("user_id", "")))
            user["points"] += amount
            bet["status"] = "refunded"
            bet["payout"] = amount
            bet["profit"] = 0
            bet["settled_at"] = now
            bet["updated_at"] = now
            bet.setdefault("history", []).append({"action": "refund", "at": now, "reason": reason})
            bet["history"] = bet["history"][-30:]
            refunded_users += 1
            refunded_points += amount
        match["status"] = "refunded"
        match["settled_at"] = now
        match["updated_at"] = now
        return refunded_users, refunded_points

    def _settle_match_locked(self, match: Dict[str, Any]) -> tuple[int, int, int]:
        winner_id = str(match.get("winner_id", "") or "")
        team_ids = {str(team.get("id", "")) for team in match.get("teams", []) if isinstance(team, dict)}
        if not winner_id or winner_id not in team_ids:
            return 0, 0, 0
        winners = 0
        settled = 0
        paid = 0
        now = self._utcnow().isoformat(timespec="seconds")
        for bet in self._get_esports_store().setdefault("bets", {}).values():
            if not isinstance(bet, dict) or bet.get("match_id") != match.get("id") or bet.get("status") != "pending":
                continue
            amount = self._normalize_int(bet.get("amount"), 0, 0)
            if str(bet.get("team_id", "")) == winner_id:
                payout = max(amount, int(math.floor(amount * float(bet.get("odds", 1.0)))))
                self._get_user_record(str(bet.get("user_id", "")))["points"] += payout
                bet["status"] = "won"
                bet["payout"] = payout
                bet["profit"] = payout - amount
                winners += 1
                paid += payout
            else:
                bet["status"] = "lost"
                bet["payout"] = 0
                bet["profit"] = -amount
            bet["settled_at"] = now
            bet["updated_at"] = now
            bet.setdefault("history", []).append({"action": "settle", "at": now, "winner_id": winner_id})
            bet["history"] = bet["history"][-30:]
            settled += 1
        match["status"] = "settled"
        match["settled_at"] = now
        match["updated_at"] = now
        return settled, winners, paid

    def _settle_ready_matches_locked(self) -> tuple[int, int]:
        settled_matches = 0
        refunded_matches = 0
        for match in self._get_esports_store().setdefault("matches", {}).values():
            if not isinstance(match, dict) or match.get("settled_at"):
                continue
            status = str(match.get("status", "")).lower()
            if status in REFUND_MATCH_STATUSES:
                self._refund_match_locked(match, f"数据源状态：{status}")
                refunded_matches += 1
            elif status == "finished" and match.get("winner_id"):
                self._settle_match_locked(match)
                settled_matches += 1
        return settled_matches, refunded_matches

    async def _sync_esports_once(self, reason: str = "自动同步") -> Dict[str, Any]:
        settings = self._get_esports_settings()
        if not settings["enabled"] or not settings["sync_enabled"]:
            raise EsportsProviderError("竞猜同步功能当前未启用")
        if settings["provider"] != "pandascore":
            raise EsportsProviderError(f"暂不支持的数据源：{settings['provider']}")
        provider = PandaScoreProvider(
            settings["pandascore_token"],
            timeout_seconds=settings["request_timeout_seconds"],
        )
        attempt_at = self._utcnow().isoformat(timespec="seconds")
        fetched: list[Dict[str, Any]] = []
        errors: list[str] = []
        target_league_ids: Dict[str, set[str]] = {
            game: set() for game in settings["games"]
        }
        async with self._data_lock:
            esports = self._get_esports_store()
            cached_league_ids = esports.setdefault(
                "tier_one_league_ids", {"lol": [], "valorant": []}
            )
            for game in target_league_ids:
                target_league_ids[game].update(
                    str(item).strip()
                    for item in cached_league_ids.get(game, [])
                    if str(item).strip()
                )
            for stored_match in esports.setdefault("matches", {}).values():
                if not isinstance(stored_match, dict) or not self._is_tier_one_match(stored_match):
                    continue
                game = str(stored_match.get("game", "") or "").lower()
                league_id = str(stored_match.get("league_id", "") or "").strip()
                if game in target_league_ids and league_id:
                    target_league_ids[game].add(league_id)

        discovery_requests = [
            (game, provider.fetch_leagues(game)) for game in target_league_ids
        ]
        discovery_responses = await asyncio.gather(
            *(item[1] for item in discovery_requests), return_exceptions=True
        )
        for (game, _), response in zip(discovery_requests, discovery_responses):
            if isinstance(response, BaseException):
                errors.append(f"{game}/leagues: {response}")
                continue
            if not isinstance(response, list):
                errors.append(f"{game}/leagues: 数据格式不符合预期")
                continue
            discovered_ids = {
                str(league.get("id", "") or "").strip()
                for league in response
                if isinstance(league, dict)
                and self._is_tier_one_league(game, league)
                and str(league.get("id", "") or "").strip()
            }
            if discovered_ids:
                target_league_ids[game] = discovered_ids

        requests = []
        for game, league_ids in target_league_ids.items():
            if not league_ids:
                errors.append(f"{game}/leagues: 未找到允许的赛事")
                continue
            allowed_ids = tuple(sorted(league_ids))
            for state in ("past", "running", "upcoming"):
                requests.append(
                    (
                        game,
                        state,
                        provider.fetch_matches(
                            game,
                            state,
                            pages=3 if state == "past" else 2 if state == "upcoming" else 1,
                            league_ids=allowed_ids,
                        ),
                    )
                )
        responses = await asyncio.gather(
            *(item[2] for item in requests), return_exceptions=True
        )
        for (game, state, _), response in zip(requests, responses):
            if isinstance(response, BaseException):
                errors.append(f"{game}/{state}: {response}")
                continue
            raw_matches = response
            if not isinstance(raw_matches, list):
                errors.append(f"{game}/{state}: 数据格式不符合预期")
                continue
            for raw_match in raw_matches:
                normalized = self._normalize_pandascore_match(game, raw_match)
                if normalized:
                    fetched.append(normalized)

        if not fetched and errors:
            async with self._data_lock:
                sync = self._get_esports_store().setdefault("sync", {})
                sync["last_attempt_at"] = attempt_at
                sync["last_error"] = "；".join(errors)[:500]
                await self._save_data_locked()
            raise EsportsProviderError(sync["last_error"])

        unique = {item["id"]: item for item in fetched}
        ordered = sorted(
            unique.values(),
            key=lambda item: self._parse_esports_datetime(item.get("start_time"))
            or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc),
        )
        created = 0
        updated = 0
        rating_updates = 0
        ignored = 0
        now = self._utcnow()
        async with self._data_lock:
            esports = self._get_esports_store()
            esports["tier_one_league_ids"] = {
                game: sorted(league_ids)
                for game, league_ids in target_league_ids.items()
            }
            matches = esports.setdefault("matches", {})
            for match in ordered:
                match_id = match["id"]
                existing = matches.get(match_id)
                if not self._is_tier_one_match(match):
                    ignored += 1
                    if isinstance(existing, dict):
                        changed, _ = self._upsert_synced_match_locked(match)
                        stored_match = matches[match_id]
                        if stored_match.get("visible", True):
                            stored_match["visible"] = False
                            changed = True
                        updated += int(changed)
                    continue
                status = str(match.get("status", "")).lower()
                if status == "finished" and match.get("winner_id"):
                    if self._apply_rating_result_locked(match):
                        rating_updates += 1
                if status in REFUND_MATCH_STATUSES and not isinstance(existing, dict):
                    ignored += 1
                    continue
                if (
                    status == "finished"
                    and not isinstance(existing, dict)
                    and not self._is_recent_match_result(match, now)
                ):
                    ignored += 1
                    continue
                changed, was_created = self._upsert_synced_match_locked(match)
                created += int(was_created)
                updated += int(changed and not was_created)
            settled, refunded = self._settle_ready_matches_locked()
            sync = self._get_esports_store().setdefault("sync", {})
            sync["last_attempt_at"] = attempt_at
            sync["last_success_at"] = self._utcnow().isoformat(timespec="seconds")
            sync["last_error"] = "；".join(errors)[:500]
            sync["last_summary"] = (
                f"读取 {len(unique)} 场，新增 {created}，更新 {updated}，"
                f"忽略 {ignored}，评分更新 {rating_updates}，结算 {settled}，退款 {refunded}"
            )
            await self._save_data_locked()
        logger.info(f"{reason}完成：{sync['last_summary']}")
        return {
            "fetched": len(unique),
            "created": created,
            "updated": updated,
            "ignored": ignored,
            "rating_updates": rating_updates,
            "settled": settled,
            "refunded": refunded,
            "errors": errors,
            "summary": sync["last_summary"],
        }

    async def _esports_sync_loop(self) -> None:
        try:
            await asyncio.wait_for(self._esports_stop_event.wait(), timeout=10)
            return
        except asyncio.TimeoutError:
            pass
        while not self._esports_stop_event.is_set():
            settings = self._get_esports_settings()
            if settings["enabled"] and settings["sync_enabled"] and settings["pandascore_token"]:
                try:
                    await self._sync_esports_once()
                except Exception as exc:
                    logger.warning(f"赛事自动同步失败：{exc}")
            wait_seconds = max(settings["sync_interval_minutes"] * 60, 120)
            try:
                await asyncio.wait_for(self._esports_stop_event.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                continue

    def _visible_upcoming_matches_locked(self) -> list[Dict[str, Any]]:
        now = self._utcnow()
        result = []
        for match in self._get_esports_store().setdefault("matches", {}).values():
            if not isinstance(match, dict) or not match.get("visible", True):
                continue
            if not self._is_tier_one_match(match):
                continue
            start = self._parse_esports_datetime(match.get("start_time"))
            if start is None:
                continue
            _, close_deadline = self._match_deadlines(match)
            if (
                match.get("status") not in OPEN_MATCH_STATUSES
                or close_deadline is None
                or now >= close_deadline
            ):
                continue
            result.append(match)
        return sorted(result, key=lambda item: self._parse_esports_datetime(item.get("start_time")))

    def _format_match_line_locked(self, match: Dict[str, Any]) -> str:
        teams = match.get("teams", [])
        if len(teams) != 2:
            return ""
        odds = match.get("odds", {})
        first_name = self._team_display_name(teams[0])
        second_name = self._team_display_name(teams[1])
        game_label = {
            "lol": "LoL",
            "valorant": "VALORANT",
        }.get(str(match.get("game", "")).lower(), "电竞")
        return "\n".join(
            [
                f"【{match.get('display_id')}｜{game_label}】",
                f"时间：{self._format_esports_time(match.get('start_time'))}",
                f"赛事：{match.get('competition') or '待定'}",
                f"对阵：{first_name} {float(odds.get(teams[0]['id'], 1.0)):.2f} "
                f"vs {second_name} {float(odds.get(teams[1]['id'], 1.0)):.2f}",
            ]
        )

    def _select_matches_for_display(
        self, matches: list[Dict[str, Any]], limit: int = 10
    ) -> list[Dict[str, Any]]:
        if len(matches) <= limit:
            return matches
        grouped: Dict[str, list[Dict[str, Any]]] = {}
        for match in matches:
            game = str(match.get("game", "") or "other").lower()
            grouped.setdefault(game, []).append(match)
        if len(grouped) <= 1:
            return matches[:limit]

        quota = max(1, limit // len(grouped))
        selected = [match for group in grouped.values() for match in group[:quota]]
        selected_ids = {str(match.get("id", "")) for match in selected}
        remaining = [
            match
            for match in matches
            if str(match.get("id", "")) not in selected_ids
        ]
        selected.extend(remaining[: max(0, limit - len(selected))])
        selected = selected[:limit]
        return sorted(
            selected,
            key=lambda item: self._parse_esports_datetime(item.get("start_time"))
            or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc),
        )

    def _esports_help_message(self) -> str:
        return "\n".join(
            [
                "赛事竞猜使用方法",
                "",
                "查看比赛：/今日赛事",
                "参与竞猜：/竞猜 L001 TES 100",
                "追加同队：再次输入相同竞猜指令",
                "改选队伍：/改选 L001 BLG",
                "撤销竞猜：/撤销竞猜 L001",
                "查看记录：/我的竞猜",
                "查看排行：/竞猜排行",
                "查看规则：/竞猜规则",
                "",
                "积分相关",
                "签到：/群聊签到",
                "查看积分：/我的积分",
                "积分排行：/积分榜",
                "积分规则：/积分规则",
            ]
        )

    async def esports_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._esports_help_message())

    async def esports_matches(self, event: AstrMessageEvent):
        settings = self._get_esports_settings()
        if not settings["enabled"]:
            yield self._plain_result(event, "当前未开启电竞竞猜功能。")
            return
        async with self._data_lock:
            matches = self._visible_upcoming_matches_locked()
            offset = datetime.timezone(datetime.timedelta(hours=settings["timezone_offset_hours"]))
            today = self._utcnow().astimezone(offset).date()
            today_matches = [
                item
                for item in matches
                if self._parse_esports_datetime(item.get("start_time")).astimezone(offset).date() == today
            ]
            selected = self._select_matches_for_display(matches)
            blocks = [self._format_match_line_locked(item) for item in selected]
        if not blocks:
            yield self._plain_result(event, "当前没有已收录的待竞猜赛事。管理员可以先同步或手动添加比赛。")
            return
        title = "今日及近期可竞猜赛事" if today_matches else "近期可竞猜赛事"
        example_id = selected[0].get("display_id", "编号")
        message = (
            f"{title}（{len(blocks)} 场）\n\n"
            + "\n\n".join(blocks)
            + f"\n\n竞猜：/竞猜 {example_id} 战队缩写 积分"
        )
        yield event.plain_result(message)

    async def esports_bet(self, event: AstrMessageEvent):
        args = self._get_command_args(event).split()
        if len(args) < 3:
            yield event.plain_result(self._esports_help_message())
            return
        try:
            amount = int(args[-1])
        except ValueError:
            yield self._plain_result(event, "下注积分必须是整数。")
            return
        match_token = args[0]
        team_token = " ".join(args[1:-1])
        settings = self._get_esports_settings()
        if amount < settings["min_bet"] or amount > settings["max_bet"]:
            yield self._plain_result(
                event,
                f"每位用户在一场比赛的下注总额须在 {settings['min_bet']}～{settings['max_bet']} 之间。",
            )
            return
        user_id = str(event.get_sender_id())
        now = self._utcnow()
        async with self._data_lock:
            match = self._resolve_match_locked(match_token)
            if not match or not match.get("visible", True):
                message = "未找到该比赛，请先用 /今日赛事 查看编号。"
            else:
                team = self._resolve_match_team(match, team_token)
                _, close_deadline = self._match_deadlines(match)
                if not team:
                    message = "无法识别战队缩写，请使用赛事列表中显示的缩写（也兼容 1/2 或完整名称）。"
                elif match.get("status") not in OPEN_MATCH_STATUSES or close_deadline is None or now >= close_deadline:
                    message = "该比赛已经封盘，不能继续下注。"
                else:
                    bets = self._get_esports_store().setdefault("bets", {})
                    key = self._bet_key(match["id"], user_id)
                    existing = bets.get(key)
                    if isinstance(existing, dict) and existing.get("status") == "pending" and existing.get("team_id") != team["id"]:
                        message = "你已经选择了另一支队伍；需要改选时请使用 /改选 比赛编号 队伍。"
                    else:
                        current_amount = (
                            self._normalize_int(existing.get("amount"), 0, 0)
                            if isinstance(existing, dict) and existing.get("status") == "pending"
                            else 0
                        )
                        total_amount = current_amount + amount
                        user = self._get_user_record(user_id)
                        if total_amount > settings["max_bet"]:
                            message = f"本场累计下注不能超过 {settings['max_bet']} {self._get_points_name()}。"
                        elif user["points"] < amount:
                            message = f"{self._get_points_name()}不足，当前余额 {user['points']}。"
                        else:
                            match["odds_locked"] = True
                            selected_odds = float(match.get("odds", {}).get(team["id"], 1.0))
                            team_name = self._team_display_name(team)
                            timestamp = now.isoformat(timespec="seconds")
                            history = existing.get("history", []) if isinstance(existing, dict) else []
                            history.append({"action": "add" if current_amount else "place", "amount": amount, "team_id": team["id"], "at": timestamp})
                            bet = {
                                "id": str(existing.get("id")) if isinstance(existing, dict) and existing.get("id") else uuid.uuid4().hex,
                                "match_id": match["id"],
                                "user_id": user_id,
                                "team_id": team["id"],
                                "team_name": team_name,
                                "amount": total_amount,
                                "odds": selected_odds,
                                "possible_payout": int(math.floor(total_amount * selected_odds)),
                                "status": "pending",
                                "payout": 0,
                                "profit": 0,
                                "source_group_id": self._get_group_id(event) or "",
                                "placed_at": existing.get("placed_at", timestamp) if isinstance(existing, dict) else timestamp,
                                "updated_at": timestamp,
                                "settled_at": "",
                                "history": history[-30:],
                            }
                            bets[key] = bet
                            user["points"] -= amount
                            self._touch_group_member(event, user_id, self._get_sender_display_name(event))
                            await self._save_data_locked()
                            message = (
                                f"下注成功：{match['display_id']}｜{team_name}｜累计 {total_amount} {self._get_points_name()}｜"
                                f"倍率 {selected_odds:.2f}｜预计返还 {bet['possible_payout']}｜余额 {user['points']}。"
                            )
        yield self._plain_result(event, self._single_line_message(message))

    async def esports_switch_bet(self, event: AstrMessageEvent):
        args = self._get_command_args(event).split(maxsplit=1)
        if len(args) < 2:
            yield self._plain_result(event, "用法：/改选 比赛编号 战队缩写")
            return
        user_id = str(event.get_sender_id())
        async with self._data_lock:
            match = self._resolve_match_locked(args[0])
            team = self._resolve_match_team(match, args[1]) if match else None
            bet = (
                self._get_esports_store().setdefault("bets", {}).get(self._bet_key(match["id"], user_id))
                if match
                else None
            )
            switch_deadline, _ = self._match_deadlines(match) if match else (None, None)
            if not match or not team:
                message = "未找到比赛或队伍。"
            elif not isinstance(bet, dict) or bet.get("status") != "pending":
                message = "你在该场比赛没有可改选的下注。"
            elif match.get("status") not in OPEN_MATCH_STATUSES:
                message = "该比赛已经封盘，不能继续改选。"
            elif switch_deadline is None or self._utcnow() >= switch_deadline:
                message = "该比赛已超过改选截止时间。"
            elif bet.get("team_id") == team["id"]:
                message = "你当前已经选择这支队伍。"
            else:
                now = self._utcnow().isoformat(timespec="seconds")
                team_name = self._team_display_name(team)
                bet["team_id"] = team["id"]
                bet["team_name"] = team_name
                bet["odds"] = float(match.get("odds", {}).get(team["id"], 1.0))
                bet["possible_payout"] = int(math.floor(bet["amount"] * bet["odds"]))
                bet["updated_at"] = now
                bet.setdefault("history", []).append({"action": "switch", "team_id": team["id"], "at": now})
                bet["history"] = bet["history"][-30:]
                await self._save_data_locked()
                message = f"已改选为 {team_name}，本金 {bet['amount']}，倍率 {bet['odds']:.2f}。"
        yield self._plain_result(event, self._single_line_message(message))

    async def esports_cancel_bet(self, event: AstrMessageEvent):
        token = self._get_command_args(event).strip()
        user_id = str(event.get_sender_id())
        async with self._data_lock:
            match = self._resolve_match_locked(token)
            bet = (
                self._get_esports_store().setdefault("bets", {}).get(self._bet_key(match["id"], user_id))
                if match
                else None
            )
            switch_deadline, _ = self._match_deadlines(match) if match else (None, None)
            if not match or not isinstance(bet, dict) or bet.get("status") != "pending":
                message = "未找到可撤销的下注。"
            elif match.get("status") not in OPEN_MATCH_STATUSES:
                message = "该比赛已经封盘，不能撤单。"
            elif switch_deadline is None or self._utcnow() >= switch_deadline:
                message = "该比赛已超过撤单截止时间。"
            else:
                amount = self._normalize_int(bet.get("amount"), 0, 0)
                user = self._get_user_record(user_id)
                user["points"] += amount
                now = self._utcnow().isoformat(timespec="seconds")
                bet["status"] = "withdrawn"
                bet["payout"] = amount
                bet["profit"] = 0
                bet["updated_at"] = now
                bet["settled_at"] = now
                bet.setdefault("history", []).append({"action": "withdraw", "at": now})
                bet["history"] = bet["history"][-30:]
                await self._save_data_locked()
                message = f"撤单成功，已退还 {amount} {self._get_points_name()}，当前余额 {user['points']}。"
        yield self._plain_result(event, self._single_line_message(message))

    async def esports_my_bets(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        async with self._data_lock:
            esports = self._get_esports_store()
            matches = esports.setdefault("matches", {})
            bets = [
                bet
                for bet in esports.setdefault("bets", {}).values()
                if isinstance(bet, dict) and bet.get("user_id") == user_id
            ]
            bets.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
            lines = []
            status_names = {"pending": "待结算", "won": "命中", "lost": "未命中", "refunded": "已退款", "withdrawn": "已撤单"}
            for bet in bets[:10]:
                match = matches.get(bet.get("match_id"), {})
                team = next(
                    (
                        item
                        for item in match.get("teams", [])
                        if isinstance(item, dict)
                        and str(item.get("id", "")) == str(bet.get("team_id", ""))
                    ),
                    None,
                )
                team_name = (
                    self._team_display_name(team)
                    if team
                    else str(bet.get("team_name", "") or "未知队伍")
                )
                lines.append(
                    f"{match.get('display_id', '?')}｜{team_name}｜{bet.get('amount')}｜"
                    f"{status_names.get(bet.get('status'), bet.get('status'))}｜返还 {bet.get('payout', 0)}"
                )
        message = "我的竞猜：\n" + "\n".join(lines) if lines else "你还没有竞猜记录。"
        yield self._plain_result(event, self._single_line_message(message))

    def _esports_user_name(self, user_id: str) -> str:
        groups = self.data.get("groups", {})
        if isinstance(groups, dict):
            for group in groups.values():
                if not isinstance(group, dict):
                    continue
                member = group.get("members", {}).get(user_id) if isinstance(group.get("members"), dict) else None
                if isinstance(member, dict) and member.get("display_name"):
                    return self._safe_display_name(member.get("display_name"), user_id)
        return self._safe_display_name(None, user_id)

    async def esports_leaderboard(self, event: AstrMessageEvent):
        settings = self._get_esports_settings()
        async with self._data_lock:
            stats: Dict[str, Dict[str, int]] = {}
            for bet in self._get_esports_store().setdefault("bets", {}).values():
                if not isinstance(bet, dict) or bet.get("status") not in {"won", "lost"}:
                    continue
                user_id = str(bet.get("user_id", ""))
                row = stats.setdefault(user_id, {"settled": 0, "wins": 0, "profit": 0, "payout": 0})
                row["settled"] += 1
                row["wins"] += int(bet.get("status") == "won")
                row["profit"] += self._normalize_signed_int(bet.get("profit"), 0)
                row["payout"] += self._normalize_int(bet.get("payout"), 0, 0)
            limit = settings["leaderboard_limit"]
            profit_rank = sorted(stats.items(), key=lambda item: (item[1]["profit"], item[1]["payout"]), reverse=True)[:limit]
            payout_rank = sorted(stats.items(), key=lambda item: (item[1]["payout"], item[1]["profit"]), reverse=True)[:limit]
            hit_candidates = [item for item in stats.items() if item[1]["settled"] >= settings["hit_rate_min_bets"]]
            hit_rank = sorted(hit_candidates, key=lambda item: (item[1]["wins"] / item[1]["settled"], item[1]["settled"]), reverse=True)[:limit]
            sections = ["竞猜排行榜"]
            sections.append("盈利：" + ("；".join(f"{i}.{self._esports_user_name(uid)} {row['profit']:+d}" for i, (uid, row) in enumerate(profit_rank, 1)) or "暂无"))
            sections.append("命中率：" + ("；".join(f"{i}.{self._esports_user_name(uid)} {row['wins'] / row['settled']:.1%}({row['settled']}场)" for i, (uid, row) in enumerate(hit_rank, 1)) or f"暂无（至少 {settings['hit_rate_min_bets']} 场）"))
            sections.append("总返还：" + ("；".join(f"{i}.{self._esports_user_name(uid)} {row['payout']}" for i, (uid, row) in enumerate(payout_rank, 1)) or "暂无"))
        yield self._plain_result(event, self._single_line_message("\n".join(sections)))

    async def esports_rules(self, event: AstrMessageEvent):
        settings = self._get_esports_settings()
        points_name = self._get_points_name()
        message = (
            f"竞猜规则：每场选择胜者，最低 {settings['min_bet']}、最高累计 {settings['max_bet']} {points_name}；"
            f"同队可追加。开赛前 {settings['switch_deadline_minutes']} 分钟前可改选或撤单，"
            f"开赛前 {settings['close_before_minutes']} 分钟封盘。异常比赛全额退款。"
            "倍率根据近期赛果的 Elo 实力评分估算，并在该场第一笔下注时锁定；它是娱乐性模型结果，不代表真实市场赔率。"
        )
        yield self._plain_result(event, self._single_line_message(message))

    def _manual_team_id(self, game: str, name: str) -> str:
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", name.casefold()).strip("-")
        return f"manual:{game}:{slug or uuid.uuid4().hex[:8]}"

    def _create_manual_match_locked(
        self,
        game: str,
        competition: str,
        team_a: str,
        team_b: str,
        local_start: str,
    ) -> Dict[str, Any]:
        settings = self._get_esports_settings()
        parsed = datetime.datetime.strptime(local_start.strip(), "%Y-%m-%d %H:%M")
        local_tz = datetime.timezone(datetime.timedelta(hours=settings["timezone_offset_hours"]))
        start_utc = parsed.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
        match_id = f"manual:{uuid.uuid4().hex}"
        teams = [
            {"id": self._manual_team_id(game, team_a), "name": team_a.strip()[:80], "code": "", "image_url": "", "score": 0},
            {"id": self._manual_team_id(game, team_b), "name": team_b.strip()[:80], "code": "", "image_url": "", "score": 0},
        ]
        now = self._utcnow().isoformat(timespec="seconds")
        match = {
            "id": match_id,
            "display_id": "",
            "source": "manual",
            "source_id": "",
            "game": game,
            "competition": competition.strip()[:120],
            "stage": "",
            "name": f"{teams[0]['name']} vs {teams[1]['name']}",
            "start_time": start_utc.isoformat(timespec="seconds"),
            "end_time": "",
            "status": "not_started",
            "teams": teams,
            "winner_id": "",
            "odds": {},
            "probabilities": {},
            "odds_locked": False,
            "visible": True,
            "settled_at": "",
            "created_at": now,
            "updated_at": now,
        }
        matches = self._get_esports_store().setdefault("matches", {})
        match["display_id"] = self._ensure_unique_display_id(match, matches)
        self._calculate_match_odds_locked(match)
        matches[match_id] = match
        return match

    async def esports_admin(self, event: AstrMessageEvent):
        admin_error = await self._ensure_points_admin(event)
        if admin_error:
            yield self._plain_result(event, admin_error)
            return
        raw = self._get_command_args(event).strip()
        command, _, remainder = raw.partition(" ")
        command = command.strip().lower()
        remainder = remainder.strip()
        if command in {"同步", "sync"}:
            try:
                result = await self._sync_esports_once("管理员同步")
                message = result["summary"]
            except Exception as exc:
                message = f"同步失败：{exc}"
            yield self._plain_result(event, self._single_line_message(message))
            return

        async with self._data_lock:
            if command in {"添加", "add"}:
                parts = [item.strip() for item in remainder.split("|")]
                if len(parts) != 5 or parts[0].lower() not in {"lol", "valorant"} or not all(parts):
                    message = "用法：/竞猜管理 添加 lol|赛事名|队伍A|队伍B|2026-09-01 19:00"
                else:
                    try:
                        match = self._create_manual_match_locked(*parts)
                        await self._save_data_locked()
                        message = f"已添加 {match['display_id']}：{match['name']}。"
                    except ValueError:
                        message = "时间格式错误，请使用 YYYY-MM-DD HH:MM。"
            elif command in {"结算", "settle"}:
                parts = remainder.split(maxsplit=1)
                match = self._resolve_match_locked(parts[0]) if parts else None
                team = self._resolve_match_team(match, parts[1]) if match and len(parts) > 1 else None
                if not match or not team:
                    message = "用法：/竞猜管理 结算 比赛编号 队伍(1或2)"
                elif match.get("settled_at"):
                    message = "该比赛已经处理过，不能重复结算。"
                else:
                    match["winner_id"] = team["id"]
                    match["status"] = "finished"
                    settled, winners, paid = self._settle_match_locked(match)
                    self._apply_rating_result_locked(match)
                    await self._save_data_locked()
                    message = f"已按 {self._team_display_name(team)} 获胜结算：{settled} 注，命中 {winners} 注，返还 {paid}。"
            elif command in {"退款", "refund"}:
                match = self._resolve_match_locked(remainder)
                if not match:
                    message = "用法：/竞猜管理 退款 比赛编号"
                elif match.get("settled_at"):
                    message = "该比赛已经处理过，不能重复退款。"
                else:
                    count, points = self._refund_match_locked(match, "管理员退款")
                    await self._save_data_locked()
                    message = f"退款完成：{count} 人，共 {points} {self._get_points_name()}。"
            elif command in {"封盘", "close"}:
                match = self._resolve_match_locked(remainder)
                if not match:
                    message = "未找到比赛。"
                else:
                    match["status"] = "closed"
                    match["updated_at"] = self._utcnow().isoformat(timespec="seconds")
                    await self._save_data_locked()
                    message = f"已封盘 {match['display_id']}。"
            elif command in {"隐藏", "hide", "显示", "show"}:
                match = self._resolve_match_locked(remainder)
                if not match:
                    message = "未找到比赛。"
                else:
                    match["visible"] = command in {"显示", "show"}
                    await self._save_data_locked()
                    message = f"已{'显示' if match['visible'] else '隐藏'} {match['display_id']}。"
            else:
                message = (
                    "竞猜管理：同步；添加 lol|赛事|队伍A|队伍B|YYYY-MM-DD HH:MM；"
                    "结算 编号 队伍；退款 编号；封盘 编号；隐藏/显示 编号。"
                )
        yield self._plain_result(event, self._single_line_message(message))
