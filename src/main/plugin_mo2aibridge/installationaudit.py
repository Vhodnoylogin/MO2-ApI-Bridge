# -*- coding: utf-8 -*-
"""Advisory selected-install planning and read-only current-state verification.

The caller supplies the original archive and explicit selection. Neither path
installs, reserves a target, refreshes native models or persists provenance.
Selected extraction writes owned scratch only. Native before/after observations
can detect divergence, but cannot track every external writer or make IO atomic.
"""
import hashlib
import json
import ntpath
import os

import mobase

from . import nativeinstall
from .archives import file_sha256, tree_files
from .base import Domain, inside
from .reading import installed_file_metadata


class _Refusal(Exception):
    def __init__(self, reason, check='context', state='unavailable'):
        self.reason, self.check, self.state = reason, check, state


def _canonical(path):
    return os.path.normcase(os.path.realpath(path))


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        value = int(value)
        return value if value > 0 else None
    except ValueError:
        return None


def _boolean(value):
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ('true', 'false', '1', '0'):
        return value.lower() in ('true', '1')
    return None


class InstallAudit(Domain):
    def __init__(self, ctx, guard, installer, reader):
        super().__init__(ctx, guard)
        self.installer, self.reader = installer, reader

    @staticmethod
    def _result(kind):
        return {'readOnly': True, 'audit': kind, 'accepted': False, 'ready': False,
                'verified': False, 'phase': 'selection-inputs', 'checks': [],
                'noNativeStart': True, 'noTargetCreated': True, 'scratchWrites': False,
                'scratchExtractionAttempted': False, 'scratchWritesPossible': False,
                'reservationCreated': False, 'applyGuaranteed': False,
                'provenanceCreated': False, 'installationComplete': False,
                'sourceMetadataPreservationVerified': False,
                'privateMetadataPreservation': {'state': 'not-verifiable',
                                                'reason': 'no-before-fingerprints'},
                'originalOperationUnchanged': True}

    @staticmethod
    def _check(result, name, state, reason=None, required=True, **evidence):
        row = dict(check=name, state=state, required=required, **evidence)
        if reason:
            row['reason'] = reason
        result['checks'].append(row)
        return state == 'verified'

    def _boundary(self, body, name, *, readiness=False):
        """Qt snapshot only; no mutation lease, refresh or model setters."""
        try:
            self.ctx.expected(body)
        except ValueError:
            raise _Refusal('expected-context-mismatch', 'expected-context', 'mismatch') from None
        if self.ctx.stopping:
            raise _Refusal('bridge-stopping')
        if self.ctx.mutations.snapshot() is not None:
            raise _Refusal('bridge-mutation-active', 'bridge-mutation', 'mismatch')
        if readiness:
            if self.guard.refusal('op.install'):
                raise _Refusal('manager-busy', 'native-readiness', 'mismatch')
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is None:
                raise _Refusal('qt-application-unavailable', 'native-readiness')
            if app.activeModalWidget() is not None:
                raise _Refusal('preexisting-modal', 'native-readiness', 'mismatch')
        identity = self.reader.native_identity(name)
        downloads = os.path.realpath(self.o.downloadsPath())
        identity = dict(identity, downloadsPath=downloads)
        target = os.path.join(identity['modsPath'], name)
        if not inside(identity['modsPath'], target):
            raise _Refusal('target-outside-mods', 'target', 'mismatch')
        native_target = identity.get('target')
        if native_target is not None and _canonical(native_target.get('path') or '') != _canonical(target):
            raise _Refusal('target-model-path-mismatch', 'target', 'mismatch')
        return {'identity': identity, 'target': target,
                'targetExists': os.path.lexists(target), 'downloads': downloads}

    def _inputs(self, body, result):
        if not isinstance(body, dict):
            raise _Refusal('request-not-object', 'selection', 'mismatch')
        values = self.installer.selection_inputs(body)
        result.update(mod=values[1], archive=os.path.basename(values[0]))
        result['phase'] = 'native-preflight'
        return values

    def _preflight(self, body, arc, name, result, *, existing):
        before = self.run_main(lambda: self._boundary(body, name, readiness=not existing))
        result['path'] = before['target']
        result['snapshot'] = self.reader.snapshot_evidence({'before': before['identity'], 'after': None})
        if _canonical(os.path.dirname(arc)) != _canonical(before['downloads']):
            raise _Refusal('archive-outside-downloads', 'archive-location', 'mismatch')
        self._check(result, 'archive-location', 'verified')
        if existing:
            if not before['targetExists'] or not os.path.isdir(before['target']):
                raise _Refusal('target-unavailable', 'target')
            if before['identity'].get('target') is None:
                raise _Refusal('target-model-unavailable', 'target')
        elif before['targetExists'] or before['identity'].get('target') is not None:
            raise _Refusal('target-exists', 'target', 'mismatch')
        self._check(result, 'target', 'verified', exists=existing)
        if not existing:
            try:
                self.run_main(lambda: nativeinstall.check_available(self.o))
            except nativeinstall.NativeInstallError as exc:
                raise _Refusal(exc.code, 'native-companion') from None
            self._check(result, 'native-companion', 'verified')
        return before

    def _evidence(self, arc, files, selection, expected, result):
        result['phase'] = 'selection-evidence'
        result.update(scratchExtractionAttempted=True, scratchWritesPossible=True,
                      scratchWrites=None)
        evidence = self.installer.selection_evidence(arc, files, selection, expected)
        result['scratchWrites'] = True
        result['selectionEvidence'] = evidence
        result['suppliedSelectionSha256'] = hashlib.sha256(json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')).hexdigest()
        self._check(result, 'archive-selection', 'verified', files=len(evidence['files']))
        return evidence

    def _finish(self, body, arc, expected, name, before, result):
        result['phase'] = 'final-observation'
        after = self.run_main(lambda: self._boundary(body, name, readiness=result['audit'] == 'preview'))
        result['snapshot'] = self.reader.snapshot_evidence(
            {'before': before['identity'], 'after': after['identity']})
        same = result['snapshot']['state'] == 'no-observed-divergence'
        self._check(result, 'native-snapshot', 'verified' if same else 'mismatch',
                    None if same else 'observed-divergence')
        same_target = before['targetExists'] == after['targetExists']
        self._check(result, 'target-presence', 'verified' if same_target else 'mismatch',
                    None if same_target else 'target-presence-changed')
        # This is the final archive read, after all extraction/payload/native work.
        matches = file_sha256(arc) == expected
        self._check(result, 'archive-final-hash', 'verified' if matches else 'mismatch',
                    None if matches else 'archive-changed')
        return all(row['state'] == 'verified' for row in result['checks'] if row['required'])

    def _refused(self, result, exc):
        if isinstance(exc, _Refusal):
            reason, name, state = exc.reason, exc.check, exc.state
        else:
            # SelectionError exposes safe machine codes and an optional row index;
            # never echo generic exception messages or arbitrary archive metadata.
            reason = getattr(exc, 'reason', None) or (
                'invalid-selection' if isinstance(exc, ValueError) else 'observation-unavailable')
            name, state = ('selection', 'mismatch') if isinstance(exc, ValueError) else (
                'observation', 'unavailable')
        refusal = {'phase': result['phase'], 'reason': reason}
        index = getattr(exc, 'index', None)
        if type(index) is int:
            refusal['index'] = index
        result.update(accepted=False, ready=False, verified=False, refusal=refusal)
        self._check(result, name, state, reason)
        return result

    def preview(self, body):
        result = self._result('preview')
        try:
            arc, name, files, selection, expected = self._inputs(body, result)
            before = self._preflight(body, arc, name, result, existing=False)
            self._evidence(arc, files, selection, expected, result)
            ready = self._finish(body, arc, expected, name, before, result)
            result.update(accepted=True, ready=ready, phase='complete')
            return result
        except Exception as exc:
            return self._refused(result, exc)

    def _payload(self, target, manifest, result):
        result['phase'] = 'payload'
        wanted = {row['destination'].replace('/', os.sep).lower() for row in manifest}
        actual = tree_files(target) - {'meta.ini'}
        missing, extra = sorted(wanted - actual), sorted(actual - wanted)
        self._check(result, 'payload-inventory', 'mismatch' if missing or extra else 'verified',
                    'payload-inventory' if missing or extra else None,
                    expectedFiles=len(wanted), observedFiles=len(actual),
                    missingCount=len(missing), extraCount=len(extra),
                    missing=missing[:max(0, self.limit())], extra=extra[:max(0, self.limit())])
        if missing or extra:
            self._check(result, 'payload-hashes', 'not-run', 'inventory-mismatch')
            return
        mismatches = []
        for row in manifest:
            path = os.path.join(target, *row['destination'].split('/'))
            if (not inside(target, path) or not os.path.isfile(path)
                    or os.path.getsize(path) != row['bytes'] or file_sha256(path) != row['sha256']):
                mismatches.append(row['destination'])
        self._check(result, 'payload-hashes', 'mismatch' if mismatches else 'verified',
                    'payload-hash' if mismatches else None,
                    files=len(manifest), mismatchCount=len(mismatches),
                    mismatches=mismatches[:max(0, self.limit())])
        # Detect inventories changing while hashes were being read.
        stable = tree_files(target) - {'meta.ini'} == actual
        self._check(result, 'payload-inventory-after', 'verified' if stable else 'mismatch',
                    None if stable else 'payload-inventory-changed')

    def _accounting(self, arc, target, identity, result):
        """Read allowlisted source/accounting values; never copy private fields."""
        result['phase'] = 'native-accounting'
        from PyQt6.QtCore import QSettings
        if not os.path.isfile(arc + '.meta'):
            raise _Refusal('download-metadata-unavailable', 'source-identity')
        download = QSettings(arc + '.meta', QSettings.Format.IniFormat)
        download.setFallbacksEnabled(False)
        if download.status() != QSettings.Status.NoError:
            raise _Refusal('download-metadata-unreadable', 'source-identity')
        mod_id, file_id = (_positive(download.value(key)) for key in ('modID', 'fileID'))
        valid_ids = mod_id is not None and file_id is not None
        self._check(result, 'source-identity', 'verified' if valid_ids else 'unavailable',
                    None if valid_ids else 'source-identity-missing-or-invalid',
                    modId=mod_id, fileId=file_id)
        native = identity['target']
        same_archive = ntpath.basename(native.get('installationFile') or '').casefold() == ntpath.basename(arc).casefold()
        self._check(result, 'archive-association', 'verified' if same_archive else 'mismatch',
                    None if same_archive else 'archive-association-mismatch')
        same_mod = valid_ids and _positive(native.get('nexusId')) == mod_id
        self._check(result, 'native-mod-id', 'verified' if same_mod else 'mismatch',
                    None if same_mod else 'native-mod-id-mismatch')
        for field, key in (('gameName', 'gameName'), ('version', 'version')):
            expected = download.value(key)
            if not isinstance(expected, str) or not expected:
                self._check(result, 'native-' + field, 'unavailable', 'source-value-unavailable')
                continue
            if field == 'version':
                expected = mobase.VersionInfo(expected).displayString()
            matches = native.get(field) == expected
            self._check(result, 'native-' + field, 'verified' if matches else 'mismatch',
                        None if matches else 'source-value-mismatch')
        rows, error = installed_file_metadata(target, self.limit())
        pairs = None if rows is None else [(_positive(row.get('modId')), _positive(row.get('fileId')))
                                          for row in rows]
        exact_ids = (valid_ids and pairs is not None and
                     all(mod is not None and file is not None for mod, file in pairs) and
                     (mod_id, file_id) in pairs)
        self._check(result, 'native-installed-files', 'unavailable' if rows is None else
                    'verified' if exact_ids else 'mismatch',
                    None if exact_ids else error or 'installed-file-identity',
                    observedPairs=pairs)
        installed = _boolean(download.value('installed'))
        uninstalled = _boolean(download.value('uninstalled', False))
        accounted = installed is True and uninstalled is False
        self._check(result, 'download-sidecar', 'verified' if accounted else 'mismatch',
                    None if accounted else 'download-accounting',
                    installed=installed, uninstalled=uninstalled)
        if download.status() != QSettings.Status.NoError:
            raise _Refusal('download-metadata-unreadable', 'download-sidecar')
        try:
            observed = self.run_main(lambda: nativeinstall.observe_downloads(self.o, arc))
            result['downloadObservation'] = observed
            good = observed.get('verified') is True and observed.get('installed') is True
            self._check(result, 'native-download-row', 'verified' if good else 'unavailable',
                        None if good else 'download-model-unverified')
        except nativeinstall.NativeInstallError as exc:
            result['downloadObservation'] = exc.receipt
            self._check(result, 'native-download-row', 'unavailable', exc.code)

    def _reference(self, body, result):
        identifier = body.get('originalOperationId')
        if identifier is None:
            return
        if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
            raise _Refusal('invalid-original-operation-id', 'original-operation', 'mismatch')
        link = {'id': identifier, 'referenceAvailable': False,
                'requestBinding': 'not-verified', 'suppliedRequestBoundToOriginal': False}
        result['originalOperation'] = link
        prior = self.ctx.retired.status(identifier)
        if prior is None:
            try:
                prior = self.ctx.operations.status(identifier)
            except ValueError:
                return
        if prior.get('route') != '/install':
            link['reason'] = 'original-route-mismatch'
            return
        outcome = {key: prior[key] for key in ('state', 'httpStatus', 'terminal', 'serverBootId')
                   if key in prior}
        original = prior.get('result')
        if isinstance(original, dict):
            for key in ('failed', 'partial', 'installationComplete', 'reconciliationRequired'):
                if type(original.get(key)) is bool:
                    outcome[key] = original[key]
        link.update(referenceAvailable=True, outcome=outcome)

    def verify(self, body):
        result = self._result('verify')
        result['scope'] = 'current-archive-selection-payload-native-accounting'
        try:
            arc, name, files, selection, expected = self._inputs(body, result)
            self._reference(body, result)
            before = self._preflight(body, arc, name, result, existing=True)
            evidence = self._evidence(arc, files, selection, expected, result)
            self._payload(before['target'], evidence['files'], result)
            self._accounting(arc, before['target'], before['identity'], result)
            verified = self._finish(body, arc, expected, name, before, result)
            result.update(accepted=True, verified=verified, phase='complete')
            return result
        except Exception as exc:
            return self._refused(result, exc)
