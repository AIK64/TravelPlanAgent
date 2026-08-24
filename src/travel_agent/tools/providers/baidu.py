from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math

import httpx

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    RouteMode,
    RouteQuery,
    RouteResult,
    ToolErrorCategory,
    ValueSource,
)
from travel_agent.tools.errors import ToolProviderError


class BaiduMapClient:
    name = "baidu"

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    async def request_json(
        self, operation: str, path: str, params: dict[str, object]
    ) -> dict[str, object]:
        try:
            response = await self._client.get(
                f"https://api.map.baidu.com{path}",
                params={**params, "ak": self._api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise ToolProviderError.timeout(operation) from error
        except httpx.HTTPStatusError as error:
            category = (
                ToolErrorCategory.RATE_LIMIT
                if error.response.status_code == 429
                else ToolErrorCategory.UPSTREAM_UNAVAILABLE
            )
            raise ToolProviderError(
                category=category,
                code=f"http_{error.response.status_code}",
                operation=operation,
                retryable=category is not ToolErrorCategory.AUTHENTICATION,
                safe_message="百度地图服务暂时不可用",
            ) from error
        except (httpx.RequestError, ValueError) as error:
            category = (
                ToolErrorCategory.CONNECTION
                if isinstance(error, httpx.RequestError)
                else ToolErrorCategory.INVALID_RESPONSE
            )
            raise ToolProviderError(
                category=category,
                code="connection_error" if category is ToolErrorCategory.CONNECTION else "invalid_json",
                operation=operation,
                retryable=True,
                safe_message="百度地图服务返回异常",
            ) from error
        if not isinstance(payload, dict):
            raise _baidu_error(operation, -1, "invalid response")
        status = int(payload.get("status", -1))
        if status != 0:
            raise _baidu_error(operation, status, str(payload.get("message", "")))
        return payload


class BaiduPOIProvider:
    name = "baidu"

    def __init__(self, client: BaiduMapClient) -> None:
        self._client = client

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        payload = await self._client.request_json(
            "poi.search",
            "/place/v2/search",
            {
                "query": query.keyword,
                "region": query.city,
                "city_limit": "true" if query.exact_match else "false",
                "scope": 2,
                "output": "json",
                "ret_coordtype": "gcj02ll",
                "page_size": min(query.limit, 20),
                "page_num": 0,
            },
        )
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise _invalid_response("poi.search", "invalid_poi_results")
        fetched_at = datetime.now(timezone.utc)
        results: list[POIFacts] = []
        for item in raw_results:
            if not isinstance(item, dict) or not isinstance(item.get("location"), dict):
                continue
            location = item["location"]
            try:
                coordinate = Coordinate(
                    longitude=float(location["lng"]),
                    latitude=float(location["lat"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            uid = str(item.get("uid", "")).strip()
            name = str(item.get("name", "")).strip()
            if not uid or not name:
                continue
            detail = item.get("detail_info")
            detail = detail if isinstance(detail, dict) else {}
            raw_tags = str(
                detail.get("classified_poi_tag") or detail.get("tag") or ""
            )
            categories = [part.strip() for part in raw_tags.replace(";", ",").split(",") if part.strip()]
            results.append(
                POIFacts(
                    id=f"baidu:{uid}",
                    name=name,
                    city=str(item.get("city") or query.city),
                    coordinate=coordinate,
                    categories=categories or [query.keyword],
                    average_cost_per_person=_decimal_or_none(detail.get("price")),
                    provider=self.name,
                    fetched_at=fetched_at,
                    data_confidence=0.95,
                    field_sources={
                        "coordinate": ValueSource.PROVIDER,
                        "categories": ValueSource.PROVIDER,
                    },
                )
            )
        return results[: query.limit]


class BaiduRouteProvider:
    name = "baidu"

    def __init__(self, client: BaiduMapClient) -> None:
        self._client = client

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        return await self._route(query, RouteMode.DRIVING)

    async def get_walking_route(self, query: RouteQuery) -> RouteResult:
        return await self._route(query, RouteMode.WALKING)

    async def _route(self, query: RouteQuery, mode: RouteMode) -> RouteResult:
        operation = f"route.get_{mode.value}"
        params: dict[str, object] = {
            "origin": _lat_lon(query.origin),
            "destination": _lat_lon(query.destination),
            "coord_type": "gcj02",
            "ret_coordtype": "gcj02",
        }
        if query.origin_poi_id and query.origin_poi_id.startswith("baidu:"):
            params["origin_uid"] = query.origin_poi_id.removeprefix("baidu:")
        if query.destination_poi_id and query.destination_poi_id.startswith("baidu:"):
            params["destination_uid"] = query.destination_poi_id.removeprefix("baidu:")
        if mode is RouteMode.DRIVING:
            params["tactics"] = _driving_tactics(query.strategy)
        payload = await self._client.request_json(
            operation, f"/directionlite/v1/{mode.value}", params
        )
        raw_result = payload.get("result")
        routes = raw_result.get("routes") if isinstance(raw_result, dict) else None
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise _invalid_response(operation, "route_no_data")
        selected = routes[0]
        try:
            distance = max(1, round(float(selected["distance"])))
            duration = max(1, math.ceil(float(selected["duration"]) / 60))
        except (KeyError, TypeError, ValueError) as error:
            raise _invalid_response(operation, "invalid_route_result") from error
        return RouteResult(
            distance_meters=distance,
            duration_minutes=duration,
            mode=mode,
            provider=self.name,
            data_confidence=0.95,
            fetched_at=datetime.now(timezone.utc),
        )


def _baidu_error(operation: str, status: int, message: str) -> ToolProviderError:
    if status == 3:
        return ToolProviderError.authentication(operation)
    if status == 4:
        category = ToolErrorCategory.RATE_LIMIT
        retryable = True
    elif status == 2:
        category = ToolErrorCategory.INVALID_REQUEST
        retryable = False
    elif status in {1, 5, 101, 102, 200, 211}:
        category = ToolErrorCategory.UPSTREAM_UNAVAILABLE
        retryable = True
    else:
        category = ToolErrorCategory.INVALID_RESPONSE
        retryable = True
    return ToolProviderError(
        category=category,
        code=f"baidu_{status}",
        operation=operation,
        retryable=retryable,
        safe_message=f"百度地图服务请求失败: {message[:80] or status}",
    )


def _invalid_response(operation: str, code: str) -> ToolProviderError:
    return ToolProviderError(
        category=ToolErrorCategory.INVALID_RESPONSE,
        code=code,
        operation=operation,
        retryable=True,
        safe_message="百度地图服务返回了无效响应",
    )


def _lat_lon(value: Coordinate) -> str:
    return f"{value.latitude:.6f},{value.longitude:.6f}"


def _driving_tactics(strategy: int) -> int:
    return {32: 0, 33: 2, 34: 3}.get(strategy, 0)


def _decimal_or_none(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result >= 0 else None
