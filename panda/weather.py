"""Weather via Open-Meteo (free, no API key)."""

from __future__ import annotations

import time

import httpx

_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers",
    86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}
_cache: dict[str, tuple[float, dict]] = {}


async def get_weather(location: str) -> dict:
    key = location.lower().strip()
    if key in _cache and time.time() - _cache[key][0] < 900:
        return _cache[key][1]
    async with httpx.AsyncClient(timeout=10) as client:
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if not results:
            return {"error": f"Could not find a place called {location!r}."}
        place = results[0]
        fc = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 3,
            },
        )
        fc.raise_for_status()
        data = fc.json()
    cur = data["current"]
    daily = data["daily"]
    result = {
        "location": f"{place['name']}, {place.get('country', '')}".strip(", "),
        "now": {
            "temp_c": cur["temperature_2m"],
            "feels_like_c": cur["apparent_temperature"],
            "humidity": cur["relative_humidity_2m"],
            "wind_kmh": cur["wind_speed_10m"],
            "conditions": _CODES.get(cur["weather_code"], "unknown"),
        },
        "forecast": [
            {
                "date": daily["time"][i],
                "conditions": _CODES.get(daily["weather_code"][i], "unknown"),
                "max_c": daily["temperature_2m_max"][i],
                "min_c": daily["temperature_2m_min"][i],
                "rain_chance": daily["precipitation_probability_max"][i],
            }
            for i in range(len(daily["time"]))
        ],
    }
    _cache[key] = (time.time(), result)
    return result
