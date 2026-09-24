"""Assign persistent numeric IDs without renumbering existing variants."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def validate_library_ids(variants):
    used = {}
    for variant in variants:
        lib_id = variant.get('libID')
        if type(lib_id) is not int or lib_id < 1:
            raise ValueError(f"{variant['id']}: libID must be a positive integer")
        if lib_id in used:
            raise ValueError(f"Duplicate libID {lib_id}: {used[lib_id]} / {variant['id']}")
        used[lib_id] = variant['id']
    return used


def assign_library_ids(root=ROOT):
    catalog_path = root / 'library/obstacle_catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    families = [(root / 'library' / group['path']) for group in catalog['groups']]
    documents = [(path, json.loads(path.read_text(encoding='utf-8'))) for path in families]
    variants = [variant for _, family in documents for variant in family['variants']]
    used = validate_library_ids(v for v in variants if 'libID' in v)
    next_id = catalog.get('nextLibID', max(used, default=0) + 1)
    if type(next_id) is not int or next_id <= max(used, default=0):
        raise ValueError('nextLibID must be greater than every assigned libID')
    assigned = 0
    changed = []
    for path, family in documents:
        dirty = False
        for variant in family['variants']:
            if 'libID' in variant:
                continue
            fields = dict(variant)
            variant.clear()
            variant.update(id=fields.pop('id'), libID=next_id, **fields)
            next_id += 1
            assigned += 1
            dirty = True
        if dirty:
            changed.append((path, family))
    validate_library_ids(variants)
    if catalog.get('nextLibID') != next_id:
        catalog['nextLibID'] = next_id
        changed.append((catalog_path, catalog))
    for path, document in changed:
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return assigned


if __name__ == '__main__':
    print(f'Assigned {assign_library_ids()} new library IDs.')
