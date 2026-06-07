import psycopg2
import pvlib
import numpy as np
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from shapely.wkt import loads
from datetime import datetime
import pytz
from config import DB_CONFIG


def get_sun_position(dt, lat=55.6761, lon=12.5683):
    location = pvlib.location.Location(lat, lon, tz='Europe/Copenhagen')
    solpos = location.get_solarposition(dt)
    azimuth = solpos['azimuth'].values[0]
    elevation = solpos['apparent_elevation'].values[0]
    return azimuth, elevation


def calculate_shadow(footprint_wkt, building_height, azimuth, elevation):
    if elevation <= 0:
        return None
    building = loads(footprint_wkt)
    coords = list(building.exterior.coords)
    coords_2d = [(x, y) for x, y, *_ in coords]
    shadow_length = building_height / np.tan(np.radians(elevation))
    shadow_direction = np.radians(azimuth + 180)
    dx = shadow_length * np.sin(shadow_direction)
    dy = shadow_length * np.cos(shadow_direction)
    shadow = Polygon([(x + dx, y + dy) for x, y in coords_2d])
    building_2d = Polygon(coords_2d)
    return unary_union([building_2d, shadow])


def check_sun_for_restaurants(dt):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    azimuth, elevation = get_sun_position(dt)
    print(f"\nTidspunkt: {dt.strftime('%H:%M')}")
    print(f"Sol azimuth: {azimuth:.1f}° | elevation: {elevation:.1f}°")

    if elevation <= 0:
        print("Solen er nede — alle steder er i skygge.")
        return

    cur.execute("""
        SELECT id, name,
            ST_X(ST_Transform(location::geometry, 25832)),
            ST_Y(ST_Transform(location::geometry, 25832))
        FROM restaurants
    """)
    restaurants = cur.fetchall()

    for rest_id, name, x, y in restaurants:
        # DEBUG: tjek antal bygninger i nærheden
        cur.execute("""
            SELECT COUNT(*)
            FROM buildings
            WHERE ST_DWithin(
                footprint,
                ST_SetSRID(ST_MakePoint(%s, %s), 25832),
                100
            )
        """, (x, y))
        building_count = cur.fetchone()[0]

        # Hent bygninger
        cur.execute("""
            SELECT ST_AsText(footprint), height
            FROM buildings
            WHERE ST_DWithin(
                footprint,
                ST_SetSRID(ST_MakePoint(%s, %s), 25832),
                100
            )
        """, (x, y))
        nearby_buildings = cur.fetchall()

        shadows = []
        for footprint_wkt, height in nearby_buildings:
            if height is None:
                height = 6.0
            shadow = calculate_shadow(footprint_wkt, height, azimuth, elevation)
            if shadow:
                shadows.append(shadow)

        if shadows:
            combined_shadow = unary_union(shadows)
            rest_point = Point(x, y)
            in_shadow = combined_shadow.contains(rest_point)
            status = "🌑 Skygge" if in_shadow else "☀️  Sol"
        else:
            status = "☀️  Sol"

        print(f"{status} | {name} ({building_count} bygninger i nærheden)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    cph_tz = pytz.timezone('Europe/Copenhagen')
    # datetime(year, month, day, hour, minute)
    test_time = cph_tz.localize(datetime(2026, 5, 7, 14, 15))
    check_sun_for_restaurants(test_time)