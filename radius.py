#!/usr/bin/env python3
"""Show the 5 nearest aircraft (adsb.lol) and Amtrak trains (Amtraker) to you.

Usage:
    python3 radius.py                     # planes + trains, location from IP
    python3 radius.py 40.7128 -74.0060    # explicit latitude / longitude
    python3 radius.py --planes            # planes only
    python3 radius.py --trains            # trains only
"""

import argparse
import json
import math
import urllib.request

API_URL = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"
AMTRAK_URL = "https://api-v3.amtraker.com/v3/trains"
SEARCH_RADIUS_NM = 250  # max radius the API allows
NUM_PLANES = 5
NUM_TRAINS = 5
NM_TO_MI = 1.15078


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "radius.py"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def locate_by_ip():
    data = fetch_json("http://ip-api.com/json/")
    if data.get("status") != "success":
        raise RuntimeError("could not determine location from IP")
    print(f"Location from IP: {data['city']}, {data['regionName']}, {data['country']}")
    return data["lat"], data["lon"]


def haversine_nm(lat1, lon1, lat2, lon2):
    r_nm = 3440.065  # Earth radius in nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def nearest_planes(lat, lon, count=NUM_PLANES):
    data = fetch_json(API_URL.format(lat=lat, lon=lon, radius=SEARCH_RADIUS_NM))
    planes = []
    for ac in data.get("ac", []):
        if "lat" not in ac or "lon" not in ac:
            continue
        ac["distance_nm"] = haversine_nm(lat, lon, ac["lat"], ac["lon"])
        planes.append(ac)
    planes.sort(key=lambda ac: ac["distance_nm"])
    return planes[:count]


def nearest_trains(lat, lon, count=NUM_TRAINS):
    data = fetch_json(AMTRAK_URL)  # {train number: [train, ...]}
    trains = []
    for runs in data.values():
        for train in runs:
            if train.get("trainState") != "Active" or train.get("lat") is None:
                continue
            train["distance_mi"] = haversine_nm(lat, lon, train["lat"], train["lon"]) * NM_TO_MI
            trains.append(train)
    trains.sort(key=lambda t: t["distance_mi"])
    return trains[:count]


def print_planes(lat, lon):
    print("=== Nearest aircraft (adsb.lol) ===")
    planes = nearest_planes(lat, lon)
    if not planes:
        print(f"No aircraft found within {SEARCH_RADIUS_NM} nm.")
        return

    print(f"{'#':<3}{'Callsign':<10}{'Reg':<9}{'Type':<6}{'Dist (nm)':>10}{'Alt (ft)':>10}{'Speed (kt)':>12}")
    for i, ac in enumerate(planes, 1):
        callsign = (ac.get("flight") or "").strip() or ac.get("hex", "?")
        alt = ac.get("alt_baro", "?")
        speed = ac.get("gs")
        print(
            f"{i:<3}{callsign:<10}{ac.get('r', '-'):<9}{ac.get('t', '-'):<6}"
            f"{ac['distance_nm']:>10.1f}{str(alt):>10}"
            f"{(f'{speed:.0f}' if speed is not None else '-'):>12}"
        )


def print_trains(lat, lon):
    print("=== Nearest Amtrak trains (Amtraker) ===")
    trains = nearest_trains(lat, lon)
    if not trains:
        print("No active Amtrak trains found.")
        return

    print(f"{'#':<3}{'Train':<7}{'Route':<24}{'To':<22}{'Dist (mi)':>10}{'Speed (mph)':>13}")
    for i, t in enumerate(trains, 1):
        speed = t.get("velocity")
        print(
            f"{i:<3}{str(t.get('trainNum', '?')):<7}{(t.get('routeName') or '-')[:23]:<24}"
            f"{(t.get('destName') or '-')[:21]:<22}{t['distance_mi']:>10.1f}"
            f"{(f'{speed:.0f}' if speed is not None else '-'):>13}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lat", nargs="?", type=float, help="latitude (default: from IP)")
    parser.add_argument("lon", nargs="?", type=float, help="longitude (default: from IP)")
    parser.add_argument("--planes", action="store_true", help="show only planes")
    parser.add_argument("--trains", action="store_true", help="show only trains")
    args = parser.parse_args()

    if (args.lat is None) != (args.lon is None):
        parser.error("give both latitude and longitude, or neither")
    lat, lon = (args.lat, args.lon) if args.lat is not None else locate_by_ip()
    show_both = not args.planes and not args.trains

    print(f"Searching near {lat:.4f}, {lon:.4f}\n")
    if args.planes or show_both:
        print_planes(lat, lon)
    if show_both:
        print()
    if args.trains or show_both:
        print_trains(lat, lon)


if __name__ == "__main__":
    main()
