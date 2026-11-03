"""Complete H3 grids, with stable identities and explicit coverage provenance."""
import argparse
import hashlib
import json
from pathlib import Path

import h3
from h3.api import basic_int as hi
import numpy as np

PRODUCTS = {'land': 1, 'maritime': 2, 'aviation': 4}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def complete_indexes(resolution):
    if not 0 <= resolution <= 6:
        raise ValueError('Census builder supports resolutions 0–6; higher resolutions need a storage plan')
    return np.sort(np.fromiter((cell for root in hi.get_res0_cells()
                                for cell in hi.cell_to_children(root, resolution)), dtype=np.uint64))


def build(path, resolution=5, mask=None, global_study=False):
    if mask and global_study:
        raise ValueError('Choose either a global census or a commercial mask')
    indexes = complete_indexes(resolution)
    if len(indexes) != h3.get_num_cells(resolution):
        raise ValueError('Master grid enumeration is incomplete')
    ids = np.arange(len(indexes), dtype=np.uint32)
    products = np.zeros(len(indexes), dtype=np.uint8)
    metadata = {'schema': 1, 'resolution': resolution, 'global_cells': len(indexes),
                'identity': 'ordinal in sorted complete H3 grid; unchanged by mask or worker count',
                'inclusion': 'H3 polygon centroid', 'domain': 'global-calibration',
                'production_ready': False, 'products': PRODUCTS,
                'blockers': ['Complete verified all-commercial-product coverage mask missing']}
    if global_study:
        metadata.update(domain='global-study', production_ready=True, blockers=[],
                        scope='Complete Earth census; does not imply commercial service in every cell')
    if mask:
        document = json.loads(Path(mask).read_text())
        provenance = document.get('coverage_provenance', {})
        if document.get('type') != 'FeatureCollection':
            raise ValueError('Coverage mask must be a product-tagged GeoJSON FeatureCollection')
        if set(provenance.get('complete_products', [])) != set(PRODUCTS):
            raise ValueError('Mask must declare complete land, maritime, and aviation layers')
        if not all(provenance.get(key) for key in ('observed_at', 'sources', 'review_notes')):
            raise ValueError('Mask needs observation time, source URLs, and geographic review notes')
        for feature in document['features']:
            props = feature['properties']
            names = props.get('products', [])
            if not names or any(name not in PRODUCTS for name in names):
                raise ValueError('Every mask feature must have valid products')
            if props.get('status') != 'available':
                raise ValueError('Only verified current available polygons belong in this union')
            selected = np.array(sorted(hi.geo_to_cells(feature['geometry'], resolution)), dtype=np.uint64)
            positions = np.searchsorted(indexes, selected)
            if np.any(positions >= len(indexes)) or not np.array_equal(indexes[positions], selected):
                raise ValueError('Mask returned indexes outside master grid')
            products[positions] |= sum(PRODUCTS[name] for name in set(names))
        keep = products != 0
        indexes, ids, products = indexes[keep], ids[keep], products[keep]
        if not len(indexes):
            raise ValueError('Commercial mask selected no cells')
        metadata.update(domain='commercial-union', production_ready=provenance.get('verified') is True,
                        mask_sha256=digest(mask), coverage_provenance=provenance,
                        blockers=[] if provenance.get('verified') is True else ['Coverage review incomplete'])
    coordinates = np.array([hi.cell_to_latlng(int(cell)) for cell in indexes])
    parents = np.array([hi.cell_to_parent(int(cell), 0) for cell in indexes], dtype=np.uint64)
    area = np.fromiter((hi.cell_area(int(cell), unit='km^2') for cell in indexes), dtype=np.float64)
    metadata.update(cells=len(indexes), area_km2=float(area.sum()),
                    product_cells={name: int(np.count_nonzero(products & bit)) for name, bit in PRODUCTS.items()},
                    geographic_tiles=len(np.unique(parents)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.tmp').open('wb') as stream:
        np.savez(stream, ids=ids, indexes=indexes, latlon=coordinates, parents=parents,
                 area_km2=area, products=products, metadata=json.dumps(metadata, sort_keys=True))
    path.with_suffix('.tmp').replace(path)
    metadata['grid_sha256'] = digest(path)
    path.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def load(path):
    with np.load(path, allow_pickle=False) as data:
        grid = {key: data[key] for key in data.files if key != 'metadata'}
        grid['metadata'] = json.loads(str(data['metadata']))
    n = len(grid['ids'])
    if not n or any(len(grid[key]) != n for key in ('indexes', 'latlon', 'parents', 'products', 'area_km2')):
        raise ValueError('Invalid grid array lengths')
    if len(np.unique(grid['ids'])) != n or len(np.unique(grid['indexes'])) != n:
        raise ValueError('Grid contains duplicate identities')
    if not np.all(np.isfinite(grid['latlon'])) or np.any(np.abs(grid['latlon']) > [90, 180]):
        raise ValueError('Invalid grid coordinates')
    return grid


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--resolution', type=int, default=5)
    parser.add_argument('--mask', type=Path)
    parser.add_argument('--global-study', action='store_true', help='Explicit complete-Earth study, with no commercial-availability claim')
    args = parser.parse_args()
    print(json.dumps(build(args.out, args.resolution, args.mask, args.global_study), indent=2))
