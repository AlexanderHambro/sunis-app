import requests
import psycopg2
from config import DB_CONFIG, GOOGLE_API_KEY

def fetch_places(query):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": GOOGLE_API_KEY,
        "language": "da",
        "location": "55.6761,12.5683",  # København centrum
        "radius": "7500"                 # 5 km radius
    }
    return requests.get(url, params=params).json().get("results", [])

def fetch_and_save():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Hent både restauranter og barer
    queries = [
        "restauranter med udendørs servering København",
        "barer med udendørs servering København"
    ]

    count = 0
    for query in queries:
        places = fetch_places(query)
        for place in places:
            name = place.get("name")
            address = place.get("formatted_address")
            lat = place["geometry"]["location"]["lat"]
            lng = place["geometry"]["location"]["lng"]
            place_id = place.get("place_id")

            cur.execute("""
                INSERT INTO restaurants (name, address, location, has_outdoor_seating, google_place_id)
                VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
                ON CONFLICT (google_place_id) DO NOTHING
            """, (name, address, lng, lat, True, place_id))
            count += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"{count} steder hentet og gemt!")

if __name__ == "__main__":
    fetch_and_save()