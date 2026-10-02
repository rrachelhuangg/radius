"""Turn free-form location text into (lat, lon, label).

Accepted formats:
    40.7128, -74.0060                 decimal degrees (comma or space separated)
    40.7128N 74.0060W                 decimal degrees with hemispheres
    40°42'46"N 74°0'21"W              degrees / minutes / seconds
    40 42 46 N 74 0 21 W              DMS with spaces
    https://maps.google.com/...@40.7,-74.0,12z   Google Maps URL
    10001 / Chicago / 1600 Pennsylvania Ave      anything else is geocoded
"""

import re
import urllib.parse

from radius import fetch_json

GEOCODE_URL = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q={q}"
NUM = r"\d+(?:\.\d+)?"
MAPS_URL_RE = re.compile(rf"@(-?{NUM}),\s*(-?{NUM})")
COORD_CHARS_RE = re.compile(r"^[\d\s.\-+°º'′\"″:NSEW]+$")


def _parse_part(text):
    """'40°42'46"N' / '-74.006' / '74 0 21 W' -> (signed degrees, hemisphere or None)."""
    text = text.strip()
    if not text or not COORD_CHARS_RE.match(text):
        return None
    hemis = re.findall(r"[NSEW]", text)
    nums = [float(n) for n in re.findall(NUM, text)]
    if len(hemis) > 1 or not 1 <= len(nums) <= 3 or any(n >= 60 for n in nums[1:]):
        return None
    value = sum(n / 60**i for i, n in enumerate(nums))
    hemi = hemis[0] if hemis else None
    if text.startswith("-") or hemi in ("S", "W"):
        value = -value
    return value, hemi


def _split(text):
    """Split coordinate text into its two halves, or None if it doesn't look like a pair."""
    if text.count(",") == 1:
        return text.split(",")
    for pattern in (r"^(.*?[NS])\s*(.*[EW])$", r"^([NS].*?)\s*([EW].*)$"):
        m = re.match(pattern, text)
        if m:
            return m.groups()
    tokens = text.split()
    if len(tokens) in (2, 4, 6):  # decimal, deg+min, or deg+min+sec per half
        half = len(tokens) // 2
        return " ".join(tokens[:half]), " ".join(tokens[half:])
    return None


def parse_coordinates(text):
    """Return (lat, lon) if text is a coordinate pair in any supported format, else None."""
    m = MAPS_URL_RE.search(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    else:
        parts = _split(text.strip().upper())
        if not parts:
            return None
        a, b = _parse_part(parts[0]), _parse_part(parts[1])
        if not a or not b:
            return None
        (lat, h1), (lon, h2) = a, b
        if h1 in ("E", "W") or h2 in ("N", "S"):  # written longitude first
            lat, lon = lon, lat
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def resolve(query):
    """Coordinates or place name -> (lat, lon, label). Raises ValueError if not found."""
    coords = parse_coordinates(query)
    if coords:
        return coords[0], coords[1], f"{coords[0]:.4f}, {coords[1]:.4f}"
    results = fetch_json(GEOCODE_URL.format(q=urllib.parse.quote(query)))
    if not results:
        raise ValueError(f"Couldn't find a location matching '{query}'")
    place = results[0]
    return float(place["lat"]), float(place["lon"]), place["display_name"]
