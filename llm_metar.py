import csv

import httpx
import llm


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

        return response.text.split("\n")[1]


def stations():
    with httpx.Client() as client:
        response = client.get('https://tgftp.nws.noaa.gov/data/nsd_cccc.txt')
        response.raise_for_status()

        lines = response.text.split('\n')
        FIELDNAMES = ('code', None, None, 'name', None, 'country', None, 'latitude', 'longitude')
        return map(lambda d: {k: v for k, v in d.items() if k},
            csv.DictReader(lines, FIELDNAMES, delimiter=';')
        )
