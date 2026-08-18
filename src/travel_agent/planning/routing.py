from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from travel_agent.domain.models import Coordinate


def haversine_distance_meters(origin: Coordinate, destination: Coordinate) -> int:
    earth_radius_meters = 6_371_000
    lat1 = radians(origin.latitude)
    lat2 = radians(destination.latitude)
    delta_lat = lat2 - lat1
    delta_lon = radians(destination.longitude - origin.longitude)
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return round(2 * earth_radius_meters * asin(sqrt(value)))


def estimate_route(origin: Coordinate, destination: Coordinate) -> tuple[int, int, int]:
    """Return estimated road distance, travel minutes, and walking meters.

    This deterministic estimator is intentionally isolated so the real AMap
    route adapter can replace it without changing the planning domain.
    """

    direct_distance = haversine_distance_meters(origin, destination)
    road_distance = round(direct_distance * 1.25)
    travel_minutes = max(8, round(road_distance / 22_000 * 60 + 5))
    walking_meters = min(round(road_distance * 0.12), 2_000)
    return road_distance, travel_minutes, walking_meters

