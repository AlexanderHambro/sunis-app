import pvlib
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
from datetime import datetime
import pytz

# lat=55.6761, lon=12.5683
def get_sun_position(dt, lat=100, lon=12.5683):
    """Beregn solens position over København"""
    location = pvlib.location.Location(lat, lon, tz='Europe/Copenhagen')
    solpos = location.get_solarposition(dt)
    
    azimuth = solpos['azimuth'].values[0]
    elevation = solpos['apparent_elevation'].values[0]
    
    return azimuth, elevation

def calculate_shadow(building_footprint_coords, building_height, azimuth, elevation):
    """Beregn skyggepolygon fra en bygning"""
    
    # Hvis solen er under horisonten, ingen skygge
    if elevation <= 0:
        return None
    
    # Beregn skyggelængde
    shadow_length = building_height / np.tan(np.radians(elevation))
    
    # Beregn retning skyggen falder (modsat solens azimuth)
    shadow_direction = np.radians(azimuth + 180)
    
    # Forskyd bygningens hjørner i skyggretningen
    dx = shadow_length * np.sin(shadow_direction)
    dy = shadow_length * np.cos(shadow_direction)
    
    # Opret skyggepolygon
    building = Polygon(building_footprint_coords)
    shadow = Polygon([(x + dx, y + dy) for x, y in building_footprint_coords])
    
    # Kombiner bygning og skygge
    full_shadow = unary_union([building, shadow])
    
    return full_shadow

if __name__ == "__main__":
    cph_tz = pytz.timezone('Europe/Copenhagen')
    now = datetime.now(cph_tz)
    
    azimuth, elevation = get_sun_position(now)
    print(f"Tidspunkt: {now.strftime('%H:%M')}")
    print(f"Sol azimuth: {azimuth:.1f}°")
    print(f"Sol elevation: {elevation:.1f}°")
    
    if elevation > 0:
        print("Solen er oppe!")
    else:
        print("Solen er nede.")

    # Test skyggeberegning med en simpel bygning
    test_building = [
        (725000, 6175000),
        (725010, 6175000),
        (725010, 6175010),
        (725000, 6175010)
    ]
    shadow = calculate_shadow(test_building, 15.0, azimuth, elevation)
    if shadow:
        print(f"Skyggepolygon areal: {shadow.area:.1f} m²")
        print("Skyggeberegning virker!")