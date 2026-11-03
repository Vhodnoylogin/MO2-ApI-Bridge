# The checks

*Эта страница на русском: [README.ru.md](README.ru.md).*

```
python run.py
```

All registered suites in a row. The suites that need MO2 are skipped with a plain word when it is not
running — a skip does not count as a failure.

Use `python run.py --offline` to skip live suites without even probing a running manager.

The wording of the checks is localised the same way the plugin's is: the catalogue lives in
`locale\<language>\`, and `MO2AIBRIDGE_LANG=ru` runs the same suites in Russian. English is the
default. There is no English duplicated inside the code as a fallback — a missing key is meant
to be loud, not papered over.

| Suite | Needs MO2 | What it checks |
|---|---|---|
| `test_sources.py` | no | source hygiene: no control characters and no paths to one machine, everything compiles, every module is described in both READMEs, and the release archive really carries the translations |
| `test_busy_logic.py` | no | the busy logic against a stand-in `IOrganizer` |
| `test_archive_resources.py` | no | scratch cleanup after timeout/spawn/list allocation failure; full/selected guards and shared mapping contract |
| `test_plugin_state_bytes.py` | no | exact BOM/codec/EOL preservation and recovery bytes while changing only activation markers |
| `test_launch_validation.py` | no | malformed/nonfinite/oversized waits refused before process creation |
| `test_update_targets.py` | no | pure update verdicts, game-scoped identities, absent downloads and per-sweep page reuse |
| `test_busy_state.py` | no | deterministic concurrent callbacks, stale observations, mixed launch ownership and detached snapshots |
| `test_i18n.py` | no | that the languages agree on keys and substitutions, that every key is used, and that `t()` keeps `key` positional |
| `test_policy.py` | no | the update rules against a table of made-up Nexus pages: the verdict and the reason for each, and that the rules import nothing of the network, MO2 or the disk |
| `test_transport_offline.py` | no | the transport on a real loopback socket: a taken port refuses, a bad request is 400 and a broken bridge is 500, no token means 403, and the port falls silent after a stop |
| `test_contract_offline.py` | no | the contract of all 32 routes against a stand-in `mobase` (`fake_mo2.py`): reply keys, both locks, writing `plugins.txt`, installing into a real temporary folder |
| `test_mutation_failures.py` | no | Failed persistence, retry repair and backup preservation; partial copy, unreadable selections and uncertain inventories; dispatch-time busy checks |
| `test_order_persistence.py` | no; some checks need PyQt6 | native-shaped disk/order persistence, primary omissions, disabled-plugin priorities, partial writes/recovery, identity/busy guards and real Qt asynchronous refresh completion |
| `test_native_adapter.py` | no; some checks need PyQt6 | one-use native installer selection, original native tree identity, cancellation and exact Downloads source-row refresh/readback with mocked MO2 APIs |
| `test_install_accounting.py` | no; metadata checks need PyQt6 | archive/hash/selection identity, exact file mapping and payload verification, native metadata/provenance readback, incomplete accounting and foreign-target rollback refusals over scratch files |
| `test_mod_removal.py` | no | pending-profile preservation, asynchronous refresh, stale setup, persistence failures, archive retention and same-operation replay |
| `test_modui.py` | no; needs PyQt6 | real nested Qt confirmations, canonical model-row binding, native false/cancel/failure, stale identities, unsupported controls and timer cleanup |
| `test_routes.py` | yes | all 32 routes and both locks |
| `test_plugins_txt.py` | yes | that plugin state reaches `plugins.txt` and survives `/refresh` |
| `test_install_modes.py` | yes | install, merge and replace in a sandbox |
| `test_busy_live.py` | yes | the lock against a real running program |

**The standalone suites and the acceptance suites see different code until MO2 is restarted.**
The first import the package from disk; the second speak HTTP to the plugin that was loaded when
the manager started. After an edit the standalone suites talk about the new version and the
acceptance ones about the old; they agree again only once MO2 has been restarted.

The checks live **outside** the plugin package and are not imported by it: `src/test/` sits outside
`src/main/plugin_mo2aibridge/`. They find the plugin beside them, by the presence of
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

An optional native Qt regression suite is `python test_profileui.py`. It requires standalone PyQt6, uses offscreen dialogs and temporary profiles, and never accesses installed MO2. It verifies nested dialog completion, cancellation, timeouts, missing controls, and preservation of unrelated modal windows.

Standalone PyQt6 protocol regression checks: `python src/test/test_protocol.py`.
Covers expired queue cancellation, started pending work, HTTP retries, boot/profile/instance identity,
clone/select readback, shutdown and Windows process identity. No installed MO2 or game is accessed.

For a separately installed package, set `MO2AIBRIDGE_TOKEN_FILE` to its token file path.

`python src/test/test_health_logging.py` requires standalone PyQt6. It checks a cold
health request with a deliberately undrained Qt queue, cancellation without a
late cache lookup, cached busy evidence, ordinary write-guard lookup, and actual
Qt logging round trips for Cyrillic, other Unicode and literal percent signs.
It uses disposable fake organizer data, not installed MO2.


Release2.3.4: run.py also invokes test_patch234.py (32 failure/identity checks),
test_http_limits.py (real bounded loopback inputs), test_lifecycle.py (real Qt with
fake native lifecycle), and the existing profileui/protocol/health suites.
The four suites requiring PyQt6 skip explicitly when it is unavailable; release
validation runs them using a Qt-enabled interpreter. No live manager is accessed
by those suites. Live acceptance remains separate and requires an isolated instance.

## Installation regressions in candidate 2.3.6

`test_archive_listing.py` preserves final empty 7-Zip fields across LF/CRLF and
refuses malformed/unsafe/encrypted/duplicate records before extraction side effects.
`test_provenance.py` checks historical raw receipt retention, stale/current/unknown
native metadata, bounded unavailable evidence and the absence of hash certification.
Native adapter/accounting tests cover one source at two unique destinations. These
use synthetic scratch fixtures and are not live MO2 acceptance.

RAR4/RAR5 checks in test_rar_archives.py generate self-authored STORE fixtures, verify them with real 7-Zip, and cover full/selected extraction, source reuse, closed stdin and volume refusal. Compressed author archives are external evidence; fixtures contain no third-party payload.

## Candidate 2.3.7 lifecycle regressions

`test_operation_lifecycle.py` uses real Qt with a fake host: failed scheduling,
immediate pending replay, mutation exclusion, nested-event lease retention,
post-stop reconciliation/draining, retained old-boot outcomes, bounded redacted
stages and process-handle retirement. `test_update_targets.py` adds exact callback
identity, late/tokenless/foreign replies, closed lifetimes, monotonic concurrent
pacing and a real Qt queue-stall counterexample. `test_snapshot_contract.py`
checks supplied target guards and truthful observed divergence. Removal tests
retain selected-install evidence and ensure malformed native receipts cannot
make a destructive outcome unencodable. No installed manager or Nexus is accessed.

## Foundation selection and current-state verification

`test_archive_resources.py` covers synthetic1833-member FOMOD selections, original
member names, excluded alternatives, Windows/archive link/collision guards,
indexed path-free mapping refusals and actual selected extraction with7-Zip.
`test_install_accounting.py` retains strict checkpoint/provenance/Installed ordering
and adds nestedFOMOD native-adapter inputs plus safe inventory/hash failure stages.

`test_installation_audit.py` exercises advisory preview and independent verification
over disposable fake-host files and real QSettings/7-Zip/Qt when available. A separate
passive DownloadList fixture checks exact path identity without refresh/filter changes.
The verifier preserves original failed outcomes and missing provenance, rejects
extra/corrupt payload, wrong/missing IDs, changed context/archive and active Bridge
mutations. Neither these fixtures nor successful real archive extraction constitute
native MO2 installation acceptance.

## Client master checks

`examples/master_checks.py` separates unique case-insensitive master membership,
actual-file active dependency edges and expected full plugin priority order,
including disabled plugins. Raw API/file sequences remain unchanged. It performs
no HTTP, filesystem or manager operations and does not verify VFS bytes or runtime
compatibility. `test_master_examples.py` covers order-only differences, duplicates,
real missing/extra names, missing/inactive/late masters, disabled dependents,
disabled full-order positions and ambiguous snapshots over synthetic inputs.
