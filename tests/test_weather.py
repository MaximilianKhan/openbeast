"""The weather tool resolves the place deliberately: 'Danville, CA' is
California, never Canada; a bare name picks the town nearest the CALLER (who
travels — there is no fixed home); no place and no caller position means the
tool asks. No network: the geocoder and forecast are stubs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import weather  # noqa: E402

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


def _fc(params):
    _fc.last = params
    return {"timezone_abbreviation": "PST",
            "current": {"time": "2026-11-15T11:00", "temperature_2m": 61, "apparent_temperature": 60,
                        "relative_humidity_2m": 55, "precipitation": 0, "weather_code": 1, "wind_speed_10m": 4},
            "daily": {"time": ["2026-11-15"], "weather_code": [1], "temperature_2m_min": [44],
                      "temperature_2m_max": [64], "precipitation_probability_max": [5]}}


def test_parse_place():
    assert weather.parse_place("Danville, CA") == ("Danville", "California", "US")
    assert weather.parse_place("Danville, California") == ("Danville", "California", "US")
    assert weather.parse_place("Danville, Quebec, Canada") == ("Danville", "Quebec", "CA")
    assert weather.parse_place("Paris, FR") == ("Paris", None, "FR")
    assert weather.parse_place("Austin, TX, USA") == ("Austin", "Texas", "US")


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
    assert _fc.last["temperature_unit"] == "fahrenheit"
    assert weather.weather("Nowhereville", geo=geo, forecast=_fc).startswith("No place matches")


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
    weather._ADMIN.update(token=None, until=0)
    uid = "8f0c1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b"
    assert weather.caller_location(uid, str(conf), http=http) == (37.822, -121.999)
    assert seen[-1] == (f"http://127.0.0.1:3000/api/v1/users/{uid}", None, "T")
    # A user id that is not a plain id never reaches a URL; no creds, no lookup.
    assert weather.caller_location("../../auths", str(conf), http=http) is None
    assert weather.caller_location(None, str(conf), http=http) is None
    weather._ADMIN.update(token=None, until=0)
    assert weather.caller_location(uid, str(tmp_path / "absent.conf"), http=http) is None
