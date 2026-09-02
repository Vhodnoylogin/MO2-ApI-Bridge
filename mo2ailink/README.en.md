# MO2 AI Link Assistant

**A local HTTP bridge to a running Mod Organizer 2 for scripts and AI agents.**

Everything `mobase` offers — the mod list, the merged virtual `Data`, plugin load order, mod
installation, launching tools inside the VFS — is reachable only from inside the MO2 process. This
plugin exposes those calls on `127.0.0.1` so scripts, command lines and AI agents can use them
without clicking through the MO2 window.

Binds to loopback only. Every request needs an `X-Token` header; MO2 generates a fresh token on
each start and writes it to `mo2ailink-token.txt` next to the plugin.

*Документация на русском: [README.md](README.md).*

---

## Two decisions that matter more than the code

**Threads.** `mobase` and Qt live on the main thread. The HTTP server runs on its own, and calling
`IOrganizer` from there crashes MO2 — not immediately, and not where you'd look. Every request is
therefore wrapped into a job, handed to the main thread by signal, and waited on. Slower, but
safe. Side effect: during long operations the MO2 window stops responding. That is not a hang.

**Windows.** The bridge can press buttons in tool dialogs, but never blindly. `/windows` returns
the window title and the captions of **all** its buttons; `/window` then clicks a button **by its
caption**. There is deliberately no "just press OK": DynDOLOD's dialog contains parameter choices,
and a stray click silently ruins the output.

---

## Install

Copy the `mo2ailink` folder into `<MO2>\plugins\` and restart MO2. It appears under
**Settings → Plugins → Tool → MO2 AI Link Assistant** and in the tools menu.

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | on | start the bridge together with MO2 |
| `port` | 8930 | port on `127.0.0.1` |
| `language` | `auto` | message language: `auto`, `ru`, `en` |

The menu entry shows the address and state, and if autostart failed it starts the bridge by hand
and shows the real reason. Diagnostics go to `mo2ailink.log` next to the plugin: load, start, and
a full traceback on any failure.

**External requirements.** MO2 2.5 with Python plugin support — nothing else, with one exception:
the `/install` route needs **7-Zip** installed (`7z.exe` is looked up in `Program Files\7-Zip` and
in `PATH`). Every other route works without it.

---

## Tests

```
python ../tests/run.py
```

Four suites: the busy-state logic runs without MO2, the other three need a running manager and
are skipped with a clear message when it is absent. They live **outside** this package, in
`tests\` next to it, and are never shipped to MO2. Details in `..	ests\README.md`.

---

## Routes

The token is supplied by the wrapper and omitted below for brevity.

### Reads (GET)

| Route | Parameters | Returns |
|---|---|---|
| `/ping` | — | profile, game, MO2 version, paths to `mods`, `overwrite`, `downloads`, and `busy` — whether MO2 is occupied by a running program |
| `/api` | — | what `IOrganizer`, `IModList`, `IPluginList`, `IProfile` actually expose in this MO2 build |
| `/mods` | — | every mod: name, active, essential, priority |
| `/mod` | `name` | full mod card, see below |
| `/analyze` | `name`, `conflicts`, `limit` | one-call analysis: card, files, plugins, who overrides whom |
| `/profiles` | — | all profiles and the active one |
| `/plugins` | — | load order, active state, masters, ESL flag, "no records" flag, source mod |
| `/vfs` | `path`, `filter` | files of the virtual `Data` by mask |
| `/origins` | `path` | every mod providing the file — **the first one wins** |
| `/resolve` | `path` | virtual path → real file on disk |
| `/dirs` | `path` | subdirectories of the virtual `Data` |
| `/procs` | — | what MO2 has launched, including launches from its own window |
| `/windows` | `pid` or `key` | windows of a process and their button captions |

### Writes (POST)

| Route | Body | Effect |
|---|---|---|
| `/refresh` | — | re-read `mods\` and the profile |
| `/install` | `archive`, `name`, `paths` | install a mod without a single dialog |
| `/toggle` | `mod`, `active` | enable or disable a mod |
| `/plugins/state` | `set`, `apply` | enable or disable plugins in bulk |
| `/plugins/order` | `order`, `apply` | set the whole load order |
| `/vfsexport` | `outDir`, `timeout`, `legacyWalk` | dump the entire virtual `Data` to CSV |
| `/run` | `binary`, `args`, `cwd`, `wait` | launch a tool **inside the VFS** |
| `/window` | `hwnd`, `action`, `button` | `close`, or `click` by button caption |
| `/mods/priority` | `mod`, `priority` | **irreversible**, see below |
| `/mods/rename` | `mod`, `newName` | **irreversible** |
| `/mods/remove` | `mod` | **irreversible** |

An unknown path returns 404 together with both tables listed separately — half of all mistakes are
a read sent as a POST, or the other way round.

---

## The mod card — `/mod?name=…`

Version, `nexusId`, a ready-made Nexus URL, MO2's own categories, notes, the archive it was
installed from, the update version the user chose to ignore, endorsement and tracking state, and
the `isSeparator` / `isForeign` / `isBackup` / `isOverwrite` flags.

Three of those cannot be obtained from `meta.ini` at all:

| Field | Why it matters |
|---|---|
| `isSeparator` | otherwise a separator is guessed from the `_separator` folder-name suffix — a convention, not a fact |
| `categories`, `primaryCategory` | MO2 stores its own categories and colours outside `meta.ini` |
| `ignoredVersion`, `endorsed` | which updates the user already declined; without this they get offered again |

The `url` field is built from `nexusId` whenever it is known. `m.url()` cannot be trusted: for some
mods it returns a bare domain with no `/mods/<id>`, producing a link to nowhere. What MO2 said is
kept separately as `urlFromMO2`.

---

## One-call analysis — `/analyze?name=…&conflicts=1&limit=80`

The card, the path, the file count, the plugin list, a sample of names and — most importantly —
**who overrides whom**: in which files the mod wins, in which it loses, with the full provider
list. Unlike a computation over an external index, this is the real order of the active profile.

`conflicts=0` turns off the expensive part: contention is checked by calling `getFileOrigins` on
every file of the mod, which is noticeable on a mod with thousands of files.

---

## Installing a mod without closing MO2

MO2 keeps its lists in memory and rewrites profile files while running, which is why editing the
setup traditionally requires closing it. That limitation goes away entirely.

**The normal path — `POST /install`:**

```json
{"archive": "D:\\...\\downloads\\SomeMod-1.2.7z",
 "name": "Some Mod",
 "paths": ["00 Core", "10 Textures 2K"]}
```

The mod is registered through `createMod`, the archive is extracted by the bridge itself, and
exactly the listed subfolders are copied in; `fomod\` and a root `meta.ini` are skipped. The reply
says how many files were copied and whether a `fomod` folder was skipped.

`installMod` is deliberately not used: it opens MO2's own installer, which **always** asks for a
name, asks "replace or merge" on a name clash, and opens the wizard for an archive containing
`fomod/ModuleConfig.xml`. Those windows cannot be automated — MO2 is Qt and has no native buttons.
For FOMOD the manual path is the only correct one anyway: options are resolved by reading
`ModuleConfig.xml`, not by clicking.

If a mod with that name already exists, the route refuses. There is no merge and no replace: a
modified file goes into a **separate** mod that overrides the original.

**The manual path**, when the files are already laid out:

1. unpack the mod into `mods\<Name>` outside MO2, write `meta.ini`;
2. `POST /refresh` — MO2 picks up the new folder and registers the mod;
3. `POST /toggle` — enable it;
4. `POST /mods/priority` — put it where it belongs.

Verified: a folder created by hand was invisible to `/mods`; after `/refresh` the mod appeared with
priority 1006; `/toggle` enabled it and `modlist.txt` on disk changed from `-` to `+`. Deleting the
folder plus `/refresh` removed the line from the profile.

**Consequence for reads:** while MO2 is running, `modlist.txt` on disk can lag behind memory by a
few seconds. Ask `/mods` for live state, not the file.

---

## Irreversible operations

`/mods/priority`, `/mods/rename` and `/mods/remove` change the setup irreversibly: reordering
changes which files win across the whole build, renaming breaks profile lines that reference a mod
by folder name, and removal deletes the folder **from disk**, not a line from a profile.

They therefore do nothing unless the request body contains:

```json
"iUnderstandTheRisk": "yes-I-read-the-docs-and-accept-irreversible-changes"
```

Without it the route performs **no action** and returns an explanation of what it would have done
plus the path to this file. The point is not secrecy — it is that an agent which stumbles onto
these routes cannot damage anything: the value can only be learned by reading this section, that
is, together with the consequences.

Plugin writes are gentler and need only `"apply": true`, because a load order can be rebuilt by
running LOOT, whereas a deleted mod cannot. `/plugins/order` additionally demands the **full**
list and refuses to work if anything is missing or unknown.

---

## While the game is running

As long as a program is running through MO2 — the game, DynDOLOD, TexGen, anything — the manager
is occupied by it and **no write route does anything**. The refusal looks like the irreversible
lock: `applied: false`, plus `busy: true` and a card describing the running process.

```json
{"applied": false, "busy": true,
 "running": {"app": "SkyrimVR.exe", "pids": [23456], "isGame": true, "viaMO2": true},
 "blocked": "enabling or disabling a mod",
 "why": "SkyrimVR.exe is running and MO2 is busy with it. ..."}
```

The reason is not politeness towards the MO2 window, it is how USVFS works. The virtual `Data` is
already mounted into the game's process. Reordering a mod, enabling a plugin or refreshing the
lists on the fly splits the world in two: the game keeps seeing the old setup while the files on
disk describe a new one. That surfaces later and elsewhere — saves referencing plugins in the old
order, and a `refresh` under a live USVFS can take MO2 down with it.

Refused: `/refresh`, `/install`, `/toggle`, `/run`, `/plugins/state` and `/plugins/order` **with**
`apply`, and the whole irreversible trio `/mods/priority`, `/mods/rename`, `/mods/remove`.

Still working: **every** read route — `/mods`, `/plugins`, `/vfs`, `/origins`, `/resolve`,
`/analyze` and the rest — plus `/plugins/state` and `/plugins/order` previews without `apply`,
which change nothing. The setup can be analysed in full during play; it just cannot be edited.
`/windows` and `/window` keep working too: they close the running tool's own dialog, which is
exactly what ends the busy state.

Verified against a live MO2 twice. With a tool: `/mods` returned the full list and all nine
write routes refused. With the real game, launched from MO2's own window — six minutes of play
under USVFS, and reads answered **instantly** the whole time: profiles, 97 plugins, virtual `Data`
listings, file providers. MO2's lock dialog does not stop its event loop, so jobs posted from the
server thread keep being executed.

**The game is caught by process enumeration, not by the callback, and that is a necessity rather
than a backup.** MO2 starts Skyrim VR through `sksevr_loader.exe`; the loader spawns
`SkyrimVR.exe` and exits immediately. The name reported by `onAboutToRun` no longer refers to the
living process — the game binary is what must be looked for. Hence `viaMO2` answers "does MO2
consider a run active", while `mo2Run` shows what it actually started.

**How it is detected.** Two ways at once, because either alone lies:

| Method | Sees | Misses |
|---|---|---|
| `onAboutToRun` and `onFinishedRun` | everything MO2 launches — through the bridge and through its own window | whatever its children started, rather than MO2 itself |
| enumerating Windows processes | whether the game is alive, and anything MO2 named | a process MO2 never reported |
| whether MO2's main window is enabled | its own lock, exactly as a person sees it | nothing, until the window is disabled |

Busy is declared if **any** of the three fires. A game started outside MO2 — straight from Steam
— counts as busy too: changing the setup under a running Skyrim is wrong no matter who started
it.

**The launch bookkeeping is never "healed" by a process check, and that is essential.** The
temptation is real: a record remains, no process by that name exists, it looks like garbage. But
MO2 reports the finish itself, and until it does, its lock is still held. So the record is not
garbage — it is the only evidence left.

That is exactly what happened. The `Envoy` plugin started a voice service, `pythonw.exe`, from
inside the game; the game ended, the service stayed alive in MO2's job, and the manager honestly
showed "locked while an application is running". The bridge, having "healed" its bookkeeping,
answered `busy: null` — the protection switched itself off in the very case it was written for.
That state is now visible as `heldByUnknown`: the run is not closed, yet no familiar process is
alive, so the lock is held by something MO2 never mentioned.

**Own launches and foreign ones are tracked differently, because MO2 does not report every
finish.** It reports only what it waited for — that is, a launch from its own window. A program
started by the bridge through `startApplication` is not waited for, and `onFinishedRun` never
arrives for it: the record would hang forever and the bridge would consider itself busy until the
manager restarts. So the bridge clears its own launches as soon as the process dies — about those
it knows the truth without MO2. Foreign ones are held until MO2 says otherwise: that is where the
whole protection lives.

The opposite risk is a stuck record, if the finish notification is lost entirely. There is
deliberately no route to reset it: that would put the lock one request away. Only a person can
clear it, by hand: **Tools → MO2 AI Link Assistant** shows what MO2 still considers active and
offers a reset.

There is deliberately no override here, unlike the irreversible lock. There a key confirms a
deliberate choice; here there is nothing to choose — closing the program is the only way.

---

## VFS export format

`vfs-<profile>.csv`, `;` separated:

| Column | Meaning |
|---|---|
| `rel` | path relative to `Data` |
| `winner` | the mod whose file the game actually sees |
| `archive` | BSA name, if the file came from an archive |
| `providers` | every provider separated by `\|`, the first one wins |

---

## Things learned the hard way

**`/run` takes a registered executable name, not a path.** `startApplication` with a full path to
an `.exe` silently fails to create a process: RootBuilder runs, no window appears. With a name from
MO2's executables list (`TexGen`, `DynDOLOD`, `PGPatcher`) it works immediately.

**`waitForApplication` stops the bridge, not just MO2.** The call goes into C++ and never
releases the GIL, and plugins share a single interpreter — so the HTTP server thread freezes along
with the main thread. Observed literally: while a launched tool was running the bridge did not
answer even `/ping`, and came back the instant the tool closed. `/run` with `"wait": true`
therefore waits on its own, via `WaitForSingleObject` on the process handle: ctypes releases the
GIL for the duration, so only that one request's thread is occupied.

**Reading MO2's own windows is pointless.** MO2 is a Qt application; its widgets are drawn inside a
single `HWND` and it has no native child windows. An empty button list on a `Qt*` window means "not
visible to this API", not "no buttons". The mechanism works for WinAPI tools such as the DynDOLOD
and TexGen dialogs, and not for MO2 itself. Such windows are flagged with `qtWindow: true`.

**`/plugins/state` writes `plugins.txt` itself, and that is not a nicety.** `setState` changes
the list in MO2's memory, while the file is rewritten at moments of its own choosing — usually on
exit. The game starts from the file, and any `/refresh` between the edit and the exit reads the
file back, silently undoing the change. That cost three ruined runs: the bridge answered
`applied: true` while the plugin stayed disabled in game. The asterisk is now flipped inside the
existing lines of the file; the set of lines and their order are not touched at all — those belong
to MO2. The reply carries a `file` field with the path and the names actually changed.

The file must be read with `newline=''`: in ordinary text mode Python collapses `CRLF` into `LF`,
the end-of-line detection then yields `LF`, and the file gets rewritten in a foreign format. Caught
exactly that way — the very first write changed all 96 lines.

**`/vfsexport` takes the tree from MO2 itself.** `IOrganizer.virtualFileTree()` returns the merged
`Data`. The older approach — seed the root from individual mod trees, then walk breadth-first with
`listDirectories` — remains as a fallback (`"legacyWalk": true`) and only existed because
`listDirectories('')` and `('.')` return nothing, making the root look unreachable. The `how` field
in the response says which method produced the dump: the two give different path counts.

**The `archive` column is always empty** while MO2's archive parsing is disabled
(`archive_parsing_experimental=false`, the default). BSA contents do not enter the virtual tree at
all in that case.

**Answers depend on the active profile.** `/origins` for a file from a category absent from the
current profile returns nothing. That is the truth about the profile, not missing data.

---

## Translating

All user-facing strings live in `i18n.py`, separated from the logic. To add a language: copy the
`EN` dictionary, translate the right-hand sides, leave the keys untouched, register it in `LANGS`,
and select it with the `language` setting. Keys are short latin labels rather than English
sentences — a sentence changes during proofreading and would detach every translation at once.

A missing key never breaks anything: English is used, and if that is missing too, the key itself.

---

## Layout

| File | Layer | Knows about |
|---|---|---|
| `winapi.py` | operating system | windows and buttons; nothing about MO2 or the network |
| `runtime.py` | transport | the Qt main thread, sockets, JSON, the token |
| `services.py` | domain | `mobase` and the mod setup; nothing about HTTP |
| `routes.py` | routing | which path maps to which operation, and nothing else |
| `plugin.py` | lifecycle | MO2's plugin interface |
| `i18n.py` | strings | across all layers |

`__init__.py` deliberately holds only the factory, and imports `mobase` lazily: otherwise
`from mo2ailink import i18n` would require a running MO2, and the lower layers could not be checked
on their own.

---

## Security

The server binds to `127.0.0.1` and never listens on an external address. The token is random,
32 characters, regenerated on every MO2 start — an intercepted token outlives nothing but the
session. It is compared in constant time so it cannot be guessed from response timing.

The bridge grants full control over the mod setup: enabling and disabling mods, changing load
order, launching programs. Those are the same rights any program started by the user already has,
but reachable over a loopback socket. Keep it enabled while something is using it.
