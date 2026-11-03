# MO2 ApI Bridge — manifest for an AI assistant

You are looking at the self-contained MO2 ApI Bridge repository. This file exists so that any chat handed this
folder — with no other context, no project history and no conversation to fall back on —
can work on it correctly from the first minute.

Read this file before touching anything. The section **Do not "fix" these** is the reason
it exists: several things here look like mistakes and are not.

---

## 1. What this is

Mod Organizer 2 does not keep a mod setup on disk. It builds a virtual `Data` folder in
memory when it starts (USVFS), so nothing outside the process can see the truth: `modlist.txt`
lags behind what the manager holds, and which mod wins a contested file is only knowable from
inside.

**MO2 ApI Bridge is a Python plugin that runs inside MO2 and exposes a local HTTP API**, so a
script or an AI assistant can ask the live manager questions and give it commands. Port 8930,
loopback only, never exposed outside the machine. Every request needs an `X-Token` header; the
token is regenerated each time MO2 starts and written next to the plugin.

Before it existed, changing a setup by script meant: close MO2, rewrite `modlist.txt` by hand,
open it again — slow, lossy, and blind to the virtual `Data`.

### The contract it must keep

- **The bridge only executes. The caller remembers.** It retains boot-scoped launch records, bounded operation outcomes and retry keys;
  the caller owns rollback decisions and durable reversal data.
- **The bridge decides nothing** for the caller: it does not guess a "correct" priority, does
  not pick between options, does not roll anything back on its own.
- **The bridge delegates nothing back.** If an operation needs an archive unpacked, files
  copied, a folder removed — it does that itself, rather than answering "do this by hand".
- **Every reply to a change must contain everything needed to reverse it**: what was there
  before, what is there now, where the files went. This is not a convenience; it is the only
  safety net the caller has.

---

## 2. Layout

Production code, locale, runtime user documentation and licence live in
`src/main/plugin_mo2aibridge/`. Packaging source is `src/main/pack.py`.
All tests/fixtures and their instructions live in `src/test/`; generated artifacts
belong in ignored `target/` or an explicit external build directory. Root READMEs
describe build/install commands; runtime READMEs document every module/API contract.

Only runtime package files ship. The installed directory in MO2 must be
`plugins/mo2aibridge/`, because Python takes the package name from that directory.
`PLUGIN_ID` remains a literal string in `__init__.py`; never derive it from the
source directory. The packer writes the correct installed package name.

`main` holds the released source; one `develop` branch holds unfinished work.
Version 2.3.6's exact packaged runtime passed isolated MO2 2.5.2 native acceptance
with a synthetic ZIP and positive source IDs: install/checkpoint/provenance,
Installed/Uninstalled accounting, guarded removal, replay and manager-restart
readback. No game was launched. RAR4/RAR5 STORE checks use real 7-Zip outside MO2;
they do not establish native RAR installation or compressed/solid codec coverage.
Historical receipt existence is not current installation proof: `/mod` preserves
`installProvenance` and adds metadata-only `installProvenanceStatus`. Matching
metadata never certifies present archive/payload bytes or FOMOD compatibility.

---

## 3. Layers

Each layer knows only the one below it. Keep it that way.

| File | Knows about | Knows nothing about |
|---|---|---|
| `winapi.py` | Windows processes and windows | MO2, networking |
| `operations.py` | retry keys and worker outcomes | MO2, Qt widgets |
| `runtime.py` | threads, sockets, JSON, the token | what the routes do |
| `services.py` and the domain files | `mobase` and the setup | HTTP, JSON, tokens |
| `modui.py` | exact MO2 2.5.2 ModList Qt removal and confirmation | HTTP, profile persistence policy |
| `routes.py` | path names | `mobase`, sockets |
| `plugin.py` | the MO2 plugin lifecycle | it does nothing by hand, it only wires things |
| `i18n.py` | strings | across all layers |
| `journal.py` | levels and sinks | where a written line goes, and what happens when a sink dies |

Adding a route means adding a line to `routes.py`. Method names on the facade are a contract:
`routes.py` and the tests both refer to them.

---

Pure domain decisions belong in functions with explicit inputs: `archives.py`
validates paths/listings/mappings, `provenance.py` compares metadata, and
`updatepolicy.py` calculates update verdicts without modifying inputs. Launch
normalization and plugin-marker transformation are also pure. Keep organizer,
filesystem, process and network access in adapters; do not add stateful classes
for calculations. `ArchiveExtractor` owns process/scratch lifecycle; `Installer`
coordinates native/copy installation; `Reader` snapshots host state and attaches
metadata evidence. Retain the Services facade and existing HTTP contracts.

## 4. The two locks

**Busy lock.** While a game or tool runs under MO2, the virtual `Data` is mounted into another
process. Changing mods, order or plugins then means the running program sees one setup while
the files on disk describe another. So all thirteen setup-write routes refuse, and every read keeps
working. Busy is decided by three independent sources, and that is not redundancy:

1. MO2's own callbacks (`onAboutToRun` / `onFinishedRun`) — they know about launches through
   the manager but do not survive a missed `onFinishedRun`.
2. Process enumeration — it knows the truth about live programs but cannot tell a launch
   through MO2 from one beside it. **Only this source catches the game**: MO2 starts it via
   `sksevr_loader.exe`, which exits immediately, leaving `SkyrimVR.exe` under a different name.
3. Whether MO2's main window is enabled — the closest thing to what a person sees.

**Irreversible lock.** Priority, mod/profile rename, remove, `/run`, and `/install` with `mode=replace`
require this in the request body:

```json
{ "iUnderstandTheRisk": "yes-I-read-the-docs-and-accept-irreversible-changes" }
```

Without it the route does nothing and returns the path to `README.md` **inside the package** —
which is why the documentation must ship with the code.

---

## 5. Do not "fix" these

Every line here is a past failure. The reason is given so nobody is tempted to simplify it away.

| Where | What looks odd | Why it stays |
|---|---|---|
| `i18n.t(key, /, **kw)` | positional-only marker | without it a substitution named `key` raises `t() got multiple values for argument 'key'`, and the irreversible lock returned a traceback instead of a refusal |
| `MainThreadRunner.call` | a check for "am I the main thread" | otherwise the thread queues a job for itself and waits for itself — deadlock until the timeout |
| `busy.py` window check | finds the window without reading its title | `GetWindowTextW` sends `WM_GETTEXT` and waits on the window's thread — it would hang in exactly the case the check exists for |
| the same place | the window handle is validated, never trusted from cache | the splash-screen handle captured at startup dies, and `IsWindowEnabled` on a dead handle answers "disabled" — the bridge called MO2 permanently busy for thirty-six hours |
| `busy.py` bookkeeping | foreign launch records are **never** cleared; own ones are, by a `mine` flag | clearing foreign ones is wrong: MO2 reports completion itself, and until it does its lock holds — the record was the only evidence. Not clearing own ones is also wrong: MO2 never reports completion for `startApplication` |
| `/run` waiting | uses `WaitForSingleObject` via `winapi`, not MO2's `waitForApplication` | the native call holds the GIL and freezes the whole embedded interpreter, HTTP server included |
| `winapi._declare()` | 17 `argtypes`/`restype` declarations | without them ctypes treats everything as 32-bit while x64 handles are 8 bytes; `GetProcessId` also lives in `kernel32`, not `user32`, which made `/run` always return `pid: 0` |
| `loadorder.py` file IO | opens with `newline=''` | text mode collapses `CRLF` to `LF`, and the first version of this fix rewrote all 96 lines of the file |
| `plugins.txt` written directly | not only through `setState` | `setState` changes an in-memory list, MO2 writes the file on exit, the game starts from the file — three runs in a row left a plugin disabled while reporting `applied: true` |
| native removal | source ModList model instead of public `IModList.removeMod` | the public method omits profile-index repair and download-uninstalled signals; saving the stale profile produced an error dialog and API stall |
| native install checkpoint | explicitly incomplete model setting before ID readback | addInstalledFile dirties MO2's model without saving meta.ini; QSettings cannot flush it, and final provenance must not be saved before strict verification |
| `isActive()` | a lenient check instead of `is True` | the "Enabled" checkbox in MO2 2.5 is the manager's own switch, not a setting named `enabled`: `pluginSetting` returns `None` for it, so a strict comparison never passed |
| `__init__.py` | `mobase` imported lazily, inside the factory | otherwise `from mo2aibridge import i18n` stops working outside MO2, and every offline check depends on that |
| `runtime.Server` | `allow_reuse_address = False` | `HTTPServer` sets it to 1, and on Windows that allows binding to an already-listening address: a second bridge started silently and received nothing |
| `src/test/` | sits **outside** the package and finds it by `__init__.py` | it must not search by name — the names differ on purpose |
| stuck-launch reset | menu item only, no route | a route would let one request bypass the busy lock |
| `log()` | writes to both MO2's log and its own file | MO2's default log level drops plugin warnings, and the reason for a refusal was lost entirely. Both sinks live in `journal.py`; a sink that fails is announced through the other one, never swallowed |

**Crash attribution is incident-specific.** During an earlier series of four MO2
test crashes, a full dump examined in WinDbg showed a double free inside the bundled
`plugins\diagnose_basic.dll` ("Basic diagnosis plugin") on a Qt thread-pool worker;
neither Bridge nor Python appeared on that faulting stack. Diagnostics run again
on `/refresh`, so preserve the required refresh operations and investigate each
new crash on its own evidence. This historical finding does not exonerate Bridge
from unrelated incidents. In the 2 November 2026 MO2 SE download crash, Windows
identified `QIODevice::isOpen`, but the saved minidump lacked an ExceptionStream:
the caller and cause remain unresolved, and neither Bridge involvement nor
exoneration has been established.

---

## 6. House rules for this code

- **Comments and identifiers are English.** Every user-facing string goes through `i18n.py`
  by key, with translations in the dictionaries there — never inline text in the code.
- **Configurable values go to config**, not to constants. Defaults are built into the program
  and create the config file on first run.
- **Work that can be done once is done once**, not in a hot path.
- **No absolute paths anywhere.** Roots are derived from file locations; MO2's own paths come
  from `IOrganizer`.
- **A tool is universal or it is not a tool**: it must work for any profile, any input, on any
  machine.
- **Tools are fixed freely, extended only by a parameter whose default preserves the old
  behaviour.** Changing the meaning of an existing parameter, its default, or the shape of a
  reply silently breaks other callers. Reply shapes may gain keys; they may not lose or rename
  them.
- **Never edit, rename, hide or delete files inside other people's mods.** The bridge changes
  a setup by enabling, disabling and reordering. `/install` and `/mods/remove` write only
  within the mod folder they were asked about, and deletions go to the Recycle Bin.
- **Install independent mods and dependencies separately, preserving each source identity.**
  A shared asset fixture does not replace actual CBBE/3BA/HIMBO or other third-party
  installations. Genuine generated BodySlide outputs and our compatibility patches have
  separate output/patch mods alongside their original prerequisites. Multiple archives
  belonging to one author's mod do not automatically become independent mods.

---

## 7. How to check your work

```
python src/test/run.py
```

Offline suites run without MO2 — source hygiene, busy logic, strings, the update rules,
the full route contract on a fake `mobase`, the transport on a real loopback socket,
mutation failure/retry checks, patch regressions and HTTP limits. Four optional Qt suites also run without MO2. Four
acceptance
suites need a running manager and are **skipped** with a clear message when there is none; a
skip is not a failure.

Use `python src/test/run.py --offline` for source-only work: it never probes live MO2.
Version 2.3.5 adds native installer/accounting checks over mocked APIs and scratch files.
The native backend is opt-in (`native: true`), new-mod only, and targets MO2 2.5.2.
Its registered companion is created by `createPlugins`; do not restore `createPlugin`, which
MO2 prioritizes over the multiple-plugin factory. The native source archive must remain the
original download, with explicit selected file mappings and hash-pinned provenance. FOMOD
choices and recursive dependency compatibility are the caller's responsibility.

Run them after every step, not at the end. They have already caught defects introduced while
fixing other defects.

**While a game or tool is running under MO2, do not change the setup — read only.** Check this
before each change rather than relying on an earlier check: the user acts between your turns.

---

## 8. Open questions, deliberately unanswered

Do not close these in passing.

| Question | State |
|---|---|
| Graded danger levels | The owner ranked the operations: enable/disable/reorder/rename are cheap and reversible; install is more involved; removal is worse; **launching programs is the most dangerous of all**. Today there is one key for all of them. A graded model has not been agreed |
| Nexus page and release | Name, presentation and the release archive are the owner's call. `src/main/pack.py` builds the archive; nothing is published automatically |

Version 2.3.0: read the reliable-request and profile-capability sections in the plugin README.
Never retry an uncertain launch with a new key. HTTP 202 is pollable; 504 means queued work was cancelled.
