import csv
import sqlite3
from pathlib import Path

from shapely import wkb
from pyproj import Transformer

DATABASE = "sunis.db"

RESTAURANTS_CSV = Path("data/restaurants.csv")
BUILDINGS_CSV = Path("data/buildings.csv")

# Restaurants are stored in longitude/latitude.
# We convert them to EPSG:25832 so they match the building footprints.
transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)


def parse_bool(value):
    return str(value).strip().lower() in ["true", "t", "1", "yes", "y"]


def parse_restaurant_location(location_hex):
    """
    restaurants.csv has location as WKB/EWKB hex.
    Returns lat, lng, x, y.
    """
    point = wkb.loads(location_hex, hex=True)

    lng = point.x
    lat = point.y

    x, y = transformer.transform(lng, lat)

    return lat, lng, x, y


def parse_building_footprint(footprint_hex):
    """
    buildings.csv has footprint as WKB/EWKB hex.
    The footprint is already in EPSG:25832 meter coordinates.
    Returns WKT that Shapely can use later.
    """
    geom = wkb.loads(footprint_hex, hex=True)

    # Some geometries may have Z coordinates. Force 2D.
    if geom.geom_type == "Polygon":
        exterior = [(x, y) for x, y, *_ in geom.exterior.coords]
        interiors = [
            [(x, y) for x, y, *_ in ring.coords]
            for ring in geom.interiors
        ]

        from shapely.geometry import Polygon
        geom_2d = Polygon(exterior, interiors)
        return geom_2d.wkt

    if geom.geom_type == "MultiPolygon":
        from shapely.geometry import Polygon, MultiPolygon

        polygons = []
        for poly in geom.geoms:
            exterior = [(x, y) for x, y, *_ in poly.exterior.coords]
            interiors = [
                [(x, y) for x, y, *_ in ring.coords]
                for ring in poly.interiors
            ]
            polygons.append(Polygon(exterior, interiors))

        return MultiPolygon(polygons).wkt

    return geom.wkt


conn = sqlite3.connect(DATABASE)
cur = conn.cursor()

cur.execute("DROP TABLE IF EXISTS reviews")
cur.execute("DROP TABLE IF EXISTS buildings")
cur.execute("DROP TABLE IF EXISTS restaurants")

cur.execute("""
CREATE TABLE restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    location_hex TEXT,
    has_outdoor_seating INTEGER NOT NULL,
    google_place_id TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL
)
""")

cur.execute("""
CREATE TABLE buildings (
    id INTEGER PRIMARY KEY,
    footprint_wkt TEXT NOT NULL,
    height REAL NOT NULL,
    source TEXT
)
""")

cur.execute("""
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
)
""")

restaurants_inserted = 0

with open(RESTAURANTS_CSV, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:
        try:
            rest_id = int(row["id"])
            name = row["name"]
            address = row["address"]
            location_hex = row["location"]
            has_outdoor_seating = 1 if parse_bool(row["has_outdoor_seating"]) else 0
            google_place_id = row.get("google_place_id", "")

            lat, lng, x, y = parse_restaurant_location(location_hex)

            cur.execute("""
                INSERT INTO restaurants (
                    id,
                    name,
                    address,
                    location_hex,
                    has_outdoor_seating,
                    google_place_id,
                    lat,
                    lng,
                    x,
                    y
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rest_id,
                name,
                address,
                location_hex,
                has_outdoor_seating,
                google_place_id,
                lat,
                lng,
                x,
                y
            ))

            restaurants_inserted += 1

        except Exception as e:
            print("Could not import restaurant row:")
            print(row)
            print("Error:", e)


buildings_inserted = 0

with open(BUILDINGS_CSV, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    for row in reader:
        try:
            building_id = int(row["id"])
            footprint_hex = row["footprint"]
            height = float(row["height"]) if row["height"] else 10.0
            source = row.get("source", "")

            footprint_wkt = parse_building_footprint(footprint_hex)

            cur.execute("""
                INSERT INTO buildings (
                    id,
                    footprint_wkt,
                    height,
                    source
                )
                VALUES (?, ?, ?, ?)
            """, (
                building_id,
                footprint_wkt,
                height,
                source
            ))

            buildings_inserted += 1

        except Exception as e:
            print("Could not import building row:")
            print(row)
            print("Error:", e)


conn.commit()
conn.close()

print(f"Database created successfully: {DATABASE}")
print(f"Restaurants inserted: {restaurants_inserted}")
print(f"Buildings inserted: {buildings_inserted}")