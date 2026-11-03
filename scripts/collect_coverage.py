#!/usr/bin/env python3
"""Freeze official public map inputs; never infer all-product eligibility from them."""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.request

import geobuf
from geobuf import geobuf_pb2

FILES = {'availability.json': 'https://api.starlink.com/public-files/availability.json',
         'availability-cells.pb': 'https://api.starlink.com/public-files/availability-cells.pb'}


def collect(directory):
    directory.mkdir(parents=True, exist_ok=True)
    sources = []
    for name, url in FILES.items():
        request = urllib.request.Request(url, headers={'User-Agent': 'Bratos research coverage snapshot'})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            headers = dict(response.headers)
        (directory / name).write_bytes(raw)
        sources.append({'url': url, 'file': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'bytes': len(raw), 'headers': headers,
                        'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    statuses = json.loads((directory / 'availability.json').read_text())
    binary = geobuf_pb2.Data()
    binary.ParseFromString((directory / 'availability-cells.pb').read_bytes())
    # Geobuf's schema defaults are 2 dimensions and precision 6. The Python
    # decoder uses proto3, which gives omitted numeric fields a zero value.
    binary.dimensions = binary.dimensions or 2
    binary.precision = binary.precision or 6
    polygons = geobuf.decode(binary.SerializeToString())
    (directory / 'availability-cells.geojson').write_text(json.dumps(polygons) + '\n')
    result = {'sources': sources, 'decoded_sha256': hashlib.sha256(
              (directory / 'availability-cells.geojson').read_bytes()).hexdigest(),
              'geobuf_defaults': {'dimensions': 2, 'precision': 6},
              'admin_status_counts': {key: dict(Counter(value['status'] for value in values.values()))
                                      for key, values in statuses.items()},
              'polygon_features': [feature['properties'] for feature in polygons['features']],
              'production_ready': False,
              'blockers': ['Country status tables have no boundary geometry',
                           'Map overlays are not a complete all-product eligibility layer',
                           'Complete aviation exceptions and territorial-water boundaries not established']}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('data/coverage/raw'))
    print(json.dumps(collect(parser.parse_args().out), indent=2))
