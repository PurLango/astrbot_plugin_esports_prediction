# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


PANDASCORE_BASE_URL = "https://api.pandascore.co"
LEAGUE_SEARCH_TERMS = {
    "lol": (
        "LPL",
        "LCK",
        "First Stand",
        "MSI",
        "Mid-Season Invitational",
        "World Championship",
        "Worlds",
    ),
}


class EsportsProviderError(RuntimeError):
    """赛事数据源返回了无法继续处理的错误。"""


class PandaScoreProvider:
    """PandaScore fixtures 客户端，只读取赛程和赛果。"""

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: int = 20,
        base_url: str = PANDASCORE_BASE_URL,
    ) -> None:
        self.token = str(token or "").strip()
        self.timeout_seconds = max(5, min(int(timeout_seconds), 60))
        self.base_url = str(base_url or PANDASCORE_BASE_URL).rstrip("/")

    def _get_json_sync(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.token:
            raise EsportsProviderError("尚未配置 PandaScore API Token")

        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "AstrBotEsportsPrediction/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise EsportsProviderError("PandaScore Token 无效或当前套餐无权访问") from exc
            raise EsportsProviderError(f"PandaScore 请求失败：HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise EsportsProviderError(f"PandaScore 网络请求失败：{exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EsportsProviderError("PandaScore 返回了无法解析的数据") from exc

        if not isinstance(payload, list):
            raise EsportsProviderError("PandaScore 返回的数据格式不符合预期")
        return [item for item in payload if isinstance(item, dict)]

    async def fetch_matches(
        self,
        game: str,
        state: str,
        *,
        page_size: int = 100,
        pages: int = 1,
        league_ids: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_game = str(game or "").strip().lower()
        if normalized_game not in {"lol", "valorant"}:
            raise EsportsProviderError(f"不支持的数据源游戏类型：{game}")
        normalized_state = str(state or "").strip().lower()
        if normalized_state not in {"upcoming", "running", "past"}:
            raise EsportsProviderError(f"不支持的数据源比赛状态：{state}")

        sort = "-begin_at" if normalized_state == "past" else "begin_at"
        normalized_league_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in (league_ids or [])
                if str(item).strip()
            )
        )
        result: list[dict[str, Any]] = []
        for page_number in range(1, max(1, min(int(pages), 5)) + 1):
            params = {
                "page[size]": max(1, min(int(page_size), 100)),
                "page[number]": page_number,
                "sort": sort,
            }
            if normalized_league_ids:
                params["filter[league_id]"] = ",".join(normalized_league_ids)
            page = await asyncio.to_thread(
                self._get_json_sync,
                f"/{normalized_game}/matches/{normalized_state}",
                params,
            )
            result.extend(page)
            if len(page) < max(1, min(int(page_size), 100)):
                break
        return result

    async def fetch_leagues(
        self,
        game: str,
        *,
        page_size: int = 100,
        pages: int = 2,
    ) -> list[dict[str, Any]]:
        normalized_game = str(game or "").strip().lower()
        if normalized_game not in LEAGUE_SEARCH_TERMS:
            raise EsportsProviderError(f"该游戏不需要查询联赛目录：{game}")

        size = max(1, min(int(page_size), 100))
        page_limit = max(1, min(int(pages), 5))

        async def fetch_search_term(search_term: str) -> list[dict[str, Any]]:
            matches: list[dict[str, Any]] = []
            for page_number in range(1, page_limit + 1):
                page = await asyncio.to_thread(
                    self._get_json_sync,
                    f"/{normalized_game}/leagues",
                    {
                        "page[size]": size,
                        "page[number]": page_number,
                        "search[name]": search_term,
                        "sort": "name",
                    },
                )
                matches.extend(page)
                if len(page) < size:
                    break
            return matches

        pages_by_term = await asyncio.gather(
            *(
                fetch_search_term(search_term)
                for search_term in LEAGUE_SEARCH_TERMS[normalized_game]
            )
        )
        result = [league for page in pages_by_term for league in page]

        unique: dict[str, dict[str, Any]] = {}
        for league in result:
            key = str(league.get("id", "") or "").strip()
            if not key:
                key = "|".join(
                    (
                        str(league.get("slug", "") or "").strip(),
                        str(league.get("name", "") or "").strip(),
                    )
                )
            unique[key] = league
        return list(unique.values())
