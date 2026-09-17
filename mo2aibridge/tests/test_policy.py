# -*- coding: utf-8 -*-
"""The update rules on their own, without MO2 and without Nexus.

Why this suite exists at all. The decision used to sit in `updates.py` beside the HTTP
client, the API key and the daily allowance, and the only way to reach it was through a
request: the one genuinely testable thing in that file was the one thing nothing could
reach. Now it is `updatepolicy.py`, a pure function over a made-up page, and a page is a
few dictionaries - so every rule can be stated as a row in a table and asserted.

What is checked here:

1. That the policy module really is free of transport. It is checked by reading its imports,
   not by trusting the layout: one `import urllib` and the rules go back behind the network.
2. The verdict AND the reason for a table of pages. The reason matters as much as the
   verdict - it is the line a person reads - and it travels as an i18n key, so it can be
   compared word for word without pulling in a language.
3. The helpers the verdicts stand on: the role of a file name, the moment inside an archive
   name, the date in words.

Every page below is invented. Nothing here reads the setup, the downloads folder or the
network, so the suite says the same thing on any machine.
"""
import ast
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

policy = common.import_package().updatepolicy
r = common.Report(T('policy.title'))
D = policy.DAY


def F(name, fid, cat, t, ver='', fn=None):
    """One row of a Nexus page: what the file is called, its id, its section, when it was
    uploaded. The same shape `updates.py` builds out of the API reply."""
    return {'name': name, 'fileName': fn or name + '.7z', 'fileId': fid, 'version': ver,
            'category': cat, 'time': t}


# ---------------------------------------------------------------- the layer boundary
r.head(T('policy.hNoTransport'))
# The whole point of the move. Read as source rather than by importing: an import would only
# prove that the module loads, while what matters is that nothing in it CAN reach outward.
src = io.open(os.path.join(common.PKG, 'updatepolicy.py'), encoding='utf-8').read()
imported = set()
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Import):
        imported.update(a.name.split('.')[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add((node.module or '').split('.')[0] or '.')
FORBIDDEN = {'urllib', 'http', 'socket', 'ssl', 'mobase', 'os', 'io', 'winapi', 'base'}
r.case(T('policy.importsNothingOutward'), sorted(imported & FORBIDDEN), [])
r.note(T('policy.importsAre'), ', '.join(sorted(imported)))

# ---------------------------------------------------------------- the table
r.head(T('policy.hVerdicts'))
# label key, page files, ids we downloaded, when our archive was uploaded, verdict, reason
TABLE = [
    (T('policy.ruleNoVersionCompare'),
     [F('Tool SE', 1, 'main', 100, '2.0'), F('Tool SE', 2, 'main', 100 + 2 * D, '1.9')],
     {1}, 100, 'UPDATE-AVAILABLE', 'upd.newer'),
    (T('policy.ruleGogIsSibling'),
     [F('Tool SE Steam', 1, 'main', 100), F('Tool SE GOG', 2, 'main', 100 + 2 * D)],
     {1}, 100, 'UP-TO-DATE', 'upd.current'),
    (T('policy.ruleSameVersionMinutesApart'),
     [F('Tool', 1, 'main', 100, '1.0'), F('Tool', 2, 'main', 100 + 60, '1.0')],
     {1}, 100, 'UP-TO-DATE', 'upd.current'),
    (T('policy.ruleOursInOldNoReplacement'),
     [F('Tool', 1, 'old', 100), F('Other Thing', 2, 'main', 100 - D)],
     {1}, 100, 'CANNOT-MATCH', 'upd.retired'),
    (T('policy.ruleAllOldMainRenamed'),
     [F('Tool', 1, 'old', 100), F('Tool Redux', 2, 'main', 100 + 3 * D)],
     {1}, 100, 'UPDATE-AVAILABLE', 'upd.renamed'),
    (T('policy.ruleNothingMarkedMainNewer'),
     [F('Tool', 5, 'main', 100 + 3 * D)], set(), 100, 'REUPLOADED', 'upd.replaced'),
    (T('policy.ruleNothingMarkedNoDates'),
     [F('Tool', 5, 'main', 100 + 3 * D)], set(), None, 'CANNOT-MATCH', 'upd.unmarked'),
    (T('policy.ruleFreshArchiveOldBuild'),
     [F('Tool', 1, 'old', 100), F('Tool', 2, 'main', 100 + 3 * D)],
     {1, 2}, 100, 'DOWNLOADED-NOT-INSTALLED', 'upd.downloadedNotInstalled'),
    (T('policy.ruleHotfixInUpdate'),
     [F('Tool', 1, 'main', 100), F('Tool Hotfix', 2, 'update', 100 + D)],
     {1}, 100, 'UPDATE-AVAILABLE', 'upd.hotfix'),
    (T('policy.ruleOtherRoleInMain'),
     [F('Tool', 1, 'main', 100), F('Tool Extras Pack', 2, 'main', 100 + 2 * D)],
     {1}, 100, 'CANNOT-MATCH', 'upd.otherRole'),
    (T('policy.ruleNothingNewer'),
     [F('Tool', 1, 'main', 100), F('Tool', 2, 'old', 100 - 3 * D)],
     {1}, 100, 'UP-TO-DATE', 'upd.current'),
    (T('policy.ruleTextureSizeIsNotVersion'),
     [F('Tool 2K', 1, 'main', 100), F('Tool 4K', 2, 'main', 100 + 2 * D)],
     {1}, 100, 'CANNOT-MATCH', 'upd.otherRole'),
    (T('policy.ruleVrIsNotSe'),
     [F('Tool SE', 1, 'main', 100), F('Tool VR', 2, 'main', 100 + 2 * D)],
     {1}, 100, 'CANNOT-MATCH', 'upd.otherRole'),
]
for label, files, got, installed, verdict, why in TABLE:
    d = policy.decide(files, got, set(), installed)
    r.case(label, (d['verdict'], d['why']), (verdict, why))

# ---------------------------------------------------------------- what comes back with it
r.head(T('policy.hReplyShape'))
newer = policy.decide(
    [F('Tool', 1, 'main', 100), F('Tool', 2, 'main', 100 + D), F('Tool', 3, 'main', 100 + 2 * D)],
    {1}, set(), 100)
r.case(T('policy.newestNamed'), newer['newest']['fileId'], 3)
r.case(T('policy.newerListedNewestFirst'),
       [f['fileId'] for f in newer['items'][0]['newer']], [3, 2])
r.case(T('policy.limitHonoured'),
       len(policy.decide([F('Tool', 1, 'main', 100)] +
                         [F('Tool', i, 'main', 100 + i * D) for i in (2, 3, 4, 5)],
                         {1}, set(), 100, limit=2)['items'][0]['newer']), 2)
# The invented pages are dated in seconds from the epoch, so 100 + two days is 3 January
# 1970. Odd to read, and better than comparing the reply against the same helper that built
# it - that would agree even if both were wrong.
r.case(T('policy.briefCarriesDate'), newer['newest']['date'], '1970-01-03')
# The reason is a key and its substitutions, never a finished phrase: the decision does not
# know what language it will be read in.
replaced = policy.decide([F('Tool', 5, 'main', 100 + 3 * D)], set(), set(), 100)
r.case(T('policy.whyArgsAreDates'), sorted(replaced['whyArgs']), ['mine', 'theirs'])
r.case(T('policy.matchedByNameNotId'),
       policy.decide([F('Tool', 1, 'main', 100, '', 'tool-1-0.7z')],
                     set(), {'tool-1-0.7z'}, 100)['verdict'], 'UP-TO-DATE')

# ---------------------------------------------------------------- the helpers
r.head(T('policy.hHelpers'))
r.case(T('policy.resolutionIsNotVersion'),
       policy.role('Tool 4K v2.1') != policy.role('Tool 2K v2.1'), True)
r.case(T('policy.versionDropped'), policy.role('Tool v2.1') == policy.role('Tool 3.0'), True)
r.case(T('policy.wordOrderDoesNotMatter'),
       policy.role('Tool SE Patch') == policy.role('Patch Tool SE'), True)
r.case(T('policy.timeFromNameEpoch'), policy.arc_time('x-1234-1-0-1700000000.7z'), 1700000000)
r.case(T('policy.timeFromNameIso'), policy.arc_time('x 1 2026-09-07T09-14Z y.7z') > 0, True)
r.case(T('policy.noTimeInName'), policy.arc_time('Tool.7z'), None)
r.case(T('policy.stampInWords'), policy.stamp(1700000000), '2023-11-14')
r.case(T('policy.stampOfNothing'), policy.stamp(None), '')

r.done()
