"""weather: current conditions and a short forecast, for a place the model
names or for where the CALLER is right now.

Why a tool and not web_search: "weather Danville, CA" through a search engine
can land on Danville, Quebec — "CA" is also Canada's country code — and the
model then reports the wrong town with confidence. Here the place is resolved
deliberately and the answer always names the exact place it describes:

- `City, XX` where XX is a US state code (CA, TX, …) means that US state.
  A two-letter code that is not a state is read as an ISO country code.
- `City, State` / `City, Country` spelled out filters the same way.
- Several towns left after that: the one NEAREST the caller when the
  caller's location is known, else the most populous — and the reply lists
  the runners-up so a wrong pick shows.
- No location named: the caller's own position. There is no fixed home
  (Max travels): with no caller position, the tool asks for a place.

The caller's position is the device geolocation Open WebUI keeps when the
user turns on Settings -> Interface -> "Allow User Location" (user.info
.location, "lat, lon"). The WebUI tool server looks it up for the calling
user (caller_location below) and passes it in; nothing else ever sees it.

Data: Open-Meteo (geocoding + forecast), free and keyless. The tool talks to
those two fixed hosts only; nothing the model passes becomes a URL host.
Offline (beast-island), the caller refuses before any request is made.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
COUNTRY_ALIASES = {"usa": "US", "us": "US", "united states": "US", "america": "US",
                   "uk": "GB", "united kingdom": "GB", "england": "GB", "canada": "CA"}

# WMO weather interpretation codes (Open-Meteo `weather_code`).
WMO = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog",
       48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain",
       65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
       95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def _get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=TIMEOUT_S) as r:  # fixed hosts only
        return json.load(r)


def parse_place(text: str) -> tuple[str, str | None, str | None]:
    """'Danville, CA' -> ('Danville', admin1 'California', country 'US')."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return "", None, None
    name, admin1, country = parts[0], None, None
    for q in parts[1:]:
        u, lo = q.upper(), q.lower()
        if len(q) == 2 and u in US_STATES and admin1 is None and country in (None, "US"):
            admin1, country = US_STATES[u], "US"
        elif lo in COUNTRY_ALIASES:
            country = COUNTRY_ALIASES[lo]
        elif len(q) == 2 and q.isalpha():
            country = u
        elif q.title() in US_STATES.values():
            admin1, country = q.title(), "US"
        elif admin1 is None:
            admin1 = q
    return name, admin1, country


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def parse_latlon(value) -> tuple[float, float] | None:
    """'37.822, -121.999' (Open WebUI's user.info.location) -> (lat, lon)."""
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", str(value or ""))
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def resolve(text: str, near: tuple[float, float] | None = None, geo=None) -> tuple[dict | None, list[dict]]:
    """The best Open-Meteo place for `text`, and the other candidates."""
    geo = geo or (lambda name: _get(GEO_URL, {"name": name, "count": 20, "language": "en",
                                              "format": "json"}).get("results") or [])
    name, admin1, country = parse_place(text)
    if not name:
        return None, []
    cands = geo(name)
    if country:
        cands = [c for c in cands if (c.get("country_code") or "").upper() == country]
    if admin1:
        a = admin1.lower()
        cands = [c for c in cands if a in (c.get("admin1") or "").lower()
                 or a in (c.get("admin2") or "").lower()]
    if not cands:
        return None, []
    if near:
        cands.sort(key=lambda c: _km(near, (c["latitude"], c["longitude"])))
    else:
        cands.sort(key=lambda c: -(c.get("population") or 0))
    return cands[0], cands[1:4]


def _label(c: dict) -> str:
    return ", ".join(x for x in (c.get("name"), c.get("admin1"), c.get("country")) if x)


def weather(location: str = "", days: int = 3, *, near=None, units: str = "",
            geo=None, forecast=None) -> str:
    where = (location or "").strip()
    if not where and not near:
        return ("No location given, and the caller's position is unknown (Open WebUI: Settings -> "
                "Interface -> Allow User Location). Ask the user which place, as 'City, State' or "
                "'City, Country'.")
    if where:
        place, others = resolve(where, near, geo)
        if place is None:
            return f"No place matches '{where}'. Ask for 'City, State' (US) or 'City, Country'."
    else:
        place = {"name": "your current location", "latitude": near[0], "longitude": near[1],
                 "country_code": ""}
        others = []
    cc = (place.get("country_code") or "").upper()
    imperial = units == "imperial" or (units != "metric" and cc in ("US", "LR", "MM", ""))
    days = max(1, min(int(days or 3), 7))
    fc = (forecast or (lambda p: _get(FORECAST_URL, p)))({
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
        "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
        "precipitation_unit": "inch" if imperial else "mm",
    })
    t, w, p = ("°F", "mph", "in") if imperial else ("°C", "km/h", "mm")
    cur = fc.get("current") or {}
    head = f"Weather for {_label(place)} ({place['latitude']:.2f}, {place['longitude']:.2f})"
    if where and near:
        head += f", {_km(near, (place['latitude'], place['longitude'])):.0f} km from the caller"
    lines = [head + f"; local time {cur.get('time', '?')} {fc.get('timezone_abbreviation', '')}".rstrip(),
             f"Now: {WMO.get(cur.get('weather_code'), 'unknown')}, {cur.get('temperature_2m')}{t} "
             f"(feels {cur.get('apparent_temperature')}{t}), humidity {cur.get('relative_humidity_2m')}%, "
             f"wind {cur.get('wind_speed_10m')} {w}, precipitation {cur.get('precipitation')} {p}"]
    d = fc.get("daily") or {}
    for i, day in enumerate(d.get("time") or []):
        lines.append(f"{day}: {WMO.get((d.get('weather_code') or [None])[i], 'unknown')}, "
                     f"{(d.get('temperature_2m_min') or [None])[i]}–{(d.get('temperature_2m_max') or [None])[i]}{t}, "
                     f"rain chance {(d.get('precipitation_probability_max') or [None])[i]}%")
    if others:
        lines.append("Other places with that name: " + "; ".join(_label(c) for c in others)
                     + " — if the user meant one of these, call again with 'City, State/Country'.")
    lines.append("Source: Open-Meteo.")
    return "\n".join(lines)


# ── The caller's position, from Open WebUI ───────────────────────────────────
# Set per request by the WebUI tool server (openapi_tools.py), read by
# mcp_server.weather — the tool function never sees the request itself.
import contextvars  # noqa: E402
CALLER_NEAR: contextvars.ContextVar = contextvars.ContextVar("weather_caller_near", default=None)
_ADMIN = {"token": None, "until": 0.0}


def _conf_values(path: str, keys: tuple[str, ...]) -> dict:
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) in keys:
                v = m.group(2)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[m.group(1)] = v
    except OSError:
        pass
    return out


def caller_location(user_id: str | None, conf_path: str, webui: str = "http://127.0.0.1:3000",
                    http=None) -> tuple[float, float] | None:
    """The calling WebUI user's last device position, or None. Signs in as the
    WebUI admin (WEBUI_ADMIN_* in openbeast.conf, which this process can read
    anyway) because Open WebUI returns a user's `info` only to admins; the
    token is cached for ten minutes. Any failure is "position unknown"."""
    if not user_id or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", user_id):
        return None
    http = http or (lambda url, body=None, tok=None: json.load(urllib.request.urlopen(
        urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                               {"Content-Type": "application/json",
                                **({"Authorization": "Bearer " + tok} if tok else {})}), timeout=5)))
    try:
        if not _ADMIN["token"] or time.monotonic() > _ADMIN["until"]:
            c = _conf_values(conf_path, ("WEBUI_ADMIN_EMAIL", "WEBUI_ADMIN_PASSWORD"))
            if not c.get("WEBUI_ADMIN_EMAIL") or not c.get("WEBUI_ADMIN_PASSWORD"):
                return None
            _ADMIN["token"] = http(webui + "/api/v1/auths/signin",
                                   {"email": c["WEBUI_ADMIN_EMAIL"], "password": c["WEBUI_ADMIN_PASSWORD"]})["token"]
            _ADMIN["until"] = time.monotonic() + 600
        u = http(webui + "/api/v1/users/" + user_id, tok=_ADMIN["token"])
        return parse_latlon((u.get("info") or {}).get("location"))
    except Exception:
        _ADMIN["token"] = None
        return None
