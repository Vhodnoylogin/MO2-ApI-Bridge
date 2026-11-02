# -*- coding: utf-8 -*-
"""Pure assessment of historical installation evidence; no IO or organizer access."""
import ntpath


def provenance_status(record, current, installed_files=None, metadata_error=None, limit=200):
    """Compare historical metadata only, without certifying the installed selection/bytes.

    Schema 1 receipts predating nativeMetadata cannot prove a current binding. Their
    archive name can still contradict the current native association after Replace.
    No manifest walk, archive access, payload read, hashing or write belongs here.
    """
    status = {'state': 'unknown', 'scope': 'native-metadata',
              'archiveHashVerified': False, 'payloadHashesVerified': False,
              'checks': [], 'reasons': []}
    if record is None:
        status.update(state='absent')
        return status
    if not isinstance(record, dict):
        status.update(state='malformed', reasons=['record-not-object'])
        return status
    if type(record.get('schemaVersion')) is not int or record['schemaVersion'] != 1:
        status['reasons'].append('unsupported-schema')
        return status

    def archive_name(value):
        return ntpath.basename(value).casefold() if isinstance(value, str) and value else None

    def text(value):
        return value if isinstance(value, str) and value else None

    def identity(value):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            number = int(value)
            return number if number >= 0 else None
        except ValueError:
            return None

    def pairs(value):
        if not isinstance(value, list) or len(value) > max(0, limit):
            return None
        result = []
        for row in value:
            if not isinstance(row, dict):
                return None
            mod_id, file_id = identity(row.get('modId')), identity(row.get('fileId'))
            if mod_id is None or file_id is None or mod_id <= 0 or file_id <= 0:
                return None
            result.append((mod_id, file_id))
        return sorted(result)

    def check(field, recorded, actual, normalize):
        before, now = normalize(recorded), normalize(actual)
        state = ('unknown' if before is None or now is None else
                 'match' if before == now else 'mismatch')
        status['checks'].append({'field': field, 'state': state,
                                 'recorded': recorded, 'current': actual})

    check('archive', record.get('archive'), current.get('installationFile'), archive_name)
    snapshot = record.get('nativeMetadata')
    if not isinstance(snapshot, dict):
        status['reasons'].append('missing-native-metadata')
    else:
        for field, normalize in (('installationFile', archive_name), ('version', text),
                                  ('nexusId', identity), ('gameName', text)):
            check(field, snapshot.get(field), current.get(field), normalize)
        recorded_files = snapshot.get('installedFiles')
        if pairs(recorded_files) is None or pairs(installed_files) is None:
            # Keep reply evidence bounded even for an oversized or malformed receipt.
            status['checks'].append({'field': 'installedFiles', 'state': 'unknown'})
            status['reasons'].append(metadata_error or 'installed-files-unavailable')
        else:
            check('installedFiles', recorded_files, installed_files, pairs)
    states = [row['state'] for row in status['checks']]
    if 'mismatch' in states:
        status['state'] = 'stale'
    elif not status['reasons'] and states and 'unknown' not in states:
        status['state'] = 'metadata-match'
    elif not status['reasons']:
        status['reasons'].append('metadata-incomplete')
    return status
