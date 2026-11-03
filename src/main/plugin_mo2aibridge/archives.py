# -*- coding: utf-8 -*-
"""Archive paths/plans are pure; ArchiveExtractor owns 7-Zip and scratch resources.

Nothing in this module imports mobase, Qt, HTTP or the organizer. Extraction returns
an owned scratch directory to the caller only after success; all failure paths clean
up resources allocated here.
"""
import hashlib
import ntpath
import os
import shutil
import subprocess
import tempfile

from . import i18n


_RESERVED_NAMES = frozenset({'CON', 'PRN', 'AUX', 'NUL'} | {
    prefix + str(n) for prefix in ('COM', 'LPT') for n in range(1, 10)})
_FOMOD_CONTROL_FILES = frozenset({'moduleconfig.xml', 'info.xml', 'script.cs'})


class SelectionError(ValueError):
    """A localized mapping refusal with bounded, path-free diagnostic fields."""

    def __init__(self, reason, index=None):
        self.reason, self.index = reason, index
        super().__init__(i18n.t('err.installFiles'))


def selection_mappings(values):
    """One canonical selected-install contract for preflight and native callbacks.

    A source may be reused; destinations must be distinct regular-file paths.
    Reject file/directory collisions and MO2/installer metadata before any writes.
    """
    if not isinstance(values, list) or not values:
        raise SelectionError('invalid-files')
    result, destinations = [], set()
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != {'source', 'destination'}:
            raise SelectionError('invalid-mapping', index)
        try:
            source = archive_member(value['source'])
        except ValueError:
            raise SelectionError('unsafe-source', index) from None
        try:
            destination = archive_member(value['destination'])
        except ValueError:
            raise SelectionError('unsafe-destination', index) from None
        source_parts = source.casefold().split('/')
        key = destination.casefold()
        parts = key.split('/')
        # FOMOD is also a legitimate author asset directory. Refuse its known
        # installer controls, not explicitly selected runtime payload beneath it.
        if ('meta.ini' in source_parts or source_parts[-1] == 'fomod'
                or ('fomod' in source_parts[:-1]
                    and source_parts[-1] in _FOMOD_CONTROL_FILES)):
            raise SelectionError('metadata-source', index)
        if 'meta.ini' in parts or 'fomod' in parts:
            raise SelectionError('metadata-destination', index)
        if parts[0] == 'ini tweaks':
            raise SelectionError('ini-tweaks-destination', index)
        if key in destinations:
            raise SelectionError('duplicate-destination', index)
        destinations.add(key)
        result.append({'source': source, 'destination': destination})
    for index, row in enumerate(result):
        parts = row['destination'].casefold().split('/')
        if any('/'.join(parts[:end]) in destinations for end in range(1, len(parts))):
            raise SelectionError('destination-file-directory-collision', index)
    return result


def selected_members(members, sources):
    """Resolve explicit selections and FOMOD instructions once, without IO."""
    selected = set()
    for source in sources:
        key = archive_member(source).lower()
        if key not in members:
            raise ValueError(i18n.t('err.noPathInArchive', path=source))
        selected.add(members[key])
    selected.update(path for path in members.values() if
                    path.lower() == 'fomod/moduleconfig.xml' or
                    path.lower().endswith('/fomod/moduleconfig.xml'))
    return sorted(selected)


def walk_error(error):
    raise error


def tree_files(root):
    """Paths of every file in a folder, relative to it, lower-cased.

    Needed for an honest "before and after": the difference shows what was added, the
    intersection what was overwritten. Case is dropped because Windows does not distinguish
    it while archives carry both spellings.
    """
    out = set()
    for dp, _dn, fs in os.walk(root, onerror=walk_error):
        rel = os.path.relpath(dp, root)
        for f in fs:
            out.add((f if rel == '.' else os.path.join(rel, f)).lower())
    return out


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def archive_member(value):
    """A portable, unambiguous relative Windows filename, never an ADS or alias."""
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError(i18n.t('err.installFiles'))
    parts = value.replace('\\', '/').split('/')
    if ntpath.isabs(value) or any(
            p in ('', '.', '..') or p != p.rstrip(' .') or
            any(c in p for c in ':<>"|?*') or any(ord(c) < 32 for c in p) or
            p.split('.')[0].upper() in _RESERVED_NAMES for p in parts):
        raise ValueError(i18n.t('err.installFiles'))
    return '/'.join(parts)


def archive_files(listing, *, rar=False):
    """Validate a 7-Zip technical listing without trimming empty field values.

    A final directory may end in ``Block = ``. Whitespace is part of the field
    delimiter, so stripping the output changes a valid record into malformed input.
    Only empty lines delimit records; LF and CRLF retain exactly the same values.
    """
    members, seen, fields = {}, set(), {}
    for line in listing.splitlines() + ['']:
        if line:
            key, sep, value = line.partition(' = ')
            if not sep or not key or key in fields:
                raise ValueError(i18n.t('err.installFiles'))
            fields[key] = value
            continue
        if not fields:
            continue
        directory = fields.get('Folder') == '+' or fields.get('Attributes', '').startswith('D')
        path = fields.get('Path')
        if directory and isinstance(path, str):
            path = path.rstrip('/\\')
        path = archive_member(path)
        key = path.lower()
        if key in seen:
            raise ValueError(i18n.t('err.installFiles'))
        if (any(fields.get(name) == '+' for name in
                ('Encrypted', 'Split Before', 'Split After', 'Alternate Stream'))
                or (rar and fields.get('Volume Index', '') != '')
                or any(fields.get(name, '') for name in
                       ('Symbolic Link', 'Hard Link', 'Copy Link'))):
            # The receipt pins one original archive and independent regular files.
            # Never silently consume another volume or materialize link/ADS entries.
            raise ValueError(i18n.t('err.archiveUnsupported'))
        seen.add(key)
        if not directory:
            members[key] = path
        fields = {}
    return members


def archive_listing(listing):
    """Read archive properties as well as members; RAR4 omits member volume IDs."""
    listing = listing.replace('\r\n', '\n')
    marker = '\n----------\nPath = '
    preamble, separator, members = listing.partition(marker)
    _banner, properties_start, properties = preamble.partition('\n--\n')
    if not separator or not properties_start or marker in members:
        raise ValueError(i18n.t('err.installFiles'))
    members = 'Path = ' + members
    fields = {}
    for line in properties.splitlines():
        key, sep, value = line.partition(' = ')
        # Other properties (notably archive comments) can contain multiline text.
        # Never let a comment override an observed security property.
        if key not in ('Type', 'Multivolume', 'Volumes', 'Encrypted'):
            continue
        if not sep or key in fields:
            raise ValueError(i18n.t('err.installFiles'))
        fields[key] = value
    if ((fields.get('Type') in ('Rar', 'Rar5') and fields.get('Multivolume') != '-')
            or fields.get('Multivolume') == '+' or fields.get('Volumes', '1') != '1'
            or fields.get('Encrypted') == '+'):
        raise ValueError(i18n.t('err.archiveUnsupported'))
    return archive_files(members, rar=fields.get('Type') in ('Rar', 'Rar5'))


class ArchiveExtractor:
    """The process/resource boundary; the installer owns a successful result only."""

    def __init__(self, cfg):
        self.cfg = cfg

    def _tool(self):
        seven = self.cfg.seven_zip()
        if not seven:
            raise RuntimeError(i18n.t('err.no7z'))
        return seven

    def _run(self, arguments):
        return subprocess.run(arguments, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL,
                              timeout=self.cfg.timeout('unpack'))

    def _members(self, seven, archive):
        result = self._run([seven, 'l', '-slt', '-sccUTF-8', '--', archive])
        if result.returncode != 0:
            raise RuntimeError(i18n.t('err.nativeInstall', reason='archive-listing-failed'))
        return archive_listing(result.stdout)

    def _extract(self, seven, archive, selected=None):
        scratch, listing = None, None
        try:
            prefix = self.cfg.get('unpackDirName') + '-'
            scratch = tempfile.mkdtemp(prefix=prefix)
            arguments = [seven, 'x', '-spd', '-scsUTF-8', '-sccUTF-8',
                         '-o' + scratch, '-y']
            if selected is not None:
                descriptor, listing = tempfile.mkstemp(prefix=prefix, suffix='.txt')
                with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
                    output.writelines('"' + path + '"\n' for path in selected)
                arguments.append('-i@' + listing)
            result = self._run(arguments + ['--', archive])
            if result.returncode != 0:
                tail = int(self.cfg.get('unpackErrorTail') or 300)
                raise RuntimeError(i18n.t('err.unpack', detail=(result.stdout or result.stderr)[-tail:]))
            if selected is not None and tree_files(scratch) != {
                    path.replace('/', os.sep).lower() for path in selected}:
                raise RuntimeError(i18n.t('err.nativeInstall', reason='selected-extraction-inventory'))
            # Remove the list before transferring scratch ownership to the caller.
            if listing:
                os.remove(listing)
                listing = None
            return scratch
        except BaseException:
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)
            raise
        finally:
            if listing and os.path.isfile(listing):
                os.remove(listing)

    def unpack(self, archive):
        seven = self._tool()
        # Apply the same archive/volume/link guards to both installation backends.
        self._members(seven, archive)
        return self._extract(seven, archive)

    def unpack_selected(self, archive, sources):
        seven = self._tool()
        selected = selected_members(self._members(seven, archive), sources)
        return self._extract(seven, archive, selected)
