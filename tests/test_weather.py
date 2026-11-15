"""The weather tool resolves the place deliberately: 'Danville, CA' is
California, never Canada; a bare name picks the town nearest the CALLER (who
travels — there is no fixed home); no place and no caller position means the
tool asks. No network: the geocoder and forecast are stubs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import weather  # noqa: E402

# Real Open-Meteo geocoding replies (count=20, language=en), captured
# 2026-11-15 and trimmed to the fields the tool reads. Keyed by the name
# the geocoder was asked for; the geocoder folds accents ("Sao Paulo" finds
# São Paulo), so lookups here fold the same way.
REAL = json.loads((Path(__file__).parent / "fixtures" / "open_meteo_geocoding.json")
                  .read_text(encoding="utf-8"))


def real_geo(name):
    return [dict(c) for c in REAL.get(weather.fold(name).title(), [])]


TOKYO_CALLER = (35.68, 139.76)
DANVILLE_CA = (37.822, -121.999)
TBILISI_CALLER = (41.72, 44.79)

DANVILLES = [
    {"name": "Danville", "admin1": "Virginia", "country": "United States", "country_code": "US",
     "latitude": 36.586, "longitude": -79.395, "population": 42590},
    {"name": "Danville", "admin1": "California", "country": "United States", "country_code": "US",
     "latitude": 37.822, "longitude": -121.999, "population": 44396},
    {"name": "Danville", "admin1": "Quebec", "country": "Canada", "country_code": "CA",
     "latitude": 45.783, "longitude": -72.016, "population": 3851},
    {"name": "Danville", "admin1": "Illinois", "country": "United States", "country_code": "US",
     "latitude": 40.124, "longitude": -87.630, "population": 30479},
]
geo = lambda name: [dict(c) for c in DANVILLES] if name.lower() == "danville" else []  # noqa: E731
SAN_RAMON = (37.780, -121.978)       # next to Danville, CA
RICHMOND_VA = (37.541, -77.436)
MONTREAL = (45.502, -73.567)


def _fc(params, tz="America/Los_Angeles"):
    """Open-Meteo's forecast reply shape; like the real API it answers in the
    units asked for (default metric) and names the place's IANA timezone."""
    _fc.last = params
    f = params.get("temperature_unit") == "fahrenheit"
    mph = params.get("wind_speed_unit") == "mph"
    inch = params.get("precipitation_unit") == "inch"
    return {"timezone": tz, "timezone_abbreviation": "PST",
            "current": {"time": "2026-11-15T11:00", "temperature_2m": 60.8 if f else 16.0,
                        "apparent_temperature": 59.0 if f else 15.0, "relative_humidity_2m": 55,
                        "precipitation": 0.04 if inch else 1.0, "weather_code": 1,
                        "wind_speed_10m": 6.2 if mph else 10.0},
            "daily": {"time": ["2026-11-15"], "weather_code": [1],
                      "temperature_2m_min": [44.6 if f else 7.0],
                      "temperature_2m_max": [64.4 if f else 18.0], "precipitation_probability_max": [5]}}


def _fc_in(tz):
    return lambda params: _fc(params, tz)


def _where(text, near=None):
    place, _ = weather.resolve(text, near, real_geo)
    return place and (place["name"], place.get("admin1"), place["country_code"])


def test_qualifier_forms_on_real_geocoder_output():
    assert _where("Danville, CA") == ("Danville", "California", "US")
    assert _where("Danville, California") == ("Danville", "California", "US")
    assert _where("Danville, Quebec, Canada") == ("Danville", "Quebec", "CA")
    assert _where("Danville, CA, USA") == ("Danville", "California", "US")
    assert _where("Danville, ca") == ("Danville", "California", "US")


def test_spelled_out_country_matches_the_country_field():
    # These used to fall through to an admin1 substring filter and answer
    # "No place matches" — the very retry the error text told the model to try.
    assert _where("Tokyo, Japan") == ("Tokyo", "Tokyo", "JP")
    assert _where("Berlin, Germany") == ("Berlin", "State of Berlin", "DE")
    assert _where("Sao Paulo, Brazil") == ("São Paulo", "São Paulo", "BR")
    assert _where("São Paulo, Brazil") == ("São Paulo", "São Paulo", "BR")
    assert _where("Tbilisi, Georgia") == ("Tbilisi", "Tbilisi", "GE")
    # ...even for a caller far away, where the nearest same-named village in
    # that country is a hamlet (São Paulo, Amazonas) or a theme park (Chiba).
    assert _where("Tokyo, Japan", DANVILLE_CA) == ("Tokyo", "Tokyo", "JP")
    assert _where("Sao Paulo, Brazil", DANVILLE_CA) == ("São Paulo", "São Paulo", "BR")


def test_state_match_is_exact_not_a_substring():
    # 'virginia' is inside 'west virginia', 'kansas' inside 'arkansas'.
    assert _where("Charleston, VA") is None
    assert _where("Charleston, Virginia") is None
    assert _where("Charleston, WV") == ("Charleston", "West Virginia", "US")
    assert _where("Arkadelphia, KS") is None
    assert _where("Arkadelphia, AR") == ("Arkadelphia", "Arkansas", "US")
    # admin2 is the county: Washington County is not Washington state.
    assert _where("Danville, Washington") == ("Danville", "Washington", "US")
    assert _where("Danville, WA") == ("Danville", "Washington", "US")


def test_two_letter_code_falls_back_from_state_to_country():
    assert _where("Berlin, DE") == ("Berlin", "State of Berlin", "DE")    # not Delaware
    assert _where("Mumbai, IN") == ("Mumbai", "Maharashtra", "IN")        # not Indiana
    assert _where("Toronto, ON") == ("Toronto", "Ontario", "CA")         # a province
    # A state that HAS the town still wins, wherever the caller is.
    assert _where("Berlin, NH", TOKYO_CALLER) == ("Berlin", "New Hampshire", "US")
    assert _where("Danville, CA", TOKYO_CALLER) == ("Danville", "California", "US")


def test_bare_name_skips_non_towns():
    # A bare "Tokyo" from California must not be Tokyo Hill, Texas (a hill).
    assert _where("Tokyo", DANVILLE_CA) == ("Tokyo", "Tokyo", "JP")
    # A caller in Berlin, New Hampshire means their own town...
    assert _where("Berlin", (44.47, -71.18)) == ("Berlin", "New Hampshire", "US")
    # ...and a caller in Europe means the capital.
    assert _where("Berlin", (48.85, 2.35)) == ("Berlin", "State of Berlin", "DE")


def test_georgia_alone_is_resolved_by_the_caller_or_asked():
    out = weather.weather("Georgia", near=None, geo=real_geo, forecast=_fc)
    assert "US state" in out and "country" in out and "Weather for" not in out
    place, others = weather.resolve("Georgia", TBILISI_CALLER, real_geo)
    assert place["country_code"] == "GE"
    place, others = weather.resolve("Georgia", DANVILLE_CA, real_geo)
    assert place["country_code"] == "US" and place["admin1"] == "Georgia"
    assert any(o["country_code"] == "GE" for o in others)   # the other one is listed


def test_ca_is_california_not_canada():
    for near in (None, MONTREAL):          # even a caller in Montreal: "CA" is the state
        place, _ = weather.resolve("Danville, CA", near, geo)
        assert place["admin1"] == "California" and place["country_code"] == "US"


def test_canada_only_when_spelled_out():
    place, _ = weather.resolve("Danville, Quebec", None, geo)
    assert place["admin1"] == "Quebec"
    place, _ = weather.resolve("Danville, Canada", None, geo)
    assert place["country_code"] == "CA"


def test_bare_name_picks_the_town_nearest_the_caller():
    assert weather.resolve("Danville", SAN_RAMON, geo)[0]["admin1"] == "California"
    assert weather.resolve("Danville", RICHMOND_VA, geo)[0]["admin1"] == "Virginia"
    assert weather.resolve("Danville", MONTREAL, geo)[0]["admin1"] == "Quebec"
    # Position unknown: the most populous, and the reply lists the others.
    place, others = weather.resolve("Danville", None, geo)
    assert place["admin1"] == "California" and len(others) == 3


def test_no_place_uses_the_callers_position_and_never_a_fixed_home():
    out = weather.weather("", near=SAN_RAMON, geo=geo, forecast=_fc)
    assert "your current location" in out and _fc.last["latitude"] == SAN_RAMON[0]
    out = weather.weather("", near=None, geo=geo, forecast=_fc)
    assert "Allow User Location" in out and "Ask the user" in out


def test_reply_names_the_place_and_distance():
    out = weather.weather("Danville", near=SAN_RAMON, geo=geo, forecast=_fc)
    assert out.startswith("Weather for Danville, California, United States")
    assert "km from the caller" in out and "Other places with that name" in out
    assert "Now: mainly clear, 60.8°F" in out and "6.2 mph" in out
    assert weather.weather("Nowhereville", geo=geo, forecast=_fc).startswith("No place matches")


PARIS = (48.857, 2.352)


def test_units_follow_the_place_and_the_callers_own_country():
    # A named place: its country decides.
    assert "°C" in weather.weather("Tokyo, Japan", near=DANVILLE_CA, geo=real_geo,
                                   forecast=_fc_in("Asia/Tokyo"))
    assert "°F" in weather.weather("Danville, CA", near=PARIS, geo=real_geo, forecast=_fc)
    # The caller's own position: a caller in Paris is metric, not imperial.
    out = weather.weather("", near=PARIS, geo=geo, forecast=_fc_in("Europe/Paris"))
    assert "16.0°C" in out and "10.0 km/h" in out and "°F" not in out
    out = weather.weather("", near=SAN_RAMON, geo=geo, forecast=_fc_in("America/Los_Angeles"))
    assert "60.8°F" in out and "mph" in out
    out = weather.weather("", near=(39.77, -86.16), geo=geo,
                          forecast=_fc_in("America/Indiana/Indianapolis"))
    assert "°F" in out
    out = weather.weather("", near=(43.65, -79.38), geo=geo, forecast=_fc_in("America/Toronto"))
    assert "°C" in out


def test_units_override():
    out = weather.weather("Danville, CA", units="metric", geo=real_geo, forecast=_fc)
    assert "16.0°C" in out and "°F" not in out
    out = weather.weather("", near=PARIS, units="imperial", geo=geo, forecast=_fc_in("Europe/Paris"))
    assert "60.8°F" in out and "°C" not in out


def test_mcp_weather_passes_units_through(monkeypatch):
    import inspect
    import mcp_server
    assert "units" in inspect.signature(mcp_server.weather).parameters
    seen = {}
    monkeypatch.setattr(weather, "weather", lambda *a, **k: seen.update(a=a, k=k) or "ok")
    monkeypatch.setattr(mcp_server._island, "web_refusal", lambda name: "")
    assert mcp_server.weather("Paris, FR", 2, units="imperial") == "ok"
    assert seen["k"]["units"] == "imperial" and seen["a"][:2] == ("Paris, FR", 2)


def test_parse_latlon():
    assert weather.parse_latlon("37.822, -121.999") == (37.822, -121.999)
    for bad in ("", None, "Location not available", "91, 0", "1,2,3", "37.8;-122"):
        assert weather.parse_latlon(bad) is None


def test_caller_location_reads_the_users_webui_info(tmp_path):
    conf = tmp_path / "openbeast.conf"
    conf.write_text('WEBUI_ADMIN_EMAIL=admin@localhost\nWEBUI_ADMIN_PASSWORD="s3cret"\n')
    seen = []

    def http(url, body=None, tok=None):
        seen.append((url, body, tok))
        if url.endswith("/auths/signin"):
            assert body == {"email": "admin@localhost", "password": "s3cret"}
            return {"token": "T"}
        return {"info": {"location": "37.822, -121.999"}}
    _fresh_cache()
    uid = "8f0c1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b"
    assert weather.caller_location(uid, str(conf), http=http) == (37.822, -121.999)
    assert seen[-1] == (f"http://127.0.0.1:3000/api/v1/users/{uid}", None, "T")
    # A user id that is not a plain id never reaches a URL; no creds, no lookup.
    assert weather.caller_location("../../auths", str(conf), http=http) is None
    assert weather.caller_location(None, str(conf), http=http) is None
    _fresh_cache()
    assert weather.caller_location(uid, str(tmp_path / "absent.conf"), http=http) is None


UID = "8f0c1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b"


def _http_err(code):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://127.0.0.1:3000/x", code, "err", {}, io.BytesIO(b""))


class FakeWebUI:
    """Open WebUI's signin + user lookup, counting signins (Open WebUI
    rate-limits them per email: 15 per 3 minutes, the human's login too)."""

    def __init__(self, signin_error=None, lookup_error=None, delay=0.0):
        self.signins, self.lookups = 0, 0
        self.signin_error, self.lookup_error, self.delay = signin_error, lookup_error, delay

    def __call__(self, url, body=None, tok=None):
        import time
        if url.endswith("/auths/signin"):
            self.signins += 1
            time.sleep(self.delay)
            if self.signin_error:
                raise self.signin_error
            return {"token": f"T{self.signins}"}
        self.lookups += 1
        if self.lookup_error:
            raise self.lookup_error
        return {"info": {"location": "37.822, -121.999"}}


def _conf(tmp_path):
    conf = tmp_path / "openbeast.conf"
    conf.write_text("WEBUI_ADMIN_EMAIL=admin@localhost\nWEBUI_ADMIN_PASSWORD=stale\n")
    return str(conf)


def _fresh_cache():
    weather._ADMIN.update(token=None, until=0.0, retry_at=0.0)


def test_failed_signin_backs_off_instead_of_hammering(tmp_path, monkeypatch):
    _fresh_cache()
    conf, ui = _conf(tmp_path), FakeWebUI(signin_error=_http_err(400))   # stale password
    for _ in range(20):
        assert weather.caller_location(UID, conf, http=ui) is None
    assert ui.signins == 1                          # not 20: the 15/3-min limit is never hit
    # After the backoff, one more try — and a fixed password works.
    later = weather.time.monotonic() + weather.SIGNIN_BACKOFF_S + 1
    monkeypatch.setattr(weather.time, "monotonic", lambda: later)
    ui.signin_error = None
    assert weather.caller_location(UID, conf, http=ui) == (37.822, -121.999)
    assert ui.signins == 2


def test_lookup_failure_keeps_a_good_token(tmp_path):
    _fresh_cache()
    conf, ui = _conf(tmp_path), FakeWebUI(lookup_error=TimeoutError("timed out"))
    for _ in range(5):
        assert weather.caller_location(UID, conf, http=ui) is None
    ui.lookup_error = _http_err(400)                # e.g. USER_NOT_FOUND
    assert weather.caller_location(UID, conf, http=ui) is None
    assert ui.signins == 1
    # Only a 401 (the token itself is bad) drops it.
    ui.lookup_error = _http_err(401)
    assert weather.caller_location(UID, conf, http=ui) is None
    ui.lookup_error = None
    assert weather.caller_location(UID, conf, http=ui) == (37.822, -121.999)
    assert ui.signins == 2


def test_concurrent_first_calls_share_one_signin(tmp_path):
    import threading
    _fresh_cache()
    conf, ui = _conf(tmp_path), FakeWebUI(delay=0.05)
    out = []
    threads = [threading.Thread(target=lambda: out.append(weather.caller_location(UID, conf, http=ui)))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out == [(37.822, -121.999)] * 8 and ui.signins == 1


def _raiser(exc):
    def f(*_a, **_k):
        raise exc
    return f


def test_upstream_failures_are_a_worded_answer_not_a_500():
    import io
    import socket
    import urllib.error
    failures = [
        urllib.error.HTTPError("https://geocoding-api.open-meteo.com/v1/search", 429,
                               "Too Many Requests", {}, io.BytesIO(b"")),
        urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")),
        TimeoutError("The read operation timed out"),
        ValueError("Expecting value: line 1 column 1 (char 0)"),   # a non-JSON body
    ]
    for exc in failures:
        for kw in ({"geo": _raiser(exc), "forecast": _fc},
                   {"geo": real_geo, "forecast": _raiser(exc)}):
            out = weather.weather("Danville, CA", **kw)
            assert out.startswith("Open-Meteo"), out
            assert "do not retry" in out and "Traceback" not in out
    out = weather.weather("", near=SAN_RAMON, geo=geo, forecast=_raiser(failures[0]))
    assert "rate-limit" in out
