from datetime import datetime, timezone
import math

import ephem
from geopy.geocoders import Nominatim
import httpx
import llm
from pydantic import BaseModel
from pydantic_extra_types.coordinate import Coordinate, Latitude, Longitude

from llm_sky.metar_data import STATIONS


def owm_key():
    return llm.get_key(None, 'openweathermap', 'OPENWEATHERMAP_API_KEY')


def weather(latitude: float, longitude: float, units: str = 'metric'):
    """Current weather information from OpenWeatherMap."""
    with httpx.Client(params=dict(appid=owm_key())) as http:
        return http.get(
            'https://api.openweathermap.org/data/2.5/weather',
            params=dict(lat=latitude, lon=longitude, units=units)
        ).json()


def sun(latitude: Latitude, longitude: Longitude) -> dict:
    """
    Return today's sunrise and sunset times in ISO-8601 (UTC).
    """
    observer = ephem.Observer()
    # ephem expects strings or Angle objects, so cast to str
    observer.lat = str(latitude)
    observer.lon = str(longitude)

    sun = ephem.Sun()
    sunrise = observer.next_rising(sun).datetime()
    sunset = observer.next_setting(sun).datetime()

    # If sunrise is later than sunset, we crossed into the next day:
    if sunrise > sunset:
        sunrise = observer.previous_rising(sun).datetime()

    offset = datetime.now().astimezone().utcoffset()
    sunrise += offset
    sunset += offset

    hours, remainder = divmod(int((sunset - sunrise).total_seconds()), 3600)
    minutes = remainder // 60
    return dict(
        sunrise=sunrise.time().isoformat(timespec="minutes"),
        sunset=sunset.time().isoformat(timespec='minutes'),
        day_length=f"{hours}:{minutes}"
    )


def uv_index(latitude: Latitude, longitude: Longitude) -> float:
    """Current UV index"""
    with httpx.Client(params=dict(appid=owm_key())) as http:
        return http.get("https://api.openweathermap.org/data/2.5/uvi",
            params=dict(lat=latitude, lon=longitude)
        ).json()['value']

    
@llm.hookimpl
def register_fragment_loaders(register):
    register('metar', metar_fragment)


@llm.hookimpl
def register_tools(register):
    register(geocode)
    register(metar)
    register(metar_nearby)
    register(moon)
    register(sun)
    register(Local)
    if owm_key():
        register(weather)
        register(uv_index)
        def w(self, units: str = 'metric'):
            """Query OpenWeatherMap."""
            return weather(self.latitude, self.longitude, units)

        def u(self):
            """Current UV index (requires OpenWeatherMap key)."""
            return uv_index(self.latitude, self.longitude)
        Local.weather = w
        Local.uv_index = u
        Local.weather.__name__ = 'weather'
        Local.uv_index.__name__ = 'uv_index'


def geocode(query: str):
    return Nominatim(user_agent=__package__).geocode(query).raw


class Local(llm.Toolbox):
    latitude: Latitude
    longitude: Longitude

    def __init__(self, query = None, latitude = None, longitude = None):
        if query:
            result = Nominatim(user_agent="llm-sky").geocode(query)
            if result is None:
                raise RuntimeError(f"'{query}' not found")
            self.latitude, self.longitude = result.latitude, result.longitude
        elif latitude is not None and longitude is not None:
            self.latitude, self.longitude = latitude, longitude
        else:
            self.latitude = float(input("Latitude: "))
            self.longitude = float(input("Longitude: "))


    def coordinates(self) -> Coordinate:
        """The coordinates the user is located at."""

        return Coordinate(self.latitude, self.longitude)


    def moon(self):
        """Moon status."""

        return moon(self.latitude, self.longitude)


    def metar(self, radius_km: int = 100):
        """METAR weather reports around the current location."""

        return metar_nearby(self.latitude, self.longitude, radius_km)

    def sun(self):
        """Today's sunrise and sunset times in local time."""
        return sunrise_sunset(self.latitude, self.longitude)


URL = 'https://tgftp.nws.noaa.gov/data/observations/metar/stations/{code}.TXT'


SYNODIC_MONTH = 29.53058867

def moon(latitude: Latitude, longitude: Longitude) -> str:
    """A textual description of the current status of the moon."""

    observer = ephem.Observer()
    observer.lat = latitude
    observer.lon = longitude
    moon = ephem.Moon(observer)
    illumination = moon.moon_phase * 100
    age = observer.date - ephem.previous_new_moon(observer.date)
    status = "waxing" if age < SYNODIC_MONTH / 2 else "waning"
    days_to_full = ephem.next_full_moon(observer.date) - observer.date

    return f"{status} moon, illumination {round(illumination)}%, {round(days_to_full)} days to next full moon"


def metar_fragment(code: str) -> llm.Fragment:
    """Fetch a METAR weather report."""
    time, report = metar(code)
    delta = time - datetime.now(timezone.utc)
    return llm.Fragment(f'{code.upper()} {round(-delta.total_seconds() / 60)}m ago: {report}', source=URL.format(code=code.upper()))


def metar(code: str) -> tuple[datetime, str]:
    """Fetch a METAR weather report."""

    with httpx.Client() as client:
        response = client.get(URL.format(code=code.upper()))
        response.raise_for_status()

        time_str, report = response.text.split("\n")[0:2]
        time = datetime.strptime(time_str, "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
        return time, " ".join(report.split(" ")[2:])


def haversine(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points on the Earth."""

    R = 6371  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def metar_nearby(latitude: Latitude, longitude: Longitude, max_distance: float = 100.0, max_seconds_ago: int = 7200):
    """Return a list of stations nearby ordered by distance."""

    distances = []

    for code, station in STATIONS.items():
        distance = haversine(latitude, longitude, station.coordinate.latitude, station.coordinate.longitude)
        if distance <= max_distance:
            distances.append((code, station, distance))

    # Sort by distance
    distances.sort(key=lambda x: x[2])

    result = {}
    for code, station, distance in distances:
        try:
            compass = bearing_to_compass(bearing(latitude, longitude, station.coordinate.latitude, station.coordinate.longitude))
            time, report = metar(code)
            delta = time - datetime.now(timezone.utc)
            if delta.total_seconds() >= -max_seconds_ago:
                result[f'{station.name}: {round(distance)}km {compass} {round(abs(delta).total_seconds() / 60)}m ago'] = report
        except:
            pass

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
