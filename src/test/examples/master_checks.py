"""Read-only client checks; callers retain raw API and independently read MAST evidence.

No sockets, file parsing, VFS resolution or game-compatibility claims live here.
Compare master membership, active dependency edges and full priority separately.
"""
from collections import Counter


def _keys(names):
    if not isinstance(names, (list, tuple)) or any(
            not isinstance(name, str) or not name or '\x00' in name for name in names):
        raise ValueError('Expected a sequence of nonempty plugin filenames')
    return [name.casefold() for name in names]


def _unique(names):
    keys = _keys(names)
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate case-insensitive plugin identity')
    return keys


def compare_master_membership(api_masters, file_mast):
    """Order differences are evidence, not a membership failure; duplicates still fail."""
    api, file = Counter(_keys(api_masters)), Counter(_keys(file_mast))
    duplicates_api = sorted(name for name, count in api.items() if count > 1)
    duplicates_file = sorted(name for name, count in file.items() if count > 1)
    return {'membersMatch': not duplicates_api and not duplicates_file and api == file,
            'sameSequence': _keys(api_masters) == _keys(file_mast),
            'duplicateApiNames': duplicates_api, 'duplicateFileNames': duplicates_file,
            'missingFromApi': sorted(file.keys() - api.keys()),
            'unexpectedInApi': sorted(api.keys() - file.keys())}


def _rows_by_name(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Expected the complete native plugin row list')
    keys = _unique([row.get('plugin') for row in rows])
    return dict(zip(keys, rows))


def active_master_issues(rows, file_mast_by_name):
    """Use actual file masters and native active loadOrder, never masters-array order."""
    native = _rows_by_name(rows)
    if not isinstance(file_mast_by_name, dict):
        raise ValueError('Expected independently read file master sequences')
    header_keys = _unique(list(file_mast_by_name))
    headers = dict(zip(header_keys, file_mast_by_name.values()))
    active_orders = []
    for row in rows:
        if type(row.get('active')) is not bool:
            raise ValueError('Invalid native activation state')
        if row['active']:
            order = row.get('loadOrder')
            if type(order) is not int or order < 0:
                raise ValueError('Invalid active native loadOrder')
            active_orders.append(order)
    if len(active_orders) != len(set(active_orders)):
        raise ValueError('Duplicate active native loadOrder')
    issues = []
    for key, row in native.items():
        if not row['active']:
            continue
        if key not in headers:
            issues.append({'plugin': row['plugin'], 'reason': 'missing-file-header'})
            continue
        masters = _unique(headers[key])
        for master in masters:
            prior = native.get(master)
            reason = ('missing-master' if prior is None else
                      'inactive-master' if not prior['active'] else
                      'late-master' if prior['loadOrder'] >= row['loadOrder'] else None)
            if reason:
                issues.append({'plugin': row['plugin'], 'master': master, 'reason': reason})
    return issues


def compare_priority_order(rows, expected_full_order):
    """Check every known plugin, including disabled rows whose loadOrder is -1."""
    native = _rows_by_name(rows)
    expected = _unique(expected_full_order)
    priorities = [row.get('priority') for row in rows]
    if (any(type(value) is not int or value < 0 for value in priorities)
            or len(priorities) != len(set(priorities))):
        raise ValueError('Invalid native full priority snapshot')
    actual_rows = sorted(rows, key=lambda row: row['priority'])
    actual = [row['plugin'].casefold() for row in actual_rows]
    members_match = set(expected) == set(native)
    return {'membersMatch': members_match, 'orderMatch': members_match and actual == expected,
            'actualOrder': [row['plugin'] for row in actual_rows]}
