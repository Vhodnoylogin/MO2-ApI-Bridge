# -*- coding: utf-8 -*-
"""MO2 ApI Bridge - a local HTTP bridge to a running Mod Organizer 2.

Layers, bottom to top; each knows only the one below it:

    winapi.py    Windows processes and windows   - knows nothing of MO2 or networking
    runtime.py   Qt main thread and HTTP         - knows nothing of what the routes do
    services.py  operations on the setup, facade - knows nothing of HTTP or JSON
      base.py       context and the shared change procedure
      busy.py       whether MO2 is busy
      reading.py    reads of state and of the virtual Data
      install.py    installing a mod
      mods.py       the mod list and the irreversible operations
      loadorder.py  plugin order and plugins.txt
      launch.py     launching programs, and their windows
      updates.py    update checks against a live Nexus
    routes.py    which path maps to what          - knows nothing of mobase or sockets
    plugin.py    the MO2 plugin lifecycle
    config.py    configurable values, across all layers
    i18n.py      strings and translations, across all layers

This file is deliberately empty apart from the factory. `mobase` exists only inside the MO2
process, and importing it at package level would break `from mo2aibridge import i18n`
everywhere else - including the translation checks and the winapi work that run without a
live manager. The import is deferred into the function, so the lower layers stay testable
on their own.

Documentation: README.md and README.ru.md next to this file.
"""

__version__ = '2.1.0'

# The name MO2 knows this plugin by: the folder in its plugins\ is called this, and so is
# the package when imported from there. The repository folder name deliberately differs -
# it is plugin_mo2aibridge, so the module name is not repeated three times over - which is
# why this is a literal here rather than something derived from the file location.
PLUGIN_ID = 'mo2aibridge'


def createPlugin():
    from .plugin import MO2ApIBridge
    return MO2ApIBridge()
