"""Pure acceptance checks over caller-captured profile bytes; no IO or repair.

Capture every fixture profile before and after selection/normal close/restart.
Only explicitly listed NEW disabled catalogue rows may be projected out. Original
names, flags, order, comments, encoding, BOM and line endings remain byte-exact.
No result from this helper establishes native writer identity or crash causation.
"""
import codecs


def _modlist(data):
    if not isinstance(data, bytes):
        raise ValueError('modlist must be bytes')
    bom = codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b''
    payload = data[len(bom):]
    payload.decode('utf-8', errors='strict')
    lines, names = [], {}
    for raw in payload.splitlines(keepends=True):
        text = raw.rstrip(b'\r\n').decode('utf-8')
        key = None
        if text and not text.startswith('#'):
            if text[0] not in '+-*' or len(text) == 1:
                raise ValueError('invalid modlist row')
            name = text[1:]
            if name != name.strip() or any(c in name for c in '\r\n\x00'):
                raise ValueError('invalid modlist identity')
            key = name.casefold()
            if key in names:
                raise ValueError('duplicate modlist identity')
            names[key] = (name, text[0])
        lines.append((key, raw))
    return bom, lines, names


def compare_modlist(before, after, allowed_disabled_additions=()):
    """Report preservation without sorting/reconstructing either original input.

    Malformed/ambiguous input raises ValueError, including permissions that name
    an original row. A changed native catalogue is not implicit permission to
    change a profile. Unknown or active additions always fail.
    """
    if isinstance(allowed_disabled_additions, (str, bytes)):
        raise ValueError('addition permissions must be a collection of names')
    allowed = {}
    for name in allowed_disabled_additions:
        if not isinstance(name, str) or not name or name != name.strip():
            raise ValueError('invalid addition permission')
        key = name.casefold()
        if key in allowed:
            raise ValueError('duplicate addition permission')
        allowed[key] = name
    _, _, old = _modlist(before)
    bom, lines, current = _modlist(after)
    if old.keys() & allowed.keys():
        raise ValueError('addition permission overlaps original identity')
    added = {key: row for key, row in current.items() if key not in old}
    permitted = {key for key, (name, flag) in added.items()
                 if key in allowed and name == allowed[key] and flag == '-'}
    projection = bom + b''.join(raw for key, raw in lines if key not in permitted)
    missing = [name for key, (name, _) in old.items() if key not in current]
    changed = [name for key, (name, flag) in old.items()
               if key in current and current[key] != (name, flag)]
    unexpected = [name for key, (name, _) in added.items() if key not in permitted]
    return {'preserved': projection == before and not unexpected,
            'exactBytes': before == after, 'originalProjectionExact': projection == before,
            'missingOriginal': missing, 'changedOriginal': changed,
            'unexpectedAdditions': unexpected,
            'permittedDisabledAdditions': [added[key][0] for key in added if key in permitted]}


def compare_profiles(before, after, allowed_disabled_additions=None):
    """Compare ALL caller-pinned relative profile-file paths, including inactive.

    Mapping values are bytes. The optional permission mapping uses exact relative
    modlist.txt paths. Every other file and the complete file set stay byte-exact.
    Empty snapshots and unknown permission paths are rejected, never passed.
    """
    if not isinstance(before, dict) or not isinstance(after, dict) or not before:
        raise ValueError('nonempty profile snapshot mappings required')
    if any(not isinstance(k, str) or not isinstance(v, bytes)
           for snapshot in (before, after) for k, v in snapshot.items()):
        raise ValueError('profile snapshot requires string paths and bytes')
    permissions = {} if allowed_disabled_additions is None else allowed_disabled_additions
    if not isinstance(permissions, dict) or any(
            k not in before or k not in after or k.rsplit('/', 1)[-1] != 'modlist.txt'
            for k in permissions):
        raise ValueError('permissions must name existing modlist snapshots')
    files = {}
    for path in sorted(before.keys() & after.keys()):
        if path.rsplit('/', 1)[-1] == 'modlist.txt':
            files[path] = compare_modlist(before[path], after[path], permissions.get(path, ()))
        else:
            files[path] = {'preserved': before[path] == after[path],
                           'exactBytes': before[path] == after[path]}
    missing = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    return {'preserved': not missing and not added and all(r['preserved'] for r in files.values()),
            'missingFiles': missing, 'addedFiles': added, 'files': files}
