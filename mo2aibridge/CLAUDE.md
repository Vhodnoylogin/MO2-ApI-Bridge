# MO2 ApI Bridge — manifest for an AI assistant

You are looking at a self-contained module. This file exists so that any chat handed this
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

- **The bridge only executes. The caller remembers.** It keeps no state between calls except
  its record of running programs.
- **The bridge decides nothing** for the caller: it does not guess a "correct" priority, does
  not pick between options, does not roll anything back on its own.
- **The bridge delegates nothing back.** If an operation needs an archive unpacked, files
  copied, a folder removed — it does that itself, rather than answering "do this by hand".
- **Every reply to a change must contain everything needed to reverse it**: what was there
  before, what is there now, where the files went. This is not a convenience; it is the only
  safety net the caller has.

---

## 2. Layout

```
mo2aibridge/                  the module (this folder)
├── CLAUDE.md                 this file
├── README.md                 module readme: layout, install, packaging
├── pack.py                   builds the release archive
├── plugin_mo2aibridge/       THE PLUGIN — only this ships to MO2
│   ├── __init__.py           factory, PLUGIN_ID, __version__
│   ├── plugin.py             MO2 plugin lifecycle
│   ├── routes.py             path -> operation table
│   ├── services.py           facade: assembles the domain classes
│   ├── base.py               shared base for domain classes
│   ├── busy.py               the "MO2 is busy" lock
│   ├── reading.py            reads of the setup state
│   ├── mods.py               mod list: toggle, priority, rename, remove
│   ├── loadorder.py          plugin states and load order, plugins.txt writes
│   ├── install.py            install from archive, merge and replace modes
│   ├── launch.py             launching programs through MO2, window actions
│   ├── updates.py            update checking against the Nexus API
│   ├── runtime.py            Qt main thread, HTTP server, token
│   ├── winapi.py             windows, processes, Recycle Bin, credential store
│   ├── config.py             settings from JSON, defaults built in
│   ├── i18n.py               user-facing strings, RU and EN
│   ├── README.md             plugin documentation, English
│   ├── README.ru.md          plugin documentation, Russian
│   └── LICENSE               MIT
└── tests/                    CHECKS — never ship to MO2
```

**Three names, three levels, deliberately different** so they do not read as one nesting:

| Name | What it is | Where it exists |
|---|---|---|
| `wt-mo2aibridge/` | a branch checked out on disk | disk only; git does not know this name |
| `mo2aibridge/` | the module folder | in the branch |
| `plugin_mo2aibridge/` | the plugin package | in the module |

MO2 takes the **package name from the folder name** in its `plugins\`, so the folder there
must be called `mo2aibridge` even though the repository calls it `plugin_mo2aibridge`. In
development this is a junction; for release, `pack.py` puts the right name in the archive.

`PLUGIN_ID` is a **string** in `__init__.py`, not derived from the folder. The token, log and
config filenames come from it.

---

## 3. Layers

Each layer knows only the one below it. Keep it that way.

| File | Knows about | Knows nothing about |
|---|---|---|
| `winapi.py` | Windows processes and windows | MO2, networking |
| `runtime.py` | threads, sockets, JSON, the token | what the routes do |
| `services.py` and the domain files | `mobase` and the setup | HTTP, JSON, tokens |
| `routes.py` | path names | `mobase`, sockets |
| `plugin.py` | the MO2 plugin lifecycle | it does nothing by hand, it only wires things |
| `i18n.py` | strings | across all layers |

Adding a route means adding a line to `routes.py`. Method names on the facade are a contract:
`routes.py` and the tests both refer to them.

---

## 4. The two locks

**Busy lock.** While a game or tool runs under MO2, the virtual `Data` is mounted into another
process. Changing mods, order or plugins then means the running program sees one setup while
the files on disk describe another. So all nine write routes refuse, and every read keeps
working. Busy is decided by three independent sources, and that is not redundancy:

1. MO2's own callbacks (`onAboutToRun` / `onFinishedRun`) — they know about launches through
   the manager but do not survive a missed `onFinishedRun`.
2. Process enumeration — it knows the truth about live programs but cannot tell a launch
   through MO2 from one beside it. **Only this source catches the game**: MO2 starts it via
   `sksevr_loader.exe`, which exits immediately, leaving `SkyrimVR.exe` under a different name.
3. Whether MO2's main window is enabled — the closest thing to what a person sees.

**Irreversible lock.** Priority, rename, remove, `/run`, and `/install` with `mode=replace`
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
| `isActive()` | a lenient check instead of `is True` | the "Enabled" checkbox in MO2 2.5 is the manager's own switch, not a setting named `enabled`: `pluginSetting` returns `None` for it, so a strict comparison never passed |
| `__init__.py` | `mobase` imported lazily, inside the factory | otherwise `from mo2aibridge import i18n` stops working outside MO2, and every offline check depends on that |
| `runtime.Server` | `allow_reuse_address = False` | `HTTPServer` sets it to 1, and on Windows that allows binding to an already-listening address: a second bridge started silently and received nothing |
| `tests/` | sits **outside** the package and finds it by `__init__.py` | it must not search by name — the names differ on purpose |
| stuck-launch reset | menu item only, no route | a route would let one request bypass the busy lock |
| `log()` | writes to both MO2's log and its own file | MO2's default log level drops plugin warnings, and the reason for a refusal was lost entirely |

**MO2 crashes are not this plugin's fault.** Heavy test runs crashed MO2 four times. A full dump
under WinDbg showed a double free inside MO2's own bundled `plugins\diagnose_basic.dll` (the
"Basic diagnosis plugin"), on a Qt thread-pool worker. Neither the bridge nor Python appears on
the faulting stack. The link is indirect: diagnostics re-run on every `/refresh`, and a full test
run triggers seven or eight in a row. **Do not hunt for the cause in the bridge and do not remove
`refresh` calls because of it** — they are there for a reason.

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

---

## 7. How to check your work

```
python tests/run.py
```

Five offline suites run without MO2 — source hygiene, busy logic, strings, the full route
contract on a fake `mobase`, and the transport on a real loopback socket. Four acceptance
suites need a running manager and are **skipped** with a clear message when there is none; a
skip is not a failure.

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
| Nexus page and release | Name, presentation and the release archive are the owner's call. `pack.py` builds the archive; nothing is published automatically |
