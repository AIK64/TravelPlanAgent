from __future__ import annotations

from datetime import time
from decimal import Decimal

from travel_agent.domain.models import Coordinate, POI, TimeWindow


HANGZHOU_POIS: tuple[POI, ...] = (
    POI(
        id="hz_west_lake",
        name="西湖风景名胜区",
        city="杭州",
        coordinate=Coordinate(longitude=120.1487, latitude=30.2448),
        categories=["自然", "城市地标"],
        opening_window=TimeWindow(start=time(6, 0), end=time(22, 0)),
        estimated_duration_minutes=120,
        estimated_cost=Decimal("0"),
        indoor_outdoor="outdoor",
        suitability_tags=["适老", "轻松"],
    ),
    POI(
        id="hz_lingyin",
        name="灵隐寺",
        city="杭州",
        coordinate=Coordinate(longitude=120.1017, latitude=30.2404),
        categories=["人文", "历史", "寺庙"],
        opening_window=TimeWindow(start=time(7, 30), end=time(17, 30)),
        estimated_duration_minutes=120,
        estimated_cost=Decimal("75"),
        indoor_outdoor="mixed",
        suitability_tags=["人文"],
    ),
    POI(
        id="hz_xixi",
        name="西溪国家湿地公园",
        city="杭州",
        coordinate=Coordinate(longitude=120.0624, latitude=30.2668),
        categories=["自然", "湿地"],
        opening_window=TimeWindow(start=time(8, 30), end=time(17, 30)),
        estimated_duration_minutes=180,
        estimated_cost=Decimal("80"),
        indoor_outdoor="outdoor",
        suitability_tags=["自然", "适老"],
    ),
    POI(
        id="hz_hefang",
        name="河坊街",
        city="杭州",
        coordinate=Coordinate(longitude=120.1714, latitude=30.2371),
        categories=["美食", "街区", "人文"],
        opening_window=TimeWindow(start=time(9, 0), end=time(21, 30)),
        estimated_duration_minutes=90,
        estimated_cost=Decimal("60"),
        indoor_outdoor="mixed",
        suitability_tags=["美食", "夜游"],
    ),
    POI(
        id="hz_zhejiang_museum",
        name="浙江省博物馆孤山馆区",
        city="杭州",
        coordinate=Coordinate(longitude=120.1420, latitude=30.2537),
        categories=["人文", "博物馆", "历史"],
        opening_window=TimeWindow(start=time(9, 0), end=time(17, 0)),
        estimated_duration_minutes=90,
        estimated_cost=Decimal("0"),
        indoor_outdoor="indoor",
        suitability_tags=["雨天", "适老"],
    ),
    POI(
        id="hz_tea_museum",
        name="中国茶叶博物馆",
        city="杭州",
        coordinate=Coordinate(longitude=120.1165, latitude=30.2298),
        categories=["人文", "博物馆", "茶文化"],
        opening_window=TimeWindow(start=time(9, 0), end=time(17, 0)),
        estimated_duration_minutes=90,
        estimated_cost=Decimal("0"),
        indoor_outdoor="mixed",
        suitability_tags=["雨天", "适老"],
    ),
    POI(
        id="hz_grand_canal",
        name="京杭大运河杭州景区",
        city="杭州",
        coordinate=Coordinate(longitude=120.1508, latitude=30.3192),
        categories=["人文", "自然", "城市地标"],
        opening_window=TimeWindow(start=time(9, 0), end=time(21, 0)),
        estimated_duration_minutes=120,
        estimated_cost=Decimal("0"),
        indoor_outdoor="outdoor",
        suitability_tags=["夜游", "轻松"],
    ),
    POI(
        id="hz_botanical_garden",
        name="杭州植物园",
        city="杭州",
        coordinate=Coordinate(longitude=120.1204, latitude=30.2561),
        categories=["自然", "公园"],
        opening_window=TimeWindow(start=time(8, 30), end=time(17, 0)),
        estimated_duration_minutes=120,
        estimated_cost=Decimal("10"),
        indoor_outdoor="outdoor",
        suitability_tags=["自然", "适老"],
    ),
)


HANGZHOU_ANCHORS: tuple[POI, ...] = (
    POI(
        id="hz_east_station",
        name="杭州东站",
        city="杭州",
        coordinate=Coordinate(longitude=120.2120, latitude=30.2909),
        categories=["交通枢纽", "火车站"],
        opening_window=TimeWindow(start=time(0, 1), end=time(23, 59)),
        estimated_duration_minutes=30,
        estimated_cost=Decimal("0"),
        indoor_outdoor="indoor",
        suitability_tags=["到达", "离开"],
    ),
    POI(
        id="hz_west_lake_east",
        name="西湖东侧",
        city="杭州",
        coordinate=Coordinate(longitude=120.1650, latitude=30.2500),
        categories=["住宿区域"],
        opening_window=TimeWindow(start=time(0, 1), end=time(23, 59)),
        estimated_duration_minutes=30,
        estimated_cost=Decimal("0"),
        indoor_outdoor="mixed",
        suitability_tags=["住宿"],
    ),
)


def get_mock_pois(city: str) -> list[POI]:
    """Return isolated copies so Provider adapters cannot mutate the fixture."""
    normalized = city.strip().removesuffix("市")
    if normalized != "杭州":
        return []
    return [
        poi.model_copy(deep=True)
        for poi in (*HANGZHOU_POIS, *HANGZHOU_ANCHORS)
    ]

