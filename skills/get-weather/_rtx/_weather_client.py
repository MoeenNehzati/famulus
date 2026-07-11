"""Fetch weather data for the get-weather skill."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from officina.runtime.python_machine_interface import PythonMachineInterface


def _die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class Interface(PythonMachineInterface):
    prog = "weather"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("--date", default=date.today().isoformat())
        parser.add_argument("--end-date")
        parser.add_argument("--location")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        start_date = args.date
        end_date = args.end_date or start_date
        if end_date < start_date:
            _die(f"--end-date ({end_date}) is before --date ({start_date})")

        if args.location:
            query = urllib.parse.urlencode({"name": args.location, "count": "1"})
            geo = _read_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}")
            results = geo.get("results") or []
            if not results:
                _die(f"Location not found: {args.location}")
            place = results[0]
            lat = place["latitude"]
            lon = place["longitude"]
            city = place["name"]
            timezone = place["timezone"]
        else:
            geo = _read_json("http://ip-api.com/json/")
            if geo.get("status", "fail") != "success":
                _die(f"Geolocation failed: {geo}")
            lat = geo["lat"]
            lon = geo["lon"]
            city = geo["city"]
            timezone = geo["timezone"]

        forecast_query = urllib.parse.urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m,weather_code",
                "timezone": timezone,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        forecast = _read_json(f"https://api.open-meteo.com/v1/forecast?{forecast_query}")
        if forecast.get("error") not in (None, False):
            _die(f"Forecast API error: {forecast.get('reason', 'unknown')}")

        output = {
            "start_date": start_date,
            "end_date": end_date,
            "location_query": city,
            "latitude": str(lat),
            "longitude": str(lon),
            "timezone": forecast.get("timezone"),
            "hourly": forecast.get("hourly"),
        }
        print(json.dumps(output, separators=(",", ":")))
        return 0


def main(argv: list[str] | None = None) -> int:
    interface = Interface()
    parser = interface.build_parser()
    return interface.run(parser.parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
