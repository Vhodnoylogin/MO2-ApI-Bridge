# -*- coding: utf-8 -*-
"""Profile mutations through MO2's own profile manager, with the shared locks."""
import os
import re

from . import i18n, profileui
from .base import Domain, inside


def profile_name(value):
    """A Windows directory component, without silently correcting the caller's name."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or value.endswith('.') or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value in ('.', '..')
            or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I)):
        raise ValueError(i18n.t('err.profileName', name=value))
    return value


class ProfileOps(Domain):
    def __init__(self, ctx, guard):
        super().__init__(ctx, guard)
        self.rename_native = profileui.rename

    def rename(self, body):
        def prepare():
            if not body.get('profile') or not body.get('newName'):
                raise ValueError(i18n.t('err.needProfileRename'))
            profile_name(body['profile'])
            profile_name(body['newName'])

        def work():
            stop = self.guard.refusal('op.profileRename')
            if stop:
                return self._refusal(stop, 'profileRename', 'busy')
            current = self.o.profile().name()
            root = os.path.dirname(os.path.abspath(self.o.profile().absolutePath()))
            names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))
            requested, new = body['profile'], body['newName']
            matches = [n for n in names if n.casefold() == requested.casefold()]
            if len(matches) != 1:
                raise ValueError(i18n.t('err.noSuchProfile', profile=requested))
            name = matches[0]
            src, dst = os.path.join(root, name), os.path.join(root, new)
            if not inside(root, src) or not inside(root, dst):
                raise ValueError(i18n.t('err.profilePath', profile=name))
            card = {'profile': name, 'newName': new, 'current': current,
                    'fromPath': src, 'toPath': dst}
            if name == new:
                card.update(applied=False, reason='unchanged')
                return card
            if name.casefold() == current.casefold():
                card.update(applied=False, reason='activeProfile',
                            why=i18n.t('err.activeProfile', profile=name))
                return card
            if any(n.casefold() == new.casefold() for n in names) or os.path.lexists(dst):
                raise ValueError(i18n.t('err.profileExists', profile=new))
            stop = self.danger(body, 'profileRename')
            if stop:
                stop.update(card)
                return stop
            self.rename_native(name, new, self.timeout('profileRename'),
                               int(self.cfg.get('profileDialogPollMs')))
            if (os.path.lexists(src) or not os.path.isdir(dst)
                    or self.o.profile().name() != current):
                raise RuntimeError(i18n.t('err.profileRenameFailed', profile=name, newName=new))
            card.update(applied=True, how='mo2ProfileDialog',
                        undo={'route': '/profiles/rename',
                              'body': {'profile': new, 'newName': name}})
            return card
        return self.change('profileRename', work, prepare=prepare,
                           timeout=self.timeout('profileRename') + 5)
