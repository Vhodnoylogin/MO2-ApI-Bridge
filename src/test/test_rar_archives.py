# -*- coding: utf-8 -*-
"""Real-reader checks over self-authored STORE RAR4/RAR5 framing.

The test writes only disposable archives containing the small byte strings below.
No downloaded payload, running organizer, credentials or native installation is
used. STORE fixtures establish framing and selection, not compressed/solid codec
coverage or host acceptance. RAR structures follow the vendor technical notes:
https://www.rarlab.com/technote.htm
https://sources.debian.org/src/rar/2:4.0.b3-1/technote.txt/
"""
import importlib
import os
import shutil
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

installer = importlib.import_module(common.NAME + '.archives')
native = importlib.import_module(common.NAME + '.nativeinstall')
services = importlib.import_module(common.NAME + '.services')

CHOSEN = '00 Core/chosen.bin'
ALTERNATIVE = '01 Alternative/other.bin'
FOMOD = 'fomod/ModuleConfig.xml'
MEMBERS = (
    ('empty', None),
    (CHOSEN, b'bridge-owned selected fixture\n'),
    (ALTERNATIVE, b'bridge-owned unselected alternative\n'),
    (FOMOD, b'<config><moduleName>Bridge fixture</moduleName></config>\n'),
    ('fomod/preview.bin', b'bridge-owned preview not selected\n'),
)
PAYLOADS = {name: data for name, data in MEMBERS if data is not None}


def _vint(value):
    """Unsigned little-endian groups of seven bits, each with a continuation bit."""
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _rar4_header(kind, flags, body=b''):
    header = struct.pack('<BHH', kind, flags, 7 + len(body)) + body
    return struct.pack('<H', zlib.crc32(header) & 0xffff) + header


def rar4_bytes(members=MEMBERS, multivolume=False):
    """RAR 1.5-4.x STORE records, with complete header and payload checksums."""
    archive = bytearray(b'Rar!\x1a\x07\x00')
    archive += _rar4_header(0x73, 1 if multivolume else 0, b'\0' * 6)
    for name, payload in members:
        directory = payload is None
        data = b'' if directory else payload
        encoded = name.encode('ascii')
        # DOS time is fixed at 2000-01-01; STORE needs only the RAR 2.0 reader.
        body = struct.pack('<IIBIIBBHI', len(data), len(data), 2,
                           zlib.crc32(data), 0x28210000, 20, 0x30,
                           len(encoded), 0x10 if directory else 0x20) + encoded
        archive += _rar4_header(0x74, 0x8000 | (0xe0 if directory else 0), body)
        archive += data
    archive += _rar4_header(0x7b, 0x4000)
    return bytes(archive)


def _rar5_header(body):
    header = _vint(len(body)) + body
    return struct.pack('<I', zlib.crc32(header)) + header


def rar5_bytes(members=MEMBERS, multivolume=False):
    """RAR5 STORE records; CRC32 includes the encoded size and complete header."""
    archive = bytearray(b'Rar!\x1a\x07\x01\x00')
    archive += _rar5_header(b'\x01\x00' + _vint(1 if multivolume else 0))
    for name, payload in members:
        directory = payload is None
        data = b'' if directory else payload
        encoded = name.encode('utf-8')
        # Header flag 2 declares a data area; file flag 4 declares payload CRC32.
        body = b'\x02\x02' + _vint(len(data))
        body += _vint(4 | int(directory)) + _vint(len(data))
        body += _vint(0x10 if directory else 0x20)
        body += struct.pack('<I', zlib.crc32(data))
        body += b'\x00\x00' + _vint(len(encoded)) + encoded
        archive += _rar5_header(body) + data
    archive += _rar5_header(b'\x05\x00\x00')
    return bytes(archive)


FORMATS = (('Rar', rar4_bytes), ('Rar5', rar5_bytes))


class RarArchives(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.svc = services.Services(self.fx.organizer, lambda f, timeout=None: f(), 'README.md')
        self.seven = self.svc.cfg.seven_zip()
        if not self.seven:
            self.skipTest('7-Zip unavailable')

    def archive(self, kind, builder, **kwargs):
        path = self.fx.path(kind + '.rar')
        Path(path).write_bytes(builder(**kwargs))
        return path, installer.file_sha256(path)

    def command(self, operation, archive):
        return subprocess.run([self.seven, operation, '-slt', '-sccUTF-8', '--', archive],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding='utf-8', timeout=30)

    def assert_payloads(self, root, expected):
        actual = installer.tree_files(root)
        self.assertEqual(actual, {name.replace('/', os.sep).lower() for name in expected})
        for name in expected:
            self.assertEqual(Path(root, *name.split('/')).read_bytes(), PAYLOADS[name])

    def test_real_reader_recognizes_each_format_and_checks_complete_archive(self):
        for kind, builder in FORMATS:
            with self.subTest(format=kind):
                archive, original_hash = self.archive(kind, builder)
                listing = self.command('l', archive)
                self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
                self.assertIn('Type = ' + kind + '\n', listing.stdout)
                self.assertIn('Multivolume = -\n', listing.stdout)
                integrity = self.command('t', archive)
                self.assertEqual(integrity.returncode, 0, integrity.stdout + integrity.stderr)
                self.assertEqual(installer.file_sha256(archive), original_hash)

    def test_copy_backend_extracts_all_owned_files_without_changing_source(self):
        for kind, builder in FORMATS:
            with self.subTest(format=kind):
                archive, original_hash = self.archive(kind, builder)
                root = self.svc.installer._unpack(archive)
                try:
                    self.assert_payloads(root, set(PAYLOADS))
                    self.assertTrue(Path(root, 'empty').is_dir())
                finally:
                    shutil.rmtree(root)
                self.assertEqual(installer.file_sha256(archive), original_hash)

    def test_selected_backend_extracts_exact_payload_and_fomod_without_changing_source(self):
        for kind, builder in FORMATS:
            with self.subTest(format=kind):
                archive, original_hash = self.archive(kind, builder)
                root = self.svc.installer._unpack_selected(archive, [CHOSEN])
                try:
                    self.assert_payloads(root, {CHOSEN, FOMOD})
                    self.assertFalse(Path(root, *ALTERNATIVE.split('/')).exists())
                finally:
                    shutil.rmtree(root)
                self.assertEqual(installer.file_sha256(archive), original_hash)

    def test_source_reuse_preparation_retains_two_destinations_and_one_extracted_source(self):
        mappings = native._mappings([
            {'source': CHOSEN, 'destination': 'meshes/body_0.nif'},
            {'source': CHOSEN, 'destination': 'meshes/body_1.nif'}])
        self.assertEqual([row['destination'] for row in mappings],
                         ['meshes/body_0.nif', 'meshes/body_1.nif'])
        for kind, builder in FORMATS:
            with self.subTest(format=kind):
                archive, original_hash = self.archive(kind, builder)
                root = self.svc.installer._unpack_selected(
                    archive, [row['source'] for row in mappings])
                try:
                    self.assert_payloads(root, {CHOSEN, FOMOD})
                finally:
                    shutil.rmtree(root)
                self.assertEqual(installer.file_sha256(archive), original_hash)

    def test_archive_commands_never_wait_for_interactive_password_input(self):
        archive, _original_hash = self.archive('Rar5', rar5_bytes)
        actual_run = subprocess.run
        for selected in (False, True):
            with patch.object(installer.subprocess, 'run', wraps=actual_run) as run:
                root = (self.svc.installer._unpack_selected(archive, [CHOSEN]) if selected
                        else self.svc.installer._unpack(archive))
                try:
                    self.assertGreaterEqual(run.call_count, 1)
                    for call in run.call_args_list:
                        self.assertEqual(call.kwargs['stdin'], subprocess.DEVNULL)
                finally:
                    shutil.rmtree(root)

    def test_missing_member_and_unsafe_archive_member_refuse_before_temporary_output(self):
        for kind, builder in FORMATS:
            for members, sources in ((MEMBERS, ['missing.bin']),
                                     (MEMBERS + (('../escape.bin', b'owned unsafe fixture'),),
                                      [CHOSEN])):
                with self.subTest(format=kind, sources=sources, unsafe=members != MEMBERS):
                    archive, original_hash = self.archive(kind, builder, members=members)
                    with patch.object(installer.tempfile, 'mkdtemp',
                                      side_effect=AssertionError('unexpected temporary output')) as mkdtemp:
                        with self.assertRaises(ValueError):
                            self.svc.installer._unpack_selected(archive, sources)
                        mkdtemp.assert_not_called()
                    self.assertEqual(installer.file_sha256(archive), original_hash)

    def test_multivolume_flag_refuses_even_when_no_member_is_split(self):
        for kind, builder in FORMATS:
            with self.subTest(format=kind):
                archive, original_hash = self.archive(kind, builder, multivolume=True)
                listing = self.command('l', archive)
                self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
                self.assertIn('Multivolume = +\n', listing.stdout)
                self.assertNotIn('Split Before = +', listing.stdout)
                self.assertNotIn('Split After = +', listing.stdout)
                integrity = self.command('t', archive)
                self.assertEqual(integrity.returncode, 0, integrity.stdout + integrity.stderr)
                with patch.object(installer.tempfile, 'mkdtemp',
                                  side_effect=AssertionError('unexpected temporary output')) as mkdtemp:
                    with self.assertRaises(ValueError):
                        self.svc.installer._unpack_selected(archive, [CHOSEN])
                    mkdtemp.assert_not_called()
                self.assertEqual(installer.file_sha256(archive), original_hash)


if __name__ == '__main__':
    unittest.main()
