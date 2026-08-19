from __future__ import annotations

from travel_agent.planning.search_plan import build_search_plan


def test_must_visit_queries_precede_interests(hangzhou_trip):
    """防止必去地点失去最高检索优先级。"""
    queries = build_search_plan(hangzhou_trip, per_query_limit=10)

    assert queries[0].keyword == "灵隐寺"
    assert queries[0].exact_match is True
    assert [query.keyword for query in queries[1:]] == ["自然", "美食", "人文"]


def test_empty_preferences_use_scenic_default(hangzhou_trip):
    """防止没有偏好时返回空的工具检索意图。"""
    trip = hangzhou_trip.model_copy(update={"must_visit": [], "interests": []})

    assert [query.keyword for query in build_search_plan(trip)] == ["景点"]


def test_duplicate_or_blank_terms_do_not_create_duplicate_queries(hangzhou_trip):
    """防止同一关键词重复消耗外部检索预算。"""
    trip = hangzhou_trip.model_copy(
        update={
            "must_visit": [" 灵隐寺 ", "灵隐寺", ""],
            "interests": ["美食", " 美食 ", ""],
        }
    )

    queries = build_search_plan(trip, per_query_limit=7)

    assert [(query.keyword, query.exact_match, query.priority, query.limit) for query in queries] == [
        ("灵隐寺", True, 100, 7),
        ("美食", False, 50, 7),
    ]
