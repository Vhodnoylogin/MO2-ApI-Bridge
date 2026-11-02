# MO2 ApI Bridge

Repository: https://github.com/Vhodnoylogin/MO2-ApI-Bridge
Released source: main. Unfinished work: one active develop branch.
Runtime package: src/main/plugin_mo2aibridge/
Packaging source: src/main/pack.py
Checks and synthetic fixtures: src/test/
Generated outputs: ignored target/ or an explicit external build location.

Read README.md and CLAUDE.md before editing. Only runtime package files ship to MO2;
the installed package directory must be named mo2aibridge. Preserve the required MIT
licence and user documentation. No developer/test bundles in the runtime ZIP.
Use python src/test/run.py --offline for source-only changes; never probe or change
the owner's manager as part of offline checks. Live acceptance needs its isolated
manager, environment ownership and restoration evidence.

Treat Skyrim-Mods as historical provenance. Before committing/pushing, verify git
remote -v and explicitly identify MO2-ApI-Bridge. Preserve release tags and assets.
Never commit credentials, build outputs, external model weights or local caches.
