# -*- coding: utf-8 -*-
"""The journal: the level says WHAT is written, a sink says WHERE, and a sink that dies
costs neither the record nor the plugin.

Three questions that used to be answered in one place, and the plugin paid for it. `log()`
wrote the same line to MO2's log and to its own file, with both writes wrapped in a silent
`except`: a folder that had gone away, a full disk, the file held open by somebody else -
and the record vanished with no trace anywhere. That is the very silence the file was added
to end, so the fix is to keep the three apart:

- **What** - the level of the record against the threshold of the sink. The thresholds
  differ on purpose: MO2's log gets what a person needs to see, the file gets everything.
- **Where** - a set of sinks, attached and dropping out independently of one another.
- **On failure** - the sink drops out, not the journal. The survivors get the record and,
  ahead of it, word of the sink that died: keeping quiet about a lost sink would lose the
  same information a second time. Nothing here raises - the journal is called from error
  handling, and a journal that throws turns a reported failure into an unreported one.

Levels are numbers with names rather than strings at the call site: the set is closed, and a
misspelt name must not be able to silence a record.
"""
import io
import threading

from . import i18n

DEBUG, INFO, WARN, ERROR = 10, 20, 30, 40
_NAMES = {DEBUG: 'debug', INFO: 'info', WARN: 'warn', ERROR: 'error'}


def level_name(level):
    """The short word for a level. A number nobody named prints as itself rather than being
    dropped: a line is never worth less than its label."""
    return _NAMES.get(int(level), str(level))


class Sink(object):
    """One place to write to, with a threshold of its own.

    A subclass overrides `emit` and catches nothing: the journal catches, marks the sink as
    dropped out and tells the others. `failure` holds the reason and is the only difference
    between a live sink and a dead one - a dead sink is never called again, so a folder that
    went away costs one failed write rather than one per line.
    """

    __slots__ = ('level', 'failure')

    def __init__(self, level=INFO):
        self.level = int(level)
        self.failure = None

    @property
    def alive(self):
        return self.failure is None

    def accepts(self, level):
        return self.alive and int(level) >= self.level

    def emit(self, line):
        raise NotImplementedError

    def __repr__(self):
        return '%s(%s%s)' % (type(self).__name__, level_name(self.level),
                             '' if self.alive else ', down: %s' % self.failure)


class QtSink(Sink):
    """MO2's own log.

    Written through `qCritical` and not `qWarning`, and that is not shouting: MO2's default
    log level drops plugin warnings, and the reason for a refusal was then lost entirely.
    The import stays inside the write on purpose - the module must be importable outside the
    manager, where there is no PyQt6 at all, and the checks do exactly that.
    """

    __slots__ = ()

    def emit(self, line):
        from PyQt6.QtCore import qCritical
        qCritical(line)


class FileSink(Sink):
    """A file next to the plugin: everything, including what MO2's log level would drop.

    Opened and closed around every line rather than held open. The plugin writes a line
    every few minutes, so nothing is saved by keeping the handle, while holding it would
    lock the log for the whole of an MO2 session - and reading that file is what a person
    does while the manager is still running.
    """

    __slots__ = ('path',)

    def __init__(self, path, level=DEBUG):
        super().__init__(level)
        self.path = path

    def emit(self, line):
        with io.open(self.path, 'a', encoding='utf-8') as fh:
            fh.write(line + chr(10))


class Journal(object):
    """The set of sinks and the single point of writing. `log` never raises."""

    def __init__(self, sinks=(), prefix=''):
        self.sinks = list(sinks)
        self.prefix = prefix
        # The bridge logs from the server thread and from MO2's main thread alike.
        self._lock = threading.Lock()

    def add(self, sink):
        with self._lock:
            self.sinks.append(sink)
        return sink

    def line(self, level, text):
        """A finished line: who, at what level, what. No timestamp - MO2's log stamps its
        own lines, and the file is read next to it."""
        head = '[%s] ' % self.prefix if self.prefix else ''
        return '%s%-5s %s' % (head, level_name(level), text)

    def log(self, level, text):
        with self._lock:
            line = self.line(level, text)
            failed = []
            for sink in [s for s in self.sinks if s.accepts(level)]:
                try:
                    sink.emit(line)
                except Exception as exc:
                    sink.failure = repr(exc)
                    failed.append(sink)
            for sink in failed:
                # The survivors learn which sink died and why - and they learn it before
                # anyone wonders where the lines went.
                note = self.line(ERROR, i18n.t('log.sinkDown', sink=type(sink).__name__,
                                               error=sink.failure))
                for other in self.sinks:
                    if other is sink or not other.accepts(ERROR):
                        continue
                    try:
                        other.emit(note)
                    except Exception as exc:
                        other.failure = repr(exc)
