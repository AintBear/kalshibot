import unittest
from unittest.mock import patch


class _JsonResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class TestWeatherEvents(unittest.TestCase):
    def tearDown(self):
        from app.services import weather_events
        weather_events._EVENT_CACHE.update({
            "refreshed_at": 0.0,
            "refreshed_at_iso": None,
            "cities": {},
            "errors": [],
        })

    def test_point_alerts_map_to_city_context(self):
        from app.services import weather_events

        payload = {
            "features": [
                {
                    "properties": {
                        "event": "Excessive Heat Warning",
                        "severity": "Severe",
                        "headline": "Excessive Heat Warning remains in effect",
                        "onset": "2026-05-08T12:00:00Z",
                        "expires": "2026-05-08T23:00:00Z",
                        "description": "Hot conditions.",
                        "areaDesc": "Phoenix Metro Area",
                    }
                }
            ]
        }

        with patch("app.services.weather_events._city_points", return_value={
            "PHX": {"state": "AZ", "lat": 33.4342, "lon": -112.0116, "station": "KPHX", "station_name": "Phoenix, AZ"}
        }), patch("app.services.weather_events.requests.get", return_value=_JsonResponse(payload)):
            result = weather_events.refresh_weather_events(force=True)
            context = weather_events.event_context_for_city("PHX")

        self.assertEqual(len(result["cities"]["PHX"]), 1)
        self.assertEqual(context["confidence_bonus"], 0.20)
        self.assertEqual(context["events"][0]["station"], "KPHX")

