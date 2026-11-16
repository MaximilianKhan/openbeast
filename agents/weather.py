"""weather: current conditions and a short forecast, for a place the model
names or for where the CALLER is right now.

Why a tool and not web_search: "weather Danville, CA" through a search engine
can land on Danville, Quebec — "CA" is also Canada's country code — and the
model then reports the wrong town with confidence. Here the place is resolved
deliberately and the answer always names the exact place it describes:

- `City, XX` where XX is a US state code (CA, TX, …) means that US state
  whenever the state has such a town ("Danville, CA" is California even for
  a caller in Montreal). Otherwise XX is read as an ISO country code, then
  as a Canadian province ("Berlin, DE", "Mumbai, IN", "Toronto, ON").
- `City, State` matches the state EXACTLY ("Charleston, VA" is not West
  Virginia); `City, Country` matches the geocoder's country name ("Tokyo,
  Japan"); any other qualifier matches whole words of the region.
- Several towns left after that: the one nearest the caller, weighted by
  size, when the caller's location is known, else the most populous — and
  the reply lists the runners-up so a wrong pick shows. Parks, airports and
  hills lose to any real town of that name.
- "Georgia" alone is the state or the country by the caller's position;
  with no position the tool lists both and asks.
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
import socket
import threading
import time
import unicodedata
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
_STATE_NAMES = {v.lower(): v for v in US_STATES.values()}
CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}
# Spelled-out countries match the geocoder's own English `country` field;
# these are only the common names that field does not use.
COUNTRY_ALIASES = {"usa": "US", "us": "US", "united states of america": "US", "america": "US",
                   "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB",
                   "scotland": "GB", "wales": "GB", "holland": "NL", "the netherlands": "NL",
                   "czech republic": "CZ", "korea": "KR", "uae": "AE"}
# A bare name that is BOTH a US state and a country. The geocoder returns the
# country as a feature but never the state, so the state gets a stand-in
# point (its capital) to compete on distance from the caller.
STATE_COUNTRY_CLASH = {"georgia": {"name": "Georgia", "admin1": "Georgia", "country": "United States",
                                   "country_code": "US", "feature_code": "ADM1", "latitude": 33.749,
                                   "longitude": -84.388, "population": 10_700_000,
                                   "note": "US state; forecast for Atlanta"}}
# Abandoned / destroyed / historical places are not where anyone is.
_GONE = {"PPLH", "PPLQ", "PPLW", "PPLCH"}

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


def fold(text: str) -> str:
    """Lower-case, accents stripped: 'São Paulo' and 'Sao Paulo' compare equal."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text or "")
                   if not unicodedata.combining(ch)).lower().strip()


def _readings(q: str) -> list:
    """What one qualifier after a comma may mean, most-preferred first. Each
    reading is a predicate over a candidate; the first one that leaves any
    candidate wins, so a US state outranks a country but never hides it."""
    u, lo = q.upper(), fold(q)

    def cc(code):
        return lambda c: (c.get("country_code") or "").upper() == code

    def region(name, code):  # EXACT admin1 — never a substring, never admin2
        return lambda c: fold(c.get("admin1") or "") == fold(name) and cc(code)(c)

    if len(q) == 2 and q.isalpha():
        out = [region(US_STATES[u], "US")] if u in US_STATES else []
        out.append(cc(u))
        if u in CA_PROVINCES:
            out.append(region(CA_PROVINCES[u], "CA"))
        return out
    out = [cc(COUNTRY_ALIASES[lo])] if lo in COUNTRY_ALIASES else []
    if lo in _STATE_NAMES:
        out.append(region(_STATE_NAMES[lo], "US"))
    out.append(lambda c: fold(c.get("country") or "") == lo)
    if lo not in _STATE_NAMES:
        # Free text ("Quebec", "Ile-de-France", "Bavaria"): whole words of the
        # region or county. Never for a US state name: 'virginia' is a word
        # of 'west virginia', and Washington County is not Washington state.
        word = re.compile(r"\b" + re.escape(lo) + r"\b")
        out.append(lambda c: any(word.search(fold(c.get(k) or "")) for k in ("admin1", "admin2")))
    return out


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


def _is_country(c: dict) -> bool:
    return (c.get("feature_code") or "").startswith("PCL")


def _rank(cands: list[dict], near) -> list[dict]:
    """Nearest the caller, weighted by size (population / distance²): a
    caller in Berlin NH gets Berlin NH, but "Sao Paulo, Brazil" asked from
    California is the city, not a hamlet in Amazonas that happens to be
    closer. No caller position: the most populous."""
    def pop(c):
        return c.get("population") or 500
    if near:
        return sorted(cands, key=lambda c: -pop(c) / max(
            _km(near, (c["latitude"], c["longitude"])), 10.0) ** 2)
    return sorted(cands, key=lambda c: -pop(c))


def resolve(text: str, near: tuple[float, float] | None = None, geo=None) -> tuple[dict | None, list[dict]]:
    """The best Open-Meteo place for `text`, and the other candidates.
    (None, []) is no match; (None, [a, b]) is ambiguous — ask which."""
    geo = geo or (lambda name: _get(GEO_URL, {"name": name, "count": 20, "language": "en",
                                              "format": "json"}).get("results") or [])
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return None, []
    name, quals = parts[0], parts[1:]
    cands = geo(name)
    # The geocoder also returns parks, airports and hills ("Tokyo Disney
    # Land", "Tokyo Hill"); and fuzzy neighbours ("Charlestown"). A real town
    # of exactly that name beats both; fall back only if there is none.
    towns = [c for c in cands if (c.get("feature_code") or "PPL")[:3] in ("PPL", "PCL")
             and c.get("feature_code") not in _GONE]
    cands = towns or cands
    cands = [c for c in cands if fold(c.get("name") or "") == fold(name)] or cands
    for q in quals:
        for keep in _readings(q):
            hit = [c for c in cands if keep(c)]
            if hit:
                cands = hit
                break
        else:
            return None, []
    clash = None if quals else STATE_COUNTRY_CLASH.get(fold(name))
    if clash and any(_is_country(c) for c in cands):
        if not near:
            return None, [dict(clash), next(c for c in cands if _is_country(c))]
        cands = [dict(clash)] + cands
    if not cands:
        return None, []
    cands = _rank(cands, near)
    return cands[0], cands[1:4]


def _label(c: dict) -> str:
    out = ", ".join(x for x in (c.get("name"), c.get("admin1"), c.get("country")) if x)
    return out + (f" ({c['note']})" if c.get("note") else "")


IMPERIAL_CC = {"US", "LR", "MM"}
# IANA zones of the 50 states: Canada and Mexico have zones of their own
# (America/Toronto, America/Vancouver, America/Tijuana), so the zone alone
# tells a caller in Detroit from one across the river in Windsor.
_US_TZ = {"America/New_York", "America/Detroit", "America/Chicago", "America/Menominee",
          "America/Denver", "America/Boise", "America/Phoenix", "America/Los_Angeles",
          "America/Anchorage", "America/Juneau", "America/Sitka", "America/Metlakatla",
          "America/Yakutat", "America/Nome", "America/Adak", "Pacific/Honolulu"}
_IMPERIAL_TZ = _US_TZ | {"Africa/Monrovia", "Asia/Yangon", "Asia/Rangoon"}
_UNIT_WORDS = {"imperial": True, "us": True, "fahrenheit": True, "f": True,
               "metric": False, "si": False, "celsius": False, "c": False}


def _imperial(units: str, country_code: str | None, tz: str | None) -> bool:
    """An explicit `units` wins; else the place's country; else (the caller's
    own position) the country its timezone belongs to."""
    word = _UNIT_WORDS.get((units or "").strip().lower())
    if word is not None:
        return word
    if country_code:
        return country_code.upper() in IMPERIAL_CC
    tz = tz or ""
    return tz in _IMPERIAL_TZ or tz.startswith(("America/Indiana/", "America/Kentucky/",
                                                "America/North_Dakota/"))


def _conv(v, kind: str, imperial: bool):
    if not imperial or not isinstance(v, (int, float)):
        return v
    if kind == "t":
        return round(v * 9 / 5 + 32, 1)
    if kind == "w":
        return round(v / 1.609344, 1)
    return round(v / 25.4, 2)


def _upstream(what: str, e: Exception) -> str:
    """An Open-Meteo failure as an answer the model can relay. A raised error
    became an HTTP 500 carrying a bare urllib message, which models retry in
    a loop (island.py says the same of network errors)."""
    code = getattr(e, "code", None)
    if code == 429:
        why = "it is rate-limiting this server (HTTP 429)"
    elif isinstance(code, int):
        why = f"it answered HTTP {code}"
    elif isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e):
        why = f"it did not answer within {TIMEOUT_S}s"
    elif isinstance(e, ValueError):
        why = "it sent a reply that is not valid JSON"
    else:
        why = "it is unreachable (" + str(getattr(e, "reason", e))[:120] + ")"
    return (f"Open-Meteo {what} failed: {why}. This is the weather service, not the place name: "
            "do not retry now; tell the user the weather service is unavailable at the moment.")


def weather(location: str = "", days: int = 3, *, near=None, units: str = "",
            geo=None, forecast=None) -> str:
    where = (location or "").strip()
    if not where and not near:
        return ("No location given, and the caller's position is unknown (Open WebUI: Settings -> "
                "Interface -> Allow User Location). Ask the user which place, as 'City, State' or "
                "'City, Country'.")
    if where:
        try:
            place, others = resolve(where, near, geo)
        except Exception as e:  # noqa: BLE001 — any upstream failure is one answer
            return _upstream("geocoding", e)
        if place is None and others:
            return (f"'{where}' is ambiguous and the caller's position is unknown: it is "
                    + " or ".join(f"{_label(c)} ({'a country' if _is_country(c) else 'a US state'})"
                                  for c in others)
                    + ". Ask the user which one, or call again as 'City, State' / 'City, Country'.")
        if place is None:
            return f"No place matches '{where}'. Ask for 'City, State' (US) or 'City, Country'."
    else:
        place = {"name": "your current location", "latitude": near[0], "longitude": near[1],
                 "country_code": ""}
        others = []
    try:
        days = max(1, min(int(days or 3), 7))
    except (TypeError, ValueError):
        days = 3
    # Always fetched metric; the units are decided after the reply, because
    # for the caller's own position the forecast's `timezone` is the cheap
    # (no extra request) answer to "which country is the caller in".
    try:
        fc = (forecast or (lambda p: _get(FORECAST_URL, p)))({
            "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
            "forecast_days": days,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm",
        })
        if not isinstance(fc, dict):
            raise ValueError("forecast reply is not a JSON object")
    except Exception as e:  # noqa: BLE001
        return _upstream("forecast", e)
    imperial = _imperial(units, place.get("country_code"), fc.get("timezone"))
    t, w, p = ("°F", "mph", "in") if imperial else ("°C", "km/h", "mm")
    def T(v): return _conv(v, "t", imperial)  # noqa: E704
    def W(v): return _conv(v, "w", imperial)  # noqa: E704
    def P(v): return _conv(v, "p", imperial)  # noqa: E704
    cur = fc.get("current") or {}
    head = f"Weather for {_label(place)} ({place['latitude']:.2f}, {place['longitude']:.2f})"
    if where and near:
        head += f", {_km(near, (place['latitude'], place['longitude'])):.0f} km from the caller"
    lines = [head + f"; local time {cur.get('time', '?')} {fc.get('timezone_abbreviation', '')}".rstrip(),
             f"Now: {WMO.get(cur.get('weather_code'), 'unknown')}, {T(cur.get('temperature_2m'))}{t} "
             f"(feels {T(cur.get('apparent_temperature'))}{t}), humidity {cur.get('relative_humidity_2m')}%, "
             f"wind {W(cur.get('wind_speed_10m'))} {w}, precipitation {P(cur.get('precipitation'))} {p}"]
    d = fc.get("daily") or {}
    for i, day in enumerate(d.get("time") or []):
        lines.append(f"{day}: {WMO.get((d.get('weather_code') or [None])[i], 'unknown')}, "
                     f"{T((d.get('temperature_2m_min') or [None])[i])}–{T((d.get('temperature_2m_max') or [None])[i])}{t}, "
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
_ADMIN = {"token": None, "until": 0.0, "retry_at": 0.0}
_ADMIN_LOCK = threading.Lock()
TOKEN_TTL_S = 600
# Open WebUI rate-limits signin PER EMAIL (15 per 3 minutes) and counts
# every attempt, so a stale WEBUI_ADMIN_PASSWORD retried on every weather
# call would lock the human admin out of their own browser login. One
# attempt per backoff window instead.
SIGNIN_BACKOFF_S = 300


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
    token is cached (see _admin_token). Any failure is "position unknown"."""
    if not user_id or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", user_id):
        return None
    http = http or (lambda url, body=None, tok=None: json.load(urllib.request.urlopen(
        urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                               {"Content-Type": "application/json",
                                **({"Authorization": "Bearer " + tok} if tok else {})}), timeout=5)))
    tok = _admin_token(conf_path, webui, http)
    if not tok:
        return None
    try:
        u = http(webui + "/api/v1/users/" + user_id, tok=tok)
    except Exception as e:  # noqa: BLE001 — any failure is "position unknown"
        # Only a 401 says the TOKEN is bad. A timeout, a 400 USER_NOT_FOUND
        # or a 5xx says nothing about it, and dropping it would cost a signin.
        if getattr(e, "code", None) == 401:
            with _ADMIN_LOCK:
                if _ADMIN["token"] == tok:
                    _ADMIN["token"] = None
        return None
    return parse_latlon(((u if isinstance(u, dict) else {}).get("info") or {}).get("location"))


def _admin_token(conf_path: str, webui: str, http) -> str | None:
    """The cached admin token, signing in at most once per TTL — and, after a
    failed signin, at most once per SIGNIN_BACKOFF_S. The lock makes
    concurrent first calls (the tool server is a thread pool) share one."""
    with _ADMIN_LOCK:
        now = time.monotonic()
        if _ADMIN["token"] and now < _ADMIN["until"]:
            return _ADMIN["token"]
        if now < _ADMIN["retry_at"]:
            return None
        c = _conf_values(conf_path, ("WEBUI_ADMIN_EMAIL", "WEBUI_ADMIN_PASSWORD"))
        if not c.get("WEBUI_ADMIN_EMAIL") or not c.get("WEBUI_ADMIN_PASSWORD"):
            return None                               # no request made, nothing to back off
        try:
            tok = http(webui + "/api/v1/auths/signin",
                       {"email": c["WEBUI_ADMIN_EMAIL"], "password": c["WEBUI_ADMIN_PASSWORD"]})["token"]
            if not isinstance(tok, str) or not tok:
                raise ValueError("signin reply has no token")
        except Exception:  # noqa: BLE001
            _ADMIN.update(token=None, retry_at=now + SIGNIN_BACKOFF_S)
            return None
        _ADMIN.update(token=tok, until=now + TOKEN_TTL_S, retry_at=0.0)
        return tok
