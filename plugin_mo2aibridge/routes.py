# -*- coding: utf-8 -*-
"""The routing layer: which path calls which operation.

It knows path names and nothing else. No mobase, no sockets, no JSON here - only the
mapping tables. Adding a route means adding a line, not touching the transport or the
domain layer.
"""


def build(svc):
    """Build the two tables: reads and writes. The split is not cosmetic - the transport
    uses it to decide whether arguments come from the query string or from the body."""
    get = {
        '/ping': svc.ping,
        '/api': svc.api,
        '/mods': svc.mods,
        '/mod': svc.mod,
        '/analyze': svc.analyze,
        '/profiles': svc.profiles,
        '/plugins': svc.plugins,
        '/vfs': svc.vfs,
        '/origins': svc.origins,
        '/resolve': svc.resolve,
        '/dirs': svc.dirs,
        '/procs': svc.procs_list,
        '/windows': svc.windows,
        # update checks against a live Nexus - a read, so it works while MO2 is busy
        '/updates': svc.updates,
    }
    post = {
        '/refresh': svc.refresh,
        '/install': svc.install,
        '/toggle': svc.toggle,
        '/plugins/state': svc.plugins_state,
        '/plugins/order': svc.plugins_order,
        '/vfsexport': svc.vfsexport,
        '/run': svc.run,
        '/window': svc.window,
        # irreversible: without the key from the documentation these routes do nothing
        '/mods/priority': svc.mods_priority,
        '/mods/rename': svc.mods_rename,
        '/mods/remove': svc.mods_remove,
    }
    return get, post
