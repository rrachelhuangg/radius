"""Flask app for Vercel: web UI + JSON API around radius.py."""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

# Local dev reads settings from .env; on Vercel they come from the project's environment variables
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import auth  # noqa: E402 (reads the environment at import time)
import pins
from location import resolve
from radius import (
    NM_TO_MI, current_weather, flight_route, nearest_planes, nearest_trains, plane_trail, station_coords,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_RESULTS = 50
MAX_RADIUS_MI = 250

app = Flask(__name__)
auth.init_app(app)
app.register_blueprint(pins.bp)


@app.get("/")
def home():
    return send_from_directory(os.path.join(ROOT, "templates"), "index.html")


def find_location():
    """Location from the search box, browser geolocation, or Vercel's IP headers."""
    query = request.args.get("q", "").strip()
    if query:
        return resolve(query)

    lat, lon = request.args.get("lat"), request.args.get("lon")
    if lat and lon:
        return float(lat), float(lon), "your device's location"

    # Vercel adds the visitor's approximate location to every request
    lat, lon = request.headers.get("x-vercel-ip-latitude"), request.headers.get("x-vercel-ip-longitude")
    if lat and lon:
        city = unquote(request.headers.get("x-vercel-ip-city", ""))
        return float(lat), float(lon), f"{city} (from your IP)" if city else "your IP location"

    raise ValueError("Enter a location to search.")


def plane_kind(ac):
    """Best guess at who's flying: "Military", "Commercial" (airline/cargo), "Private", or None if unknown."""
    if ac.get("dbFlags", 0) & 1:  # adsb.lol's database flags known military aircraft
        return "Military"
    flight = (ac.get("flight") or "").strip()
    if not flight:
        return None
    # Airlines fly under their 3-letter ICAO code plus a flight number (AAL123, UPS5606);
    # private planes usually just broadcast their registration (N12345)
    if re.fullmatch(r"[A-Z]{3}\d{1,4}[A-Z]{0,2}", flight) and flight != (ac.get("r") or "").replace("-", ""):
        return "Commercial"
    return "Private"


def plane_row(ac):
    return {
        "callsign": (ac.get("flight") or "").strip() or ac.get("hex", "?"),
        "kind": plane_kind(ac),
        "hex": ac.get("hex"),
        "reg": ac.get("r"),
        "type": ac.get("t"),
        "distance_nm": round(ac["distance_nm"], 1),
        "alt_ft": ac.get("alt_baro"),
        "speed_kt": round(ac["gs"]) if ac.get("gs") is not None else None,
        "track": ac.get("track"),
        "lat": ac["lat"],
        "lon": ac["lon"],
    }


def train_row(t, coords):
    return {
        "number": t.get("trainNum"),
        "route": t.get("routeName"),
        "destination": t.get("destName"),
        "distance_mi": round(t["distance_mi"], 1),
        "speed_mph": round(t["velocity"]) if t.get("velocity") is not None else None,
        "lat": t["lat"],
        "lon": t["lon"],
        # Every stop on the run, in order, for drawing the route when the train is clicked
        "stops": [
            {"name": s.get("name"), "passed": s.get("status") == "Departed", "lat": c[0], "lon": c[1]}
            for s in t.get("stations", []) if (c := coords.get(s.get("code")))
        ],
    }


def count_arg(name, default=5):
    """How many results the user asked for, clamped to 0..MAX_RESULTS."""
    try:
        n = int(request.args.get(name, default))
    except ValueError:
        n = default
    return max(0, min(MAX_RESULTS, n))


def radius_arg(default=100):
    """Search radius in miles the user asked for, clamped to 1..MAX_RADIUS_MI."""
    try:
        r = int(request.args.get("radius", default))
    except ValueError:
        r = default
    return max(1, min(MAX_RADIUS_MI, r))


def collect(future, to_row):
    if future is None:  # user asked for 0
        return {"rows": [], "error": None}
    try:
        return {"rows": [to_row(x) for x in future.result()], "error": None}
    except Exception as e:
        return {"rows": [], "error": f"Lookup failed: {e}"}


@app.get("/api/nearby")
def nearby():
    try:
        lat, lon, label = find_location()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"Location lookup failed: {e}"), 502

    num_planes, num_trains = count_arg("planes"), count_arg("trains")
    radius_mi = radius_arg()
    with ThreadPoolExecutor(max_workers=4) as pool:
        planes = pool.submit(nearest_planes, lat, lon, num_planes, radius_mi / NM_TO_MI) if num_planes else None
        trains = pool.submit(nearest_trains, lat, lon, num_trains, radius_mi) if num_trains else None
        stations = pool.submit(station_coords) if num_trains else None
        weather = pool.submit(current_weather, lat, lon)
        try:
            weather = {"data": weather.result(), "error": None}
        except Exception as e:
            weather = {"data": None, "error": f"Lookup failed: {e}"}
        try:
            coords = stations.result() if stations else {}
        except Exception:
            coords = {}  # trains still show, just without routes
        return jsonify(
            location={"lat": lat, "lon": lon, "label": label},
            radius_mi=radius_mi,
            weather=weather,
            planes=collect(planes, plane_row),
            trains=collect(trains, lambda t: train_row(t, coords)),
        )


@app.get("/api/plane-path")
def plane_path():
    """Where a plane has been this flight, and its origin/destination airports if known."""
    hex_code = request.args.get("hex", "").lower()
    callsign = request.args.get("callsign", "").strip().upper()
    if not re.fullmatch(r"~?[0-9a-f]{6}", hex_code):
        return jsonify(error="Invalid aircraft ID."), 400
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify(error="Invalid position."), 400
    # Planes without a flight number show their hex ID as the callsign; there's no route to look up then
    has_callsign = re.fullmatch(r"[A-Z0-9]{2,8}", callsign) and callsign.lower() != hex_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        trail = pool.submit(plane_trail, hex_code)
        route = pool.submit(flight_route, callsign, lat, lon) if has_callsign else None
        try:
            trail = trail.result()
        except Exception:
            trail = []  # no history for this plane (e.g. it just appeared)
        try:
            route = route.result() if route else None
        except Exception:
            route = None
    return jsonify(trail=trail, route=route)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
