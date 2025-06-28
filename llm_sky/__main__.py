import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import math

import httpx
import llm
from pydantic_extra_types.coordinate import Coordinate


@llm.hookimpl
def register_fragment_loaders(register):
    register('metar', metar_fragment)


@llm.hookimpl
def register_tools(register):
    register(metar)


URL = 'https://tgftp.nws.noaa.gov/data/observations/metar/stations/{code}.TXT'


def metar_fragment(code: str) -> llm.Fragment:
    """Fetch a METAR weather report."""

    return llm.Fragment(metar(code), source=URL.format(code=code.upper()))


def metar(code: str) -> str:
    """Fetch a METAR weather report."""

    with httpx.Client() as client:
        response = client.get(URL.format(code=code.upper()))
        response.raise_for_status()

        time, report = response.text.split("\n")[0:2]
        time = datetime.strptime(time, "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
        delta = time - datetime.now(timezone.utc)
        return delta, " ".join(report.split(" ")[2:])


def dms_to_decimal(dms: str) -> float:
    """Convert degree-minutes-seconds format to decimal degrees."""
    direction = dms[-1]
    numbers = list(map(int, dms[:-1].split('-')))
    while len(numbers) < 3: numbers.append(0)
    degrees, minutes, seconds = numbers
    decimal = degrees + minutes / 60 + seconds / 3600
    if direction in 'SW': decimal = -decimal

    return decimal


def haversine(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points on the Earth."""

    R = 6371  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def metar_nearby_station(latitude_dms: str | float, longitude_dms: str | float, max_distance: float = 100.0, max_seconds_ago: int = 7200):
    """Return a list of stations nearby ordered by distance."""

    latitude = dms_to_decimal(latitude_dms) if isinstance(latitude_dms, str) else latitude_dms
    longitude = dms_to_decimal(longitude_dms) if isinstance(longitude_dms, str) else longitude_dms
    
    distances = []

    for code, station in stations().items():
        try:
            if station['latitude'] and station['longitude']:
                station_lat = dms_to_decimal(station['latitude'])
                station_lon = dms_to_decimal(station['longitude'])
                distance = haversine(latitude, longitude, station_lat, station_lon)
                if distance <= max_distance:
                    distances.append((station, distance))
        except ValueError:
            # Skip stations with invalid lat/lon
            continue
    
    # Sort by distance
    distances.sort(key=lambda x: x[1])

    result = {}
    for station, distance in distances:
        try:
            compass = bearing_to_compass(bearing(latitude, longitude, dms_to_decimal(station['latitude']), dms_to_decimal(station['longitude'])))
            delta, report = metar(station['code'])
            if delta.total_seconds() >= -max_seconds_ago:
                result[f'{station["name"]}: {round(distance)}km {compass} {round(abs(delta).total_seconds() / 60)}m ago'] = report
        except:
            pass

    return result


@dataclass
class station:
    name: str
    coordinate: Coordinate
    altitude: int | None = None
    country: str | None = None

def stations():
    with httpx.Client() as client:
        response = client.get('https://tgftp.nws.noaa.gov/data/nsd_cccc.txt')
        response.raise_for_status()

        lines = response.text.split('\n')
        FIELDNAMES = ('code', None, None, 'name', None, 'country', None, 'latitude', 'longitude', None, None, 'altitude')
        result = {}
        def proc(k, v):
            if isinstance(v, str): v = v.strip()
            if k == 'altitude':
                return int(v)
            return v

        for d in csv.DictReader(lines, FIELDNAMES, delimiter=';'):
            if 'latitude' in d and 'longitude' in d and d['latitude'] and d['longitude']:
                d['coordinate'] = (round(dms_to_decimal(d['latitude']), 5), round(dms_to_decimal(d['longitude']), 5))
                del d['latitude']
                del d['longitude']
                result[d['code']] = station(**{
                    k: proc(k, v) for k, v in d.items() if k and k != 'code' and v
                })

        return result


def bearing(lat1, lon1, lat2, lon2):
    """Calculate the bearing between two points."""

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(d_lon))

    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360

    return compass_bearing


def bearing_to_compass(bearing):
    """Convert bearing to compass direction."""

    compass_sectors = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]

    sector_size = 360 / len(compass_sectors)
    sector_index = int((bearing + sector_size / 2) / sector_size) % len(compass_sectors)

    return compass_sectors[sector_index]


if __name__ == '__main__':
    from rich import console
    print('from dataclasses import dataclass')
    print('from pydantic_extra_types.coordinate import Coordinate')
    print()
    print("""
@dataclass
class station:
    name: str
    coordinate: Coordinate
    altitude: int | None = None
    country: str | None = None
""") 
    console.Console(width=200).print("STATIONS", "=", stations())
