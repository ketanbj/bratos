# Commercial coverage inputs

`raw/manifest.json` freezes the public Starlink data retrieved October 6, 2026
UTC (October 5 EDT), including URLs, HTTP metadata, dates, checksums, and decoded
feature properties. Refresh with `scripts/collect_coverage.py` in the Option 2
environment. These files have **not** been promoted to a verified commercial mask.

- [Official availability status table](https://api.starlink.com/public-files/availability.json):
  255 admin0 and 46 admin1 entries. Last-Modified September 22, 2026. The admin1
  keys refer to Mapbox boundary IDs and need an authoritative geometry mapping.
- [Official polygon overlay](https://api.starlink.com/public-files/availability-cells.pb):
  seven Geobuf features. Last-Modified February 20, 2026. Statuses include
  launched, test, blacklisted, and coming-soon variants, including Sold Out.
  Decoded to `availability-cells.geojson`; this is not a cell-by-cell census.
- [Official aviation authorization guidance](https://starlink.com/fr-ch/support/article/5e433e52-5583-6c37-81a4-426a6478d49c):
  international-water service and a country/territory authorization list.
- [Official aviation availability guidance](https://starlink.com/au/support/article/bc134ccc-57ee-c44d-8b90-e8cbc9a44865):
  describes additional in-flight service over most countries, with exceptions
  that the article does not enumerate as machine-readable boundaries.
- [Official in-motion guidance](https://starlink.com/lk/support/article/4497c64e-d214-803a-bd8d-1f0c957b894d):
  distinguishes international waters, approved territories, and product restrictions.

Residential Sold Out cannot be a blanket exclusion from an all-product union.
Likewise, country launch status is not an exact territorial-water or aviation
coverage polygon. No exact commercial-union cell count is asserted from these
inputs. Needed next: complete product eligibility/exception boundaries, mapped
subnational IDs, dated coastal/territorial-water treatment, and geographic review.
Natural Earth boundaries alone would introduce a disclosed approximation rather
than solve all of these gaps.

Geobuf JS omits schema defaults for two dimensions and six decimal digits. The
Python decoder's proto3 object exposes omitted numeric fields as zero; the
collector restores those documented defaults before decoding. The unmodified
binary remains preserved, and both its hash and the decoded output hash are recorded.

The selected Option 2 domain is now the complete Earth census in
`data/generated/global-option2-res5.npz`, explicitly labeled **global-study**.
These coverage inputs are optional reference data; their missing product
boundaries do not block the global simulation. It makes no claim that every
cell has commercial service. The older `global-res5.npz` retains its original
calibration-only metadata for checkpoint reproducibility.
