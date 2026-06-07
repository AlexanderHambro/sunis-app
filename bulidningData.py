import os
import requests
import psycopg2
from lxml import etree
from config import DB_CONFIG, DATAFORDELER_USERNAME, DATAFORDELER_PASSWORD


# Tunables

STOREY_HEIGHT = 3.0
DEFAULT_HEIGHT = 6.0
WFS_COUNT = 10000        # Datafordeler hard cap per request

CSV_OUT = "data/buildings.csv"

GEODANMARK_URL = "https://services.datafordeler.dk/GeoDanmarkVektor/GeoDanmark60_NOHIST_GML3/1.0.0/WFS"
BBR_URL = "https://services.datafordeler.dk/BBR/BBRPublic/1/rest/bygning"

# Whole area of interest in EPSG:25832, tiled into TILE-metre squares so each
# request stays under the 10 km^2 / 10000-feature limits (no STARTINDEX needed).
AREA = (720000, 6172000, 730000, 6180000)   # minX, minY, maxX, maxY
TILE = 1000                                  # metres

ns = {
    "gdk60": "http://data.gov.dk/schemas/geodanmark60/2/gml3",
    "gml": "http://www.opengis.net/gml/3.2",
    "wfs": "http://www.opengis.net/wfs/2.0",
}
GML_POLYGON = "{http://www.opengis.net/gml/3.2}Polygon"


def tiles(area, step):
    minx, miny, maxx, maxy = area
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            yield (x, y, x + step, y + step)
            y += step
        x += step


def fetch_bbr_points(vest, syd, oest, nord):
    """(floors, wkt_point) for current buildings that have a coordinate."""
    out, page = [], 1
    while True:
        rows = requests.get(BBR_URL, params={
            "username": DATAFORDELER_USERNAME, "password": DATAFORDELER_PASSWORD,
            "Format": "JSON", "status": "6",
            "nord": nord, "syd": syd, "oest": oest, "vest": vest,
            "pagesize": 1000, "page": page,
        }).json()
        if not rows:
            break
        for b in rows:
            wkt = b.get("byg404Koordinat")
            etager = b.get("byg054AntalEtager")
            if not wkt:
                continue
            try:
                floors = int(etager) if etager not in (None, "") else None
            except (ValueError, TypeError):
                floors = None
            out.append((floors, wkt))
        if len(rows) < 1000:
            break
        page += 1
    return out


def fetch_geodanmark_polygons(bbox):
    """All <Polygon> fragments in bbox (single request; bbox is small enough)."""
    root = etree.fromstring(requests.get(GEODANMARK_URL, params={
        "username": DATAFORDELER_USERNAME, "password": DATAFORDELER_PASSWORD,
        "SERVICE": "WFS", "REQUEST": "GetFeature", "VERSION": "2.0.0",
        "TYPENAMES": "gdk60:Bygning",
        "NAMESPACES": "xmlns(gdk60,http://data.gov.dk/schemas/geodanmark60/2/gml3)",
        "COUNT": WFS_COUNT, "SRSNAME": "EPSG:25832", "BBOX": bbox,
    }).content)
    members = root.findall(".//wfs:member", ns)
    polys = []
    for member in members:
        building = member.find("gdk60:Bygning", ns)
        if building is None:
            continue
        for poly in building.findall(".//" + GML_POLYGON):
            polys.append(etree.tostring(poly, encoding="unicode"))
    return polys, len(members)


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("TRUNCATE buildings RESTART IDENTITY")
    cur.execute("DROP TABLE IF EXISTS bbr_points")
    cur.execute("CREATE TABLE bbr_points (floors INTEGER, geom geometry(Point, 25832))")
    conn.commit()

    total = insert_errors = 0
    capped_tiles = 0
    tile_list = list(tiles(AREA, TILE))
    print(f"{len(tile_list)} tiles a {TILE} m...")

    for i, (minx, miny, maxx, maxy) in enumerate(tile_list, 1):
        bbox = f"{minx},{miny},{maxx},{maxy},EPSG:25832"

        # BBR points
        try:
            for floors, wkt in fetch_bbr_points(minx, miny, maxx, maxy):
                cur.execute(
                    "INSERT INTO bbr_points (floors, geom) VALUES (%s, ST_GeomFromText(%s, 25832))",
                    (floors, wkt))
        except Exception as e:
            print("BBR fejl i tile", bbox, "->", e)

        # GeoDanmark footprints
        try:
            polys, n_members = fetch_geodanmark_polygons(bbox)
        except Exception as e:
            print("GeoDanmark fejl i tile", bbox, "->", e)
            polys, n_members = [], 0

        if n_members >= WFS_COUNT:
            capped_tiles += 1          # tile hit the cap -> shrink TILE and rerun

        for geom_gml in polys:
            cur.execute("SAVEPOINT sp")
            try:
                cur.execute("""
                    INSERT INTO buildings (footprint, height, source)
                    VALUES (ST_GeomFromGML(%s), %s, 'geodanmark')
                """, (geom_gml, DEFAULT_HEIGHT))
                cur.execute("RELEASE SAVEPOINT sp")
                total += 1
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT sp")
                insert_errors += 1
                if insert_errors <= 10:
                    print("Insert fejlede:", e)

        conn.commit()
        if i % 10 == 0:
            print(f"  {i}/{len(tile_list)} tiles, {total} polygoner indtil nu")

    # spatial join: footprint takes floors from the BBR point inside it
    cur.execute("CREATE INDEX IF NOT EXISTS buildings_footprint_gix ON buildings USING GIST (footprint)")
    cur.execute("CREATE INDEX bbr_points_gix ON bbr_points USING GIST (geom)")
    conn.commit()

    cur.execute("""
        UPDATE buildings b
        SET height = sub.floors * %s, source = 'geodanmark+bbr'
        FROM (
            SELECT b2.id AS bid, MAX(p.floors) AS floors
            FROM buildings b2
            JOIN bbr_points p ON ST_Contains(ST_Force2D(b2.footprint), p.geom)
            WHERE p.floors IS NOT NULL
            GROUP BY b2.id
        ) sub
        WHERE b.id = sub.bid
    """, (STOREY_HEIGHT,))
    matched = cur.rowcount
    conn.commit()

    os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
    with open(CSV_OUT, "w", encoding="utf-8") as f:
        cur.copy_expert("COPY buildings (id, footprint, height, source) TO STDOUT WITH CSV HEADER", f)

    cur.close()
    conn.close()

    print("\nFaerdig!")
    print(f"  {total} polygoner gemt ({insert_errors} insert-fejl)")
    print(f"  {matched} bygninger fik hoejde fra BBR (resten = {DEFAULT_HEIGHT} m)")
    if capped_tiles:
        print(f"  ADVARSEL: {capped_tiles} tiles ramte {WFS_COUNT}-graensen -> saet TILE mindre og koer igen")
    print(f"  Eksporteret til {CSV_OUT} -> koer nu: python init_db.py")


if __name__ == "__main__":
    main()