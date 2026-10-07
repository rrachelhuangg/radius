"""Flask app for Vercel: web UI + JSON API around radius.py."""

import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

from flask import Flask, jsonify, request, send_from_directory

from location import resolve
from radius import current_weather, nearest_planes, nearest_trains

ROOT = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)


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


def plane_row(ac):
    return {
        "callsign": (ac.get("flight") or "").strip() or ac.get("hex", "?"),
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


def train_row(t):
    return {
        "number": t.get("trainNum"),
        "route": t.get("routeName"),
        "destination": t.get("destName"),
        "distance_mi": round(t["distance_mi"], 1),
        "speed_mph": round(t["velocity"]) if t.get("velocity") is not None else None,
        "lat": t["lat"],
        "lon": t["lon"],
    }


def collect(future, to_row):
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

    with ThreadPoolExecutor(max_workers=3) as pool:
        planes = pool.submit(nearest_planes, lat, lon)
        trains = pool.submit(nearest_trains, lat, lon)
        weather = pool.submit(current_weather, lat, lon)
        try:
            weather = {"data": weather.result(), "error": None}
        except Exception as e:
            weather = {"data": None, "error": f"Lookup failed: {e}"}
        return jsonify(
            location={"lat": lat, "lon": lon, "label": label},
            weather=weather,
            planes=collect(planes, plane_row),
            trains=collect(trains, train_row),
        )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
