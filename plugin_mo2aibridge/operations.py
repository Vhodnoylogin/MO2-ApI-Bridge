# -*- coding: utf-8 -*-
"""Bounded, boot-scoped operation outcomes and HTTP retry deduplication."""
import hashlib
import json
import copy
import threading
import uuid
import traceback

from . import i18n

_local = threading.local()


def active():
    return getattr(_local, 'record', None)


class QueueExpired(RuntimeError):
    def __init__(self, job):
        self.job = job
        super().__init__(i18n.t('err.queueExpired'))


class JobPending(RuntimeError):
    def __init__(self, job):
        self.job = job
        super().__init__(i18n.t('err.jobPending'))


class Record:
    def __init__(self, identifier, route, body):
        self.identifier, self.route = identifier, route
        self.caller_run = body.get('callerRunId')
        self.caller_operation = body.get('operationId')
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.state, self.code, self.result, self.job = 'accepted', 202, None, None

    def snapshot(self):
        with self.lock:
            out = {'id': self.identifier, 'route': self.route, 'state': self.state,
                   'callerRunId': self.caller_run, 'operationId': self.caller_operation,
                   'terminal': self.done.is_set()}
            if self.job is not None:
                out['mainThreadJob'] = self.job.snapshot(include_result=False)
            if self.done.is_set():
                out.update(httpStatus=self.code, result=self.result)
            return out


class Operations:
    def __init__(self, boot_id, limit=4096, wait=120):
        self.boot_id, self.limit, self.wait = boot_id, int(limit), float(wait)
        self.lock = threading.RLock()
        self.records, self.keys = {}, {}
        self.closed = False

    def invoke(self, route, body, fn):
        if not isinstance(body, dict):
            raise ValueError(i18n.t('err.operationBody'))
        for name in ('idempotencyKey', 'operationId', 'callerRunId', 'expectedServerBootId'):
            value = body.get(name)
            if value is not None and (not isinstance(value, str) or not value or len(value) > 200):
                raise ValueError(i18n.t('err.operationField', field=name))
        expected = body.get('expectedServerBootId')
        if expected is not None and expected != self.boot_id:
            raise ValueError(i18n.t('err.bootMismatch'))
        key = body.get('idempotencyKey') or body.get('operationId')
        if key and expected is None:
            raise ValueError(i18n.t('err.expectedBoot'))
        # The caller's operation ID is an alternative retry key; explicit key and ID
        # must both refer to the same record. No eviction can re-enable an old launch.
        aliases = [(route, kind, value) for kind, value in
                   (('key', body.get('idempotencyKey')), ('id', body.get('operationId'))) if value]
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()
        with self.lock:
            found = [self.keys[a] for a in aliases if a in self.keys]
            if found:
                record, previous = found[0]
                if previous != digest or any(r is not record for r, _ in found):
                    raise ValueError(i18n.t('err.operationConflict'))
            else:
                if self.closed or len(self.records) >= self.limit:
                    raise RuntimeError(i18n.t('err.operationCapacity'))
                record = Record(self.boot_id + ':' + uuid.uuid4().hex, route, body)
                self.records[record.identifier] = record
                for a in aliases:
                    self.keys[a] = (record, digest)
                worker = threading.Thread(target=self._execute,
                                          args=(record, fn, copy.deepcopy(body)), daemon=True)
                worker.start()
        record.done.wait(self.wait)
        snapshot = record.snapshot()
        if not snapshot['terminal']:
            return 202, {'pending': True, 'operation': snapshot, 'serverBootId': self.boot_id}
        result = record.result
        if isinstance(result, dict):
            result = dict(result, operation=snapshot, serverBootId=self.boot_id)
        return record.code, result

    def _execute(self, record, fn, body):
        _local.record = record
        try:
            with record.lock:
                record.state = 'running'
            result = fn(body)
            code, state = 200, ('refused' if isinstance(result, dict)
                                and result.get('applied') is False else 'succeeded')
        except QueueExpired as exc:
            code, state = 504, 'failed'
            result = {'error': str(exc), 'code': 'queueExpired', 'job': exc.job.snapshot()}
        except ValueError as exc:
            code, state, result = 400, 'failed', {'error': str(exc), 'code': 'badRequest'}
        except Exception as exc:
            code, state, result = 500, 'failed', {'error': str(exc), 'code': 'bridgeFailure'}
            result['trace'] = traceback.format_exc()[-800:]
        finally:
            _local.record = None
        with record.lock:
            record.code, record.state, record.result = code, state, result
            record.done.set()

    def status(self, identifier):
        with self.lock:
            record = self.records.get(identifier)
        if record is None:
            raise ValueError(i18n.t('err.noOperation', id=identifier))
        return dict(record.snapshot(), serverBootId=self.boot_id)

    def close(self):
        with self.lock:
            self.closed = True
