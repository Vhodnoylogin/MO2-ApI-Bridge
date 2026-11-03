# -*- coding: utf-8 -*-
"""Bounded, boot-scoped operation outcomes and HTTP retry deduplication."""
import hashlib
import json
import copy
import threading
import uuid
import traceback
import time
from collections import deque
from contextlib import contextmanager

from . import i18n

_local = threading.local()


def active():
    return getattr(_local, 'record', None)


def mutation():
    return getattr(_local, 'mutation', None)


@contextmanager
def using_mutation(lease):
    previous = mutation()
    _local.mutation = lease
    try:
        yield
    finally:
        _local.mutation = previous


class MutationOwner:
    """Host-lifetime logical exclusion, without holding a lock across external calls.

    Native jobs retain the lease independently of their waiting caller. Nested Qt
    events can therefore refuse a competing change rather than block the UI.
    This coordinates Bridge callers only; native/UI writers still need readback.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self._current = None

    def acquire(self, operation, boot_id):
        with self.lock:
            if self._current is not None:
                return None
            self._current = MutationLease(self, operation, boot_id)
            return self._current

    def snapshot(self):
        with self.lock:
            lease = self._current
            return None if lease is None else {
                'op': lease.operation, 'serverBootId': lease.boot_id}


class MutationLease:
    def __init__(self, owner, operation, boot_id):
        self.owner, self.operation, self.boot_id = owner, operation, boot_id
        self.references = 1

    def retain(self):
        with self.owner.lock:
            if self.owner._current is not self:
                raise RuntimeError(i18n.t('err.runnerClosed'))
            self.references += 1
        return self

    def release(self):
        with self.owner.lock:
            self.references -= 1
            if self.references == 0:
                self.owner._current = None

    def active_for(self, boot_id):
        with self.owner.lock:
            return self.boot_id == boot_id and self.owner._current is self


class QueueExpired(RuntimeError):
    def __init__(self, job):
        self.job = job
        super().__init__(i18n.t('err.queueExpired'))


class JobPending(RuntimeError):
    def __init__(self, job):
        self.job = job
        super().__init__(i18n.t('err.jobPending'))


class Record:
    def __init__(self, identifier, route, body, stage_limit=16, note=None):
        self.identifier, self.route = identifier, route
        self.caller_run = body.get('callerRunId')
        self.caller_operation = body.get('operationId')
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.state, self.code, self.result, self.job = 'accepted', 202, None, None
        self.stages = deque(maxlen=max(1, int(stage_limit)))
        self.note = note

    def stage(self, state, job=None):
        with self.lock:
            event = {'stage': state, 'monotonicSec': time.monotonic()}
            if job is not None:
                event['jobId'] = job
            self.stages.append(event)
        # Only internal route, generated IDs and fixed stage names enter the audit.
        # Bodies, retry keys, exception strings and signed URLs are never logged.
        if self.note is not None and state in ('accepted', 'succeeded', 'refused', 'failed'):
            try:
                self.note(i18n.t('log.operationEvent', id=self.identifier,
                                 route=self.route, stage=state))
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            out = {'id': self.identifier, 'route': self.route, 'state': self.state,
                   'callerRunId': self.caller_run, 'operationId': self.caller_operation,
                   'terminal': self.done.is_set(), 'stages': list(self.stages)}
            job = self.job
            if self.done.is_set():
                out.update(httpStatus=self.code, result=self.result)
        # Jobs may publish a stage while holding their own lock. Never acquire
        # the job lock while retaining the record lock in the polling path.
        if job is not None:
            out['mainThreadJob'] = job.snapshot(include_result=False)
        return out


class Operations:
    def __init__(self, boot_id, limit=4096, wait=120, stage_limit=16, note=None):
        self.boot_id, self.limit, self.wait = boot_id, int(limit), float(wait)
        self.lock = threading.RLock()
        self.records, self.keys = {}, {}
        self.closed = False
        self.stage_limit, self.note = stage_limit, note

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
                record = Record(self.boot_id + ':' + uuid.uuid4().hex, route, body,
                                self.stage_limit, self.note)
                record.stage('accepted')
                self.records[record.identifier] = record
                for a in aliases:
                    self.keys[a] = (record, digest)
                try:
                    worker = threading.Thread(target=self._execute,
                                              args=(record, fn, copy.deepcopy(body)), daemon=True)
                    worker.start()
                except Exception as exc:
                    # Keep the published aliases terminal: a retry must never wait for
                    # a worker that could not be prepared or started.
                    with record.lock:
                        record.code, record.state = 500, 'failed'
                        record.result = {'error': str(exc), 'code': 'workerStartFailed'}
                        record.stage('failed')
                        record.done.set()
        # Existing pending records are reconciliation requests, not a second long
        # wait occupying the finite HTTP handler pool.
        if not found:
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
            with self.lock:
                if self.closed:
                    raise RuntimeError(i18n.t('err.operationCapacity'))
            with record.lock:
                record.state = 'running'
            record.stage('running')
            result = fn(body)
            if isinstance(result, dict) and result.get('failed'):
                code, state = 500, 'failed'
            else:
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
            record.stage(state)
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

    def pending(self):
        with self.lock:
            return [record for record in self.records.values() if not record.done.is_set()]


class RetiredOperations:
    """Bounded outcomes of work still running at HTTP shutdown.

    Completed boots are otherwise collectible. Old keys never become executable
    in a fresh boot, but interrupted callers can reconcile their final outcome.
    """
    def __init__(self):
        self._records = {}
        self._lock = threading.RLock()

    def retire(self, store):
        with self._lock:
            for record in store.pending():
                self._records[record.identifier] = (store.boot_id, record)

    def draining(self):
        with self._lock:
            return any(not record.done.is_set() for _, record in self._records.values())

    def remaining(self, limit):
        with self._lock:
            return max(0, int(limit) - len(self._records))

    def full(self, limit):
        return self.remaining(limit) == 0

    def status(self, identifier):
        with self._lock:
            entry = self._records.get(identifier)
        return None if entry is None else dict(entry[1].snapshot(), serverBootId=entry[0])
