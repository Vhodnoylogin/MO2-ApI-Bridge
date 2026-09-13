# MO2 ApI Bridge

A local HTTP bridge to a running Mod Organizer 2, for scripts and AI agents.

*Эта страница на русском: [README.ru.md](README.ru.md). Подробная документация плагина —
[plugin_mo2aibridge/README.ru.md](plugin_mo2aibridge/README.ru.md).*

**Looking for the detailed documentation?** It is one level down, inside the plugin package:
[plugin_mo2aibridge/README.md](plugin_mo2aibridge/README.md) — every route, both locks, the
reply contract, how updates are decided, and why each awkward-looking decision is there. This
page answers only the questions of the module level: what the folders are, how to install, how
to build the release archive, how to run the checks.

This is the **module folder**, not the plugin. The plugin is the nested folder
`plugin_mo2aibridge\`. The split is not cosmetic: what travels into MO2 is exactly what MO2
needs and not one line of the checks. The module's checks live here, beside it — in the
repository root there would be no telling what they check.

**The three levels of the layout are deliberately named differently**, so that they do not read
as one nesting:

| Name | What it is | Where it exists |
|---|---|---|
| `wt-mo2aibridge\` | the branch's copy on disk | on disk only; git does not know this name |
| `mo2aibridge\` | the module folder | in the branch |
| `plugin_mo2aibridge\` | the plugin's own files | in the module |

The name MO2 knows the plugin by is `mo2aibridge`, and it is set as a **string** in `PLUGIN_ID`
in `__init__.py` rather than derived from the folder name. The names of the token file and of
the log come from it too.

```
mo2aibridge\                the module folder
├── README.md              this file
├── README.ru.md           this file in Russian
├── CLAUDE.md              the manifest for an AI assistant that gets this folder
├── pack.py                builds the release archive
├── plugin_mo2aibridge\     THE PLUGIN - only this reaches MO2
│   ├── __init__.py       the factory; mobase is imported lazily
│   ├── plugin.py         the MO2 plugin life cycle
│   ├── routes.py         the "path -> operation" table
│   ├── services.py       the facade of the domain layer: the same 25 methods
│   ├── base.py           the domain context and the shared recipe for a change
│   ├── busy.py           whether MO2 is busy
│   ├── reading.py        reading state and the virtual Data
│   ├── install.py        installing a mod
│   ├── mods.py           the mod list and the irreversible operations
│   ├── loadorder.py      plugin order and plugins.txt
│   ├── launch.py         starting programs and driving windows
│   ├── updates.py        updates against the live Nexus
│   ├── runtime.py        the Qt main thread, sockets, JSON, the token
│   ├── winapi.py         Windows windows and processes
│   ├── config.py         the configurable values
│   ├── i18n.py           the catalogue loader
│   ├── locale\en\*.json  every string the plugin can say, English
│   ├── locale\ru\*.json  the same, Russian
│   ├── README.md         the plugin documentation, en
│   ├── README.ru.md      the plugin documentation, ru
│   └── LICENSE           MIT
└── tests\                the module's CHECKS - these never reach MO2
    ├── common.py         where the plugin is, how to reach it, how to report
    ├── run.py            run every suite in a row
    ├── fake_mo2.py       a stand-in mobase and IOrganizer over a temporary folder
    ├── locale\en\*.json  the wording of the checks, English
    ├── locale\ru\*.json  the same, Russian
    ├── test_sources.py            no MO2: source hygiene
    ├── test_busy_logic.py         no MO2: the busy logic
    ├── test_i18n.py               no MO2: the catalogues agree and every key is used
    ├── test_transport_offline.py  no MO2: the socket, the token, the reply codes
    ├── test_contract_offline.py   no MO2: all 25 routes against a stand-in mobase
    ├── test_routes.py             acceptance: all 25 routes
    ├── test_plugins_txt.py        acceptance: plugin state reaches the file
    ├── test_install_modes.py      acceptance: install, merge and replace in a sandbox
    ├── test_busy_live.py          acceptance: the lock against a running program
    ├── README.md         what each suite checks and what it has already caught
    └── README.ru.md      the same in Russian
```

## Installing

MO2 loads a plugin from `<MO2>\plugins\<folder with __init__.py>` and **takes the package name
from that folder's name**. So the folder in `plugins\` must be called `mo2aibridge`, even though
in the repository it is called `plugin_mo2aibridge`:

```
xcopy /E /I plugin_mo2aibridge "<MO2>\plugins\mo2aibridge"
```

For development a junction is more convenient: edits are visible without copying, and the name
of the junction is what gives MO2 the package name it needs.

```
mklink /J "<MO2>\plugins\mo2aibridge" "<module folder>\plugin_mo2aibridge"
```

**For a release this means one thing:** inside the archive the folder must be called
`mo2aibridge`, not what it is called in the repository. Copying the repository wholesale does
not give a working plugin — Python imports a package by its folder name, and
`plugin_mo2aibridge` would yield a package of that same name, while MO2 looks for
`mo2aibridge`.

## Building the release archive

```
python pack.py
```

Writes `dist\mo2aibridge-<version>.zip` with a `mo2aibridge\` folder inside. **The archive must
not be built with the `xcopy` above**, and this is not pedantry: beside the code live the token
file, the log and the settings the plugin creates on every start, plus a `__pycache__` from
every import. Recursive copying does not tell them apart, and into the archive would go somebody
else's token, somebody else's log full of the author's machine paths, and frozen settings —
including paths to 7-Zip that another person may not have. `.gitignore` is no protection here:
it governs git, not copying.

So `pack.py` lists what **ships**, not what is excluded: `*.py`, both READMEs, the `locale\`
tree and `LICENSE`. A forgotten file then means "did not make it into the archive" rather than
"somebody else's file did" — the first mistake costs a bug report, the second a leak. What was
taken and what was left behind the script prints as a list.

The plugin's own documentation is `plugin_mo2aibridge\README.md` and `README.ru.md`. It lives
inside the package deliberately: the lock on irreversible operations answers with the path to
that file, so the file has to arrive together with the plugin.

## Checks

```
python tests/run.py
```

Five standalone suites run without MO2 — source hygiene, the busy logic, the string catalogues,
the transport, and the contract of every route against a stand-in `mobase`. Four acceptance
suites need a running manager and are skipped with a plain word when there is none; a skip does
not count as a failure. The details are in [tests/README.md](tests/README.md).

The wording of the checks is localised as well. `MO2AIBRIDGE_LANG=ru` runs the same suites in
Russian; the default is English.

The checks are not imported by the plugin and are invisible to MO2: they live outside the
package and find it themselves — by the neighbouring folder holding an `__init__.py`, not by a
coincidence of names.

## What is not versioned

The token and the log are created beside the plugin on every MO2 start, the settings file
`mo2aibridge-config.json` on the first one, and `__pycache__` on every import. All of it is in
`.gitignore`: the token and the log in the shared one, the settings in the `.gitignore` inside
the package.
