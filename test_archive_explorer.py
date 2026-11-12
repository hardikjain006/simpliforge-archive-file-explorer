"""Repository-native tests for the archive-file explorer.

Black-box contract tests: every test invokes ``archive_explorer.py`` as a
subprocess and asserts byte-exact standard output, the ``error: <reason>``
standard-error form, and the exit status (0 success, 1 archive problem,
2 usage problem) required by README.md.
"""

import io
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ARCHIVE_EXPLORER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "archive_explorer.py"
)
ARCHIVE_EXPLORER_SHIM = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "archive-explorer"
)


def make_zip_bytes(members):
    """Build a ZIP archive. ``members`` maps name -> bytes (in order)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _tar_data_blocks(data):
    """Pad file content to whole 512-byte TAR data blocks."""
    return data + b"\x00" * (-len(data) % 512)


def make_tar_bytes(members, compress=False):
    """Build a TAR archive. ``members`` is a list of ("file"|"dir"|"link",
    name, payload) tuples in order. ``payload`` is bytes for files, the
    link target for links, and None for dirs.

    Members are serialized directly from ``TarInfo.tobuf`` so the exact
    byte layout is known (``tarfile`` pads streams to whole records when
    writing through a file object, which would hide the end marker).
    """
    blocks = []
    for kind, name, payload in members:
        info = tarfile.TarInfo(name)
        if kind == "dir":
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.size = 0
            blocks.append(info.tobuf(tarfile.PAX_FORMAT, "utf-8", "strict"))
        elif kind == "link":
            info.type = tarfile.SYMTYPE
            info.linkname = payload
            info.size = 0
            blocks.append(info.tobuf(tarfile.PAX_FORMAT, "utf-8", "strict"))
        else:
            data = payload if payload is not None else b""
            info.size = len(data)
            blocks.append(info.tobuf(tarfile.PAX_FORMAT, "utf-8", "strict"))
            blocks.append(_tar_data_blocks(data))
    raw = b"".join(blocks) + b"\x00" * 1024
    if compress:
        import gzip

        return gzip.compress(raw)
    return raw


def raw_tar_header(raw_name, typeflag=b"0"):
    """Build one 512-byte TAR header with an exact raw name (no data or
    end-of-archive blocks), for names ``tarfile`` cannot express."""
    header = bytearray(512)
    header[0 : len(raw_name)] = raw_name
    header[100:108] = b"0000777\x00"
    header[108:116] = b"0000000\x00"
    header[116:124] = b"0000000\x00"
    header[124:136] = b"00000000000\x00"
    header[136:148] = b"00000000000\x00"
    header[148:156] = b"        "
    header[156:157] = typeflag
    header[257:262] = b"ustar"
    header[262:264] = b"00"
    checksum = sum(header)
    header[148:156] = ("%06o\x00 " % checksum).encode("ascii")
    return bytes(header)


def raw_tar_member(raw_name, typeflag=b"0"):
    """Build a minimal one-member TAR archive with an exact raw name."""
    return raw_tar_header(raw_name, typeflag) + b"\x00" * 1024


class ArchiveExplorerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write(self, name, data):
        full = self.path(name)
        with open(full, "wb") as handle:
            handle.write(data)
        return full

    def run_tool(self, *args):
        return subprocess.run(
            [sys.executable, ARCHIVE_EXPLORER, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.tmp.name,
        )

    def assertFailure(self, result, status):
        self.assertEqual(result.returncode, status)
        self.assertEqual(result.stdout, b"")
        self.assertRegex(result.stderr.decode("utf-8"), r"^error: .+\n$")

    def test_zip_lists_all_members_sorted_verbatim(self):
        inner = make_zip_bytes({"inner-file.txt": b"x"})
        archive = self.write(
            "mixed.zip",
            make_zip_bytes(
                {
                    "zebra.txt": b"z",
                    "docs-file": b"d",
                    "docs/": b"",
                    "dup.txt": b"1",
                    "a/b.txt": b"no stored a/ entry",
                    "café.txt": "café".encode("utf-8"),
                    "inner.zip": inner,
                }
            ),
        )
        # Second member with a duplicate name: one line per archive member.
        with zipfile.ZipFile(archive, "a") as extra:
            extra.writestr("dup.txt", b"2")
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        # ZIP directory entries store the trailing "/" as part of the name,
        # so stored "docs/" sorts after stored "docs-file" ("-" < "/").
        self.assertEqual(
            result.stdout,
            "a/b.txt\n"
            "café.txt\n"
            "docs-file\n"
            "docs/\n"
            "dup.txt\n"
            "dup.txt\n"
            "inner.zip\n"
            "zebra.txt\n".encode("utf-8"),
        )

    def test_sort_key_is_stored_name_before_slash_appended(self):
        base = make_tar_bytes([("file", "docs-file", b"a")])
        archive = self.write(
            "sort.tar",
            base[:-1024]
            + raw_tar_header(b"docs", typeflag=b"5")
            + b"\x00" * 1024,
        )
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        # Stored "docs" sorts before stored "docs-file" even though the
        # printed "docs/" would sort after "docs-file".
        self.assertEqual(result.stdout, b"docs/\ndocs-file\n")

    def test_tar_lists_dirs_links_and_verbatim_names(self):
        base = make_tar_bytes(
            [
                ("file", "plain.txt", b"hi"),
                ("dir", "slashdir/", None),
                ("link", "latest", "lib-v2/"),
                ("link", "filelink", "plain.txt"),
                ("link", "dangling", "no/such/target"),
                ("file", "./prefixed.txt", b"dot"),
                ("file", "/abs.txt", b"abs"),
                ("file", "back\\slash.txt", b"win"),
            ]
        )
        # Splice a directory member whose stored name lacks a trailing "/"
        # before the end-of-archive marker (tar readers stop at it).
        archive = self.write(
            "mixed.tar",
            base[:-1024] + raw_tar_member(b"noslash", typeflag=b"5"),
        )
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            b"./prefixed.txt\n"
            b"/abs.txt\n"
            b"back\\slash.txt\n"
            b"dangling\n"
            b"filelink\n"
            b"latest\n"
            b"noslash/\n"
            b"plain.txt\n"
            b"slashdir/\n",
        )

    def test_non_directory_member_keeps_stored_trailing_slash(self):
        archive = self.write("odd.tar", raw_tar_member(b"weird/"))
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"weird/\n")

    def test_gzip_tar_is_supported(self):
        archive = self.write(
            "arch.tgz",
            make_tar_bytes(
                [("file", "b.txt", b"b"), ("file", "a.txt", b"a")],
                compress=True,
            ),
        )
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"a.txt\nb.txt\n")

    def test_format_detected_from_content_not_extension(self):
        archive = self.write(
            "weird.dat", make_zip_bytes({"only.txt": b"data"})
        )
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"only.txt\n")

    def test_implicit_parent_directories_are_never_synthesized(self):
        archive = self.write(
            "child.tar", make_tar_bytes([("file", "a/b.txt", b"deep")])
        )
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"a/b.txt\n")

    def test_empty_zip_succeeds_with_zero_lines(self):
        archive = self.write("empty.zip", make_zip_bytes({}))
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_empty_tar_succeeds_with_zero_lines(self):
        archive = self.write("empty.tar", make_tar_bytes([]))
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_nested_archive_is_listed_not_recursed(self):
        inner = make_zip_bytes({"deep.txt": b"deep"})
        archive = self.write(
            "outer.zip", make_zip_bytes({"inner.zip": inner})
        )
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"inner.zip\n")

    def test_entries_listed_without_extracting(self):
        members = [("file", "f%04d.txt" % i, b"x") for i in range(500)]
        archive = self.write("big.tar", make_tar_bytes(members))
        before = set(os.listdir(self.tmp.name))
        result = self.run_tool(archive)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.decode("utf-8").splitlines()), 500)
        self.assertEqual(set(os.listdir(self.tmp.name)), before)

    def test_help_long_flag(self):
        result = self.run_tool("--help")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        text = result.stdout.decode("utf-8")
        for token in ("Usage:", "ZIP", "TAR", "error", "archive-explorer "):
            self.assertIn(token, text)
        self.assertIn("archive-explorer photos.tar.gz", text)

    def test_help_short_flag(self):
        result = self.run_tool("-h")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertIn(b"Usage:", result.stdout)

    def test_help_ignores_all_other_arguments(self):
        result = self.run_tool("--", "--help", "missing.tar", "-x")
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"Usage:", result.stdout)

    def test_no_arguments_is_usage_error(self):
        self.assertFailure(self.run_tool(), 2)

    def test_two_arguments_is_usage_error(self):
        self.assertFailure(self.run_tool("a.zip", "b.zip"), 2)

    def test_unknown_flag_is_usage_error(self):
        self.assertFailure(self.run_tool("-x"), 2)

    def test_double_dash_is_usage_error(self):
        self.assertFailure(self.run_tool("--"), 2)

    def test_help_with_value_is_usage_error(self):
        self.assertFailure(self.run_tool("--help=x"), 2)

    def test_combined_short_flags_are_usage_error(self):
        self.assertFailure(self.run_tool("-hx"), 2)

    def test_single_dash_is_usage_error(self):
        self.assertFailure(self.run_tool("-"), 2)

    def test_missing_file_is_archive_error(self):
        result = self.run_tool(self.path("no-such-file.zip"))
        self.assertFailure(result, 1)
        self.assertIn(b"not found", result.stderr)

    def test_empty_string_argument_is_archive_error(self):
        self.assertFailure(self.run_tool(""), 1)

    def test_directory_is_archive_error(self):
        self.assertFailure(self.run_tool(self.tmp.name), 1)

    def test_unreadable_file_is_archive_error(self):
        if os.geteuid() == 0:
            self.skipTest("unreadable files stay readable for root")
        archive = self.write("locked.zip", make_zip_bytes({"a.txt": b"a"}))
        os.chmod(archive, 0)
        self.assertFalse(os.access(archive, os.R_OK))
        self.assertFailure(self.run_tool(archive), 1)

    def test_unsupported_format_is_archive_error(self):
        archive = self.write("notes.txt", b"just some text\n")
        result = self.run_tool(archive)
        self.assertFailure(result, 1)
        self.assertIn(b"unsupported", result.stderr)

    def test_empty_file_is_unsupported_format(self):
        archive = self.write("empty.bin", b"")
        result = self.run_tool(archive)
        self.assertFailure(result, 1)
        self.assertIn(b"unsupported", result.stderr)

    def test_bzip2_tar_is_unsupported(self):
        try:
            import bz2  # noqa: F401
        except ImportError:
            self.skipTest("bz2 module unavailable")
        buffer = io.BytesIO()
        try:
            with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
                info = tarfile.TarInfo("a.txt")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"a"))
        except (tarfile.CompressionError, ImportError):
            self.skipTest("bz2 tar writing unavailable")
        archive = self.write("arch.tbz", buffer.getvalue())
        result = self.run_tool(archive)
        self.assertFailure(result, 1)
        self.assertIn(b"unsupported", result.stderr)

    def test_truncated_zip_is_corrupt(self):
        full = make_zip_bytes({"a.txt": b"a" * 100, "b.txt": b"b" * 100})
        self.assertTrue(full.startswith(b"PK"))
        archive = self.write("cut.zip", full[:30])
        result = self.run_tool(archive)
        self.assertFailure(result, 1)
        self.assertIn(b"corrupt", result.stderr)

    def test_truncated_tar_is_corrupt(self):
        full = make_tar_bytes(
            [("file", "a.txt", b"a" * 5000), ("file", "b.txt", b"b" * 5000)]
        )
        archive = self.write("cut.tar", full[:300])
        result = self.run_tool(archive)
        self.assertFailure(result, 1)
        self.assertIn(b"corrupt", result.stderr)

    def test_non_utf8_tar_name_is_archive_error(self):
        archive = self.write("bad.tar", raw_tar_member(b"\xff\xfe bad.txt"))
        self.assertFailure(self.run_tool(archive), 1)

    def test_non_utf8_zip_name_is_archive_error(self):
        good = make_zip_bytes({"victim.txt": b"data"})
        bad = good.replace(b"victim.txt", b"victim\xff.txt")
        self.assertNotEqual(good, bad)
        archive = self.write("bad.zip", bad)
        self.assertFailure(self.run_tool(archive), 1)

    def test_shim_executable_lists_archive(self):
        archive = self.write("shim.zip", make_zip_bytes({"s.txt": b"s"}))
        os.chmod(ARCHIVE_EXPLORER_SHIM, os.stat(ARCHIVE_EXPLORER_SHIM).st_mode
                 | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        result = subprocess.run(
            [ARCHIVE_EXPLORER_SHIM, archive],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.tmp.name,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"s.txt\n")


if __name__ == "__main__":
    unittest.main()
