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
        self.clone_native = profileui.clone
        self.select_native = profileui.select

    def capabilities(self, q=None):
        return {'profileRename': 'nativeQt', 'profileClone': 'nativeQt',
                'profileSelect': 'nativeQt', 'localSaves': 'unavailable',
                'localSettings': 'unavailable', 'rootBuilder': 'unavailable',
                'why': i18n.t('err.profileCapabilities')}

    def _target(self, value):
        profile_name(value)
        root = self.ctx.profiles_root
        matches = [n for n in os.listdir(root) if n.casefold() == value.casefold()
                   and os.path.isdir(os.path.join(root, n))]
        if len(matches) != 1:
            raise ValueError(i18n.t('err.noSuchProfile', profile=value))
        path = os.path.join(root, matches[0])
        if not inside(root, path):
            raise ValueError(i18n.t('err.profilePath', profile=value))
        return matches[0], path

    def select(self, body):
        def prepare():
            profile_name(body.get('profile'))
        def work():
            stop = self.guard.refusal('op.profileSelect')
            if stop:
                return self._refusal(stop, 'profileSelect', 'busy')
            self.ctx.expected(body)
            name, path = self._target(body['profile'])
            previous = self.o.profile().name()
            if name == previous:
                return {'applied': False, 'reason': 'unchanged', 'current': previous}
            stop = self.danger(body, 'profileSelect')
            if stop:
                return stop
            self.select_native(name, self.timeout('profileRename'),
                               int(self.cfg.get('profileDialogPollMs')))
            actual = self.o.profile().name()
            if actual != name:
                raise RuntimeError(i18n.t('err.profileSelectFailed', profile=name))
            return {'applied': True, 'before': previous, 'current': actual, 'path': path,
                    'undo': {'route': '/profiles/select', 'body': {'profile': previous}}}
        return self.change('profileSelect', work, prepare=prepare,
                           timeout=self.timeout('profileRename') + 5)

    def clone(self, body):
        def prepare():
            profile_name(body.get('profile'))
            profile_name(body.get('newName'))
        def work():
            stop = self.guard.refusal('op.profileClone')
            if stop:
                return self._refusal(stop, 'profileClone', 'busy')
            self.ctx.expected(body)
            name, source = self._target(body['profile'])
            new = body['newName']
            destination = os.path.join(self.ctx.profiles_root, new)
            if (not inside(self.ctx.profiles_root, destination)
                    or any(n.casefold() == new.casefold() for n in os.listdir(self.ctx.profiles_root))):
                raise ValueError(i18n.t('err.profileExists', profile=new))
            current = self.o.profile().name()
            stop = self.danger(body, 'profileClone')
            if stop:
                return stop
            self.clone_native(name, new, self.timeout('profileRename'),
                              int(self.cfg.get('profileDialogPollMs')))
            if not os.path.isdir(destination) or self.o.profile().name() != current:
                raise RuntimeError(i18n.t('err.profileRenameFailed', profile=name, newName=new))
            return {'applied': True, 'profile': name, 'newName': new, 'current': current,
                    'fromPath': source, 'toPath': destination, 'created': True,
                    'reversal': {'createdProfile': new, 'createdPath': destination,
                                 'sourceProfile': name, 'sourcePath': source}}
        return self.change('profileClone', work, prepare=prepare,
                           timeout=self.timeout('profileRename') + 5)

    def local_flags(self, body):
        # Disabling these via MO2's remembered QuestionBox may delete data before
        # a caller can choose "No". No stable public setter is exposed by mobase.
        def work():
            self.ctx.expected(body)
            name, _ = self._target(body.get('profile'))
            return {'applied': False, 'reason': 'unsupported', 'profile': name,
                    'why': i18n.t('err.profileCapabilities')}
        return self.change('profileLocalFlags', work)

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
            self.ctx.expected(body)
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
