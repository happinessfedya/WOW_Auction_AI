import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from wow_auction.file_reader import StableReadError, read_stable


class StableReaderTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent
        self.directory = tempfile.TemporaryDirectory(dir=self.test_root)
        self.addCleanup(self.cleanup_directory)
        self.path = Path(self.directory.name) / "Synthetic.lua"
        self.path.write_bytes(b'DB={cash=1}')

    def cleanup_directory(self):
        self.assertEqual(Path(self.directory.name).resolve().parent, self.test_root)
        self.directory.cleanup()

    def test_stable_read_and_repeat_leave_source_unchanged(self):
        before = self.path.stat()
        first = read_stable(self.path, settle_seconds=0)
        second = read_stable(self.path, settle_seconds=0)
        self.assertEqual(first, second)
        self.assertEqual(first.data, b'DB={cash=1}')
        self.assertEqual(first.sha256, hashlib.sha256(first.data).hexdigest())
        self.assertEqual(first.size_bytes, len(first.data))
        self.assertEqual(first.file_written_at_ns, before.st_mtime_ns)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_same_size_same_mtime_changed_bytes_detected(self):
        before = self.path.stat()
        def mutate(_):
            self.path.write_bytes(b'DB={cash=2}')
            os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with patch("wow_auction.file_reader.time.sleep", side_effect=mutate):
            with self.assertRaises(StableReadError) as error:
                read_stable(self.path, settle_seconds=0)
        self.assertEqual(error.exception.code, "file_changed")

    def test_partial_write_growth_detected(self):
        def mutate(_):
            with self.path.open("ab") as stream:
                stream.write(b' trailing')
        with patch("wow_auction.file_reader.time.sleep", side_effect=mutate):
            with self.assertRaises(StableReadError) as error:
                read_stable(self.path, settle_seconds=0)
        self.assertEqual(error.exception.code, "file_changed")

    def test_replaced_file_detected_even_with_same_content(self):
        replacement = self.path.with_suffix('.tmp')
        replacement.write_bytes(self.path.read_bytes())
        def mutate(_):
            replacement.replace(self.path)
        with patch("wow_auction.file_reader.time.sleep", side_effect=mutate):
            with self.assertRaises(StableReadError) as error:
                read_stable(self.path, settle_seconds=0)
        self.assertEqual(error.exception.code, "file_changed")

    def test_backup_rejected_before_read(self):
        with self.assertRaises(StableReadError) as error:
            read_stable(self.path.with_suffix('.lua.BAK'), settle_seconds=0)
        self.assertEqual(error.exception.code, "backup_requires_explicit_recovery")

    def test_missing_file_error_has_no_personal_path(self):
        with self.assertRaises(StableReadError) as error:
            read_stable(self.path.with_name('private_account.lua'), settle_seconds=0)
        self.assertEqual(error.exception.code, "read_failed")
        self.assertNotIn('private_account', str(error.exception))

    def test_directory_and_size_limit_rejected(self):
        with self.assertRaises(StableReadError) as error:
            read_stable(self.path.parent, settle_seconds=0)
        self.assertEqual(error.exception.code, "not_regular_file")
        with self.assertRaises(StableReadError) as error:
            read_stable(self.path, settle_seconds=0, max_bytes=2)
        self.assertEqual(error.exception.code, "size_limit")

    def test_read_only_open_flags(self):
        original_open = os.open
        flags_seen = []
        def observed_open(path, flags):
            flags_seen.append(flags)
            return original_open(path, flags)
        with patch("wow_auction.file_reader.os.open", side_effect=observed_open):
            read_stable(self.path, settle_seconds=0)
        self.assertEqual(len(flags_seen), 2)
        for flags in flags_seen:
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))

    def test_windows_ctime_semantic_difference_is_not_a_false_write(self):
        original_fstat = os.fstat
        def different_ctime(fd):
            metadata = original_fstat(fd)
            return SimpleNamespace(st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                                   st_size=metadata.st_size, st_mtime_ns=metadata.st_mtime_ns,
                                   st_ctime_ns=metadata.st_ctime_ns + 1000000,
                                   st_mode=metadata.st_mode,
                                   st_file_attributes=getattr(metadata, "st_file_attributes", 0))
        with patch("wow_auction.file_reader.os.fstat", side_effect=different_ctime):
            self.assertEqual(read_stable(self.path, settle_seconds=0).data, b'DB={cash=1}')

    def test_settle_limit_configuration(self):
        for seconds in (-1, 11, float('nan')):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                read_stable(self.path, settle_seconds=seconds)


if __name__ == "__main__":
    unittest.main()
