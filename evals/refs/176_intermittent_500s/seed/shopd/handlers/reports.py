"""/reports"""
from __future__ import annotations

from ..errors import BadRequest
from ..http import json_response


def register(router, s) -> None:
    def summary(_request, _params):
        return json_response(200, s.reports.summary())

    def daily(request, _params):
        day = request.query.get("date", "")
        try:
            body = s.reports.daily(day)
        except ValueError:
            raise BadRequest("date must be YYYY-MM-DD", code="invalid_date")
        return json_response(200, body)

    router.add("GET", "/reports/summary", summary, name="reports.summary")
    router.add("GET", "/reports/daily", daily, name="reports.daily")
