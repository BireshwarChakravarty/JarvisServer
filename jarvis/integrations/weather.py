"""Weather via Open-Meteo (free, no API key)."""
from __future__ import annotations

import time
from functools import lru_cache

import httpx

WMO = {
    0: "clear skies", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
    96: "thunderstorms with hail", 99: "severe thunderstorms with hail",
}

_cache: dict[tuple, tuple[float, dict]] = {}


@lru_cache(maxsize=32)
def geocode(city: str) -> tuple[float, float, str]:
    r = httpx.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise ValueError(f"Couldn't locate {city!r}")
    top = results[0]
    label = ", ".join(x for x in (top.get("name"), top.get("country")) if x)
    return float(top["latitude"]), float(top["longitude"]), label


def forecast(city: str | None = None, lat: float | None = None, lon: float | None = None,
             units: str = "metric") -> dict:
    label = city or ""
    if lat is None or lon is None:
        if not city:
            raise ValueError("Need a city or coordinates")
        lat, lon, label = geocode(city)
    key = (round(lat, 2), round(lon, 2), units)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    imperial = units == "imperial"
    r = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
            "hourly": "precipitation_probability,temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,uv_index_max",
            "timezone": "auto", "forecast_days": 3,
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    cur, daily = data["current"], data["daily"]
    unit = "°F" if imperial else "°C"
    # rain risk over the next 12 hours
    probs = (data.get("hourly", {}).get("precipitation_probability") or [])[:12]
    out = {
        "location": label,
        "unit": unit,
        "temperature": round(cur["temperature_2m"]),
        "feels_like": round(cur["apparent_temperature"]),
        "humidity": cur["relative_humidity_2m"],
        "wind": f"{round(cur['wind_speed_10m'])} {'mph' if imperial else 'km/h'}",
        "condition": WMO.get(cur["weather_code"], "unsettled"),
        "code": cur["weather_code"],
        "is_day": bool(cur.get("is_day", 1)),
        "high": round(daily["temperature_2m_max"][0]),
        "low": round(daily["temperature_2m_min"][0]),
        "rain_chance_12h": max(probs) if probs else daily["precipitation_probability_max"][0],
        "sunrise": daily["sunrise"][0][-5:],
        "sunset": daily["sunset"][0][-5:],
        "uv_max": daily.get("uv_index_max", [None])[0],
        "tomorrow": {
            "condition": WMO.get(daily["weather_code"][1], ""),
            "high": round(daily["temperature_2m_max"][1]),
            "low": round(daily["temperature_2m_min"][1]),
        },
    }
    _cache[key] = (time.time(), out)
    return out


def spoken(w: dict) -> str:
    s = (f"It's {w['temperature']} degrees and {w['condition']} in {w['location']}, "
         f"with a high of {w['high']} and a low of {w['low']}.")
    if w.get("rain_chance_12h", 0) >= 40:
        s += f" There's a {w['rain_chance_12h']}% chance of rain later — an umbrella wouldn't go amiss."
    return s
