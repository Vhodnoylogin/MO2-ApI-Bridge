# MO2 ApI Bridge

A local HTTP API inside a running Mod Organizer 2, for scripts and AI agents.

**Development candidate 2.3.6; not released.** The stable release on `main` is
[2.3.5](https://github.com/Vhodnoylogin/MO2-ApI-Bridge/releases/tag/v2.3.5).
This candidate fixes selected extraction of valid archives ending in empty directory
fields and distinguishes historical installation receipts from current native metadata.
RAR4/RAR5 archive handling has dedicated regressions alongside ZIP and 7z.
No new live MO2 acceptance is claimed for this candidate.

[Русская версия](README.ru.md) · [API documentation](src/main/plugin_mo2aibridge/README.md)
· [Checks](src/test/README.md)

The opt-in native installation backend targets MO2 2.5.2. The caller chooses FOMOD
options and verifies recursive requirements/runtime compatibility; the Bridge executes
the explicit selection and verifies original-archive accounting. Independent mods and
their dependencies remain separate installations. Generated outputs use separate mods.

## Source and release layout

```text
src/main/plugin_mo2aibridge/  runtime Python package, locale, user documentation, MIT licence
src/main/pack.py             release packaging source
src/test/                   offline/live test source and synthetic fixtures
target/                     ignored generated output
```

Released source and tests belong on `main`; unfinished work uses the single `develop`
branch. End-user ZIPs contain only the runtime package, required user instructions and
licence. Build instructions, test fixtures and developer tools stay in this repository.
The original `Skyrim-Mods/mo2aibridge` history remains preserved.

## Installation

Extract the release archive into `<MO2>/plugins/`, producing
`<MO2>/plugins/mo2aibridge/__init__.py`. MO2 derives the Python package name from the
installed directory, so it must be `mo2aibridge`. Copying the repository wholesale does
not install the plugin. For development, copy `src/main/plugin_mo2aibridge/` or create a
junction to it at that installed path. Restart MO2 to load changed code.

## Build and verify

From the repository root:

```text
python src/test/run.py --offline
python src/main/pack.py
```

The packer writes `target/release/mo2aibridge-<version>.zip`; an explicit output directory
may be passed as its argument. Its allowlist includes runtime `.py` files, both user
READMEs, `LICENSE` and the complete locale tree. Credentials, tokens, config, logs, tests
and bytecode must not be committed or shipped.

Offline checks never probe the owner's manager. Optional Qt tests require PyQt6. Run
`python src/test/run.py` only when the intended live test manager is available; live
suites may change its disposable fixtures. A skipped suite is not runtime acceptance.
`MO2AIBRIDGE_LANG=ru` selects Russian check output.

Repository: [Vhodnoylogin/MO2-ApI-Bridge](https://github.com/Vhodnoylogin/MO2-ApI-Bridge).
Developed with assistance from [Codex (OpenAI)](https://github.com/codex).
