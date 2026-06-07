from flask import Flask, jsonify, request
import sqlite3
import re
from datetime import datetime

import numpy as np
import pvlib
import pytz

from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.ops import unary_union
from shapely.wkt import loads


app = Flask(__name__)

DATABASE = "sunis.db"


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def get_sun_position(dt, lat=55.6761, lon=12.5683):
    """
    Calculates current sun position for Copenhagen.
    Returns azimuth and elevation in degrees.
    """
    location = pvlib.location.Location(lat, lon, tz="Europe/Copenhagen")
    solpos = location.get_solarposition(dt)

    azimuth = float(solpos["azimuth"].values[0])
    elevation = float(solpos["apparent_elevation"].values[0])

    return azimuth, elevation


def polygon_shadow(poly, building_height, azimuth, elevation):
    """
    Calculates shadow polygon for one building polygon.
    Coordinates must be in meters, EPSG:25832.
    """
    if elevation <= 0:
        return None

    if poly.is_empty:
        return None

    shadow_length = building_height / np.tan(np.radians(elevation))

    # pvlib azimuth is clockwise from north.
    # Shadow direction is opposite the sun.
    shadow_direction = np.radians(azimuth + 180)

    dx = shadow_length * np.sin(shadow_direction)
    dy = shadow_length * np.cos(shadow_direction)

    shifted_coords = []

    for coord in poly.exterior.coords:
        x = coord[0]
        y = coord[1]
        shifted_coords.append((x + dx, y + dy))

    shifted_poly = Polygon(shifted_coords)

    # Covers area between building and shifted polygon
    shadow = unary_union([poly, shifted_poly]).convex_hull

    return shadow


def calculate_shadow(footprint_wkt, building_height, azimuth, elevation):
    """
    Handles both Polygon and MultiPolygon building footprints.
    """
    if elevation <= 0:
        return None

    geom = loads(footprint_wkt)

    shadows = []

    if isinstance(geom, Polygon):
        shadow = polygon_shadow(geom, building_height, azimuth, elevation)
        if shadow is not None:
            shadows.append(shadow)

    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            shadow = polygon_shadow(poly, building_height, azimuth, elevation)
            if shadow is not None:
                shadows.append(shadow)

    if not shadows:
        return None

    return unary_union(shadows)


def table_exists(cur, table_name):
    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
    """, (table_name,))

    return cur.fetchone() is not None


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/ping")
def ping():
    return jsonify({"status": "ok"})


@app.route("/api/venues")
def get_venues():
    search_query = request.args.get("q", "")

    # Current Copenhagen time
    cph_tz = pytz.timezone("Europe/Copenhagen")
    now = datetime.now(cph_tz)
    # Her kan man teste om det sol cal virker. 
    # now = cph_tz.localize(datetime(2026, 5, 27, 18, 30))

    azimuth, elevation = get_sun_position(now)
    sun_is_up = elevation > 0

    conn = get_db_connection()
    cur = conn.cursor()

    # Hent restauranter/barer fra databasen
    cur.execute("""
        SELECT 
            id,
            name,
            address,
            lat,
            lng,
            x,
            y,
            has_outdoor_seating
        FROM restaurants
    """)

    restaurants = cur.fetchall()

    has_buildings_table = table_exists(cur, "buildings")

    buildings = []

    if has_buildings_table:
        cur.execute("""
            SELECT
                id,
                height,
                footprint_wkt
            FROM buildings
            WHERE footprint_wkt IS NOT NULL
        """)

        building_rows = cur.fetchall()

        for building in building_rows:
            try:
                height = building["height"]

                if height is None:
                    height = 10.0

                geom = loads(building["footprint_wkt"])

                buildings.append({
                    "id": building["id"],
                    "height": float(height),
                    "footprint_wkt": building["footprint_wkt"],
                    "geometry": geom
                })

            except Exception as e:
                print("Could not load building:", building["id"])
                print("Error:", e)

    results = []

    for row in restaurants:
        rest_id = row["id"]
        name = row["name"]
        address = row["address"]
        lat = row["lat"]
        lng = row["lng"]
        x = row["x"]
        y = row["y"]

        restaurant_point = Point(x, y)

        if not sun_is_up:
            in_shadow = True

        else:
            in_shadow = False
            shadows = []

            # Find relevante bygninger tæt på baren
            # Find relevante bygninger tæt på baren.
            # Maks skyggelængde afhænger af solens elevation:
            # Ved 53° og 20m bygning = ~15m shadow
            for building in buildings:
                try:
                    building_geom = building["geometry"]

                    # Dynamisk filter baseret på bygningens faktiske skyggelængde
                    max_shadow = building["height"] / np.tan(np.radians(max(elevation, 5)))
                    if building_geom.distance(restaurant_point) > max_shadow + 10:
                        continue

                    shadow = calculate_shadow(
                        building["footprint_wkt"],
                        building["height"],
                        azimuth,
                        elevation
                    )

                    # Tjek med det samme — ingen grund til at merge alle skygger
                    if shadow is not None and shadow.contains(restaurant_point):
                        in_shadow = True
                        break

                except Exception as e:
                    print("Could not calculate shadow for building:", building["id"])
                    print("Error:", e)

            if shadows:
                combined_shadow = unary_union(shadows)
                in_shadow = combined_shadow.contains(restaurant_point)
  

        results.append({
            "id": rest_id,
            "name": name,
            "address": address,
            "lat": lat,
            "lng": lng,
            "has_outdoor_seating": bool(row["has_outdoor_seating"]),
            "in_shadow": bool(in_shadow),
            "sun_is_up": bool(sun_is_up),
            "sun_elevation": round(elevation, 1),
            "sun_azimuth": round(azimuth, 1)
        })

    conn.close()

    # Regular expression matching
    if search_query:
        try:
            pattern = re.compile(search_query, re.IGNORECASE)

            results = [
                venue for venue in results
                if pattern.search(venue["name"]) or pattern.search(venue["address"])
            ]

        except re.error:
            return jsonify({"error": "Invalid regular expression"}), 400

    return jsonify(results)


@app.route("/api/reviews/<int:restaurant_id>")
def get_reviews(restaurant_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            id,
            restaurant_id,
            username,
            rating,
            comment,
            created_at
        FROM reviews
        WHERE restaurant_id = ?
        ORDER BY created_at DESC
    """, (restaurant_id,))

    rows = cur.fetchall()
    conn.close()

    reviews = []

    for row in rows:
        reviews.append({
            "id": row["id"],
            "restaurant_id": row["restaurant_id"],
            "username": row["username"],
            "rating": row["rating"],
            "comment": row["comment"],
            "created_at": row["created_at"]
        })

    return jsonify(reviews)


@app.route("/api/reviews", methods=["POST"])
def add_review():
    data = request.get_json()

    restaurant_id = data.get("restaurant_id")
    username = data.get("username")
    rating = data.get("rating")
    comment = data.get("comment", "")

    if not restaurant_id or not username or not rating:
        return jsonify({
            "error": "restaurant_id, username and rating are required"
        }), 400

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO reviews (
            restaurant_id,
            username,
            rating,
            comment
        )
        VALUES (?, ?, ?, ?)
    """, (
        restaurant_id,
        username,
        rating,
        comment
    ))

    conn.commit()
    conn.close()

    return jsonify({"status": "review added"})


if __name__ == "__main__":
    app.run(debug=True, port=5001)