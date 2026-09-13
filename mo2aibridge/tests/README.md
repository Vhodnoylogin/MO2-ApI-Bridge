# The checks

*Эта страница на русском: [README.ru.md](README.ru.md).*

```
python run.py
```

All nine suites in a row. The suites that need MO2 are skipped with a plain word when it is not
running — a skip does not count as a failure.

The wording of the checks is localised the same way the plugin's is: the catalogue lives in
`locale\<language>\`, and `MO2AIBRIDGE_LANG=ru` runs the same suites in Russian. English is the
default. There is no English duplicated inside the code as a fallback — a missing key is meant
to be loud, not papered over.

| Suite | Needs MO2 | What it checks |
|---|---|---|
| `test_sources.py` | no | source hygiene: no control characters and no paths to one machine, everything compiles, every module is described in both READMEs, and the release archive really carries the translations |
| `test_busy_logic.py` | no | the busy logic against a stand-in `IOrganizer` |
| `test_i18n.py` | no | that the languages agree on keys and substitutions, that every key is used, and that `t()` keeps `key` positional |
| `test_transport_offline.py` | no | the transport on a real loopback socket: a taken port refuses, a bad request is 400 and a broken bridge is 500, no token means 403, and the port falls silent after a stop |
| `test_contract_offline.py` | no | the contract of all 25 routes against a stand-in `mobase` (`fake_mo2.py`): reply keys, both locks, writing `plugins.txt`, installing into a real temporary folder |
| `test_routes.py` | yes | all 25 routes and both locks |
| `test_plugins_txt.py` | yes | that plugin state reaches `plugins.txt` and survives `/refresh` |
| `test_install_modes.py` | yes | install, merge and replace in a sandbox |
| `test_busy_live.py` | yes | the lock against a real running program |

**The standalone suites and the acceptance suites see different code until MO2 is restarted.**
The first import the package from disk; the second speak HTTP to the plugin that was loaded when
the manager started. After an edit the standalone suites talk about the new version and the
acceptance ones about the old; they agree again only once MO2 has been restarted.

The checks live **outside** the plugin package and are not imported by it: `tests\` is a sibling
of `plugin_mo2aibridge\`, not a part of it. They find the plugin beside them, by the presence of
an `__init__.py`, and not by a coincidence of names — the names are deliberately different.

The name MO2 knows the plugin by, the checks ask the plugin itself — the `PLUGIN_ID` string in
`__init__.py`; the name of the token file comes from it too. Assembling it from the folder name
is not allowed: in the repository the folder is called `plugin_mo2aibridge`, and in MO2's
`plugins\` it is `mo2aibridge`.

There are no paths to one particular machine: the module root is derived from the file's own
location, and the port comes from `MO2AIBRIDGE_PORT` (8930 by default).

`test_busy_live.py` starts a tool through MO2 so that the tool occupies the manager. The name
comes from `MO2AIBRIDGE_TEST_APP` (`TexGen` by default) and must be **registered in MO2**:
`startApplication` takes a name from the list of executables, not a path. The tool is closed
through the same `/window` route the bridge uses for windows in general.

`fake_mo2.py` is a stand-in `mobase` and `IOrganizer` over a real temporary folder with a
profile, a `plugins.txt` and a few mods. It exists so that the contract can be checked without a
running manager and, above all, against the code that is on disk right now.

## What these checks have already caught

**The busy state went blind exactly when it was needed.** The check erased its own launch
bookkeeping when it found no live process of a familiar name in the system: a record with no
process looks like rubbish. But MO2 reports completion itself, and until it has, its lock still
holds — so the record was not rubbish but the only evidence. A real case: a service raised by
another plugin from inside the game outlived the game, MO2 honestly showed "locked", and the
bridge answered `busy: null`.

**The opposite trouble.** If the bookkeeping is simply never erased, a record hangs forever: MO2
reports completion only for what it waited for itself, and a `startApplication` launch from a
plugin is not something it waits for. Found by a live run against a tool — the tool closed, and
the bridge still considered itself busy.

**Plugin state did not reach the file.** `setState` changed the list in memory, MO2 rewrote the
file on exit, the game started from the file, and a `/refresh` between the edit and the exit
read the file back. Three runs in a row the plugin stayed disabled while the reply said
`applied: true`.

**The first version of that fix broke the file format itself.** It read in ordinary text mode,
where Python collapses `CRLF` into `LF` — and the write changed all 96 lines. That is why the
suite carries a separate check of the line endings.

**The paths to 7-Zip never matched.** In `services.py`, instead of `\7` in
`C:\Program Files\7-Zip\7z.exe` there stood BEL characters (0x07): some tool along the way read
`\7` as a character code. Installing worked only because `7z` happens to be on `PATH` on this
machine. Found by reading the code rather than by a check — which is why the standalone suite
now looks for control characters in the sources.

**A busy port was taken silently.** `HTTPServer` declares `allow_reuse_address = 1`, and on
Windows that lets a second bridge bind an address that is already being listened on. It wrote
"listening" into its log and received not one request. The transport suite now takes a port and
demands a refusal.

**A mistake in the request was indistinguishable from a broken bridge.** Both came back as a 500
with a traceback, so a caller could not decide whether to fix the request or retry it. Now a
`ValueError` from validation is a 400 and everything else is a 500 — and the suite holds both
apart.

**A locale folder shipped empty.** When `locale\` became a folder per language, the walk in
`pack.py` stayed one level deep and took zero translations into the archive — the released
plugin would have spoken in bare keys. Invisible until release day, so `test_sources.py` now
asks `pack.py` itself what it is going to take.
