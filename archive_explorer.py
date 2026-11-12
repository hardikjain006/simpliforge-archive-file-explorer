"""Archive-file explorer.

Lists the entries of a single archive file. One input mode (one archive
file as a single positional argument), one output mode (the entry listing
on standard output), useful help text, and predictable errors.

Supported formats, detected from file content rather than the file
extension: ZIP archives, and TAR archives that are uncompressed or
gzip-compressed.
"""

import os
import sys
import tarfile
import zipfile

EXIT_OK = 0
EXIT_ARCHIVE_ERROR = 1
EXIT_USAGE_ERROR = 2

_HELP_LINES = [
    "Usage: archive-explorer <archive-file>",
    "       archive-explorer --help | -h",
    "",
    "List the entries of a single archive file.",
    "",
    "Input: exactly one positional argument giving the path to one readable",
    "regular archive file. Supported formats (detected from file content,",
    "not the file extension): ZIP (.zip), and TAR uncompressed or",
    "gzip-compressed (.tar, .tar.gz, .tgz).",
    "",
    "Output: one entry path per line on standard output, sorted by Unicode",
    "code point; directory entries end with a trailing '/'.",
    "",
    "Errors are reported as one 'error: <reason>' line on standard error;",
    "nothing is printed to standard output on failure. Exit status is 0 on",
    "success (including --help), 1 for archive problems (missing,",
    "unreadable, unsupported, or corrupt archive), and 2 for usage problems",
    "(wrong number of arguments or unknown flag).",
    "",
    "Example:",
    "  archive-explorer photos.tar.gz",
    "",
]

HELP_TEXT = "\n".join(_HELP_LINES)

_HELP_TOKENS = ("-h", "--help")

_ZIP_MAGIC = b"PK"
_GZIP_MAGIC = b"\x1f\x8b"
_USTAR_MAGIC = b"ustar"
_USTAR_MAGIC_OFFSET = 257


class ArchiveError(Exception):
    """A problem with the archive file itself (exit status 1)."""


def _parse_args(argv):
    """Classify the command line.

    Returns one of ("help", None), ("run", path), or ("usage", reason).
    """
    args = argv[1:]
    if any(arg in _HELP_TOKENS for arg in args):
        return ("help", None)
    if len(args) == 0:
        return ("usage", "expected exactly one archive file argument")
    if len(args) > 1:
        return ("usage", "expected exactly one archive file argument")
    arg = args[0]
    if arg.startswith("-"):
        return ("usage", "unknown flag: %s" % arg)
    return ("run", arg)


def _raw_zip_name(info):
    """Recover the exact stored bytes of a ZIP member name.

    ``ZipInfo.filename`` is already decoded (UTF-8 when the language
    encoding flag is set, CP437 otherwise). Re-encoding restores the raw
    header bytes in both cases because CP437 covers all 256 byte values.
    """
    if info.flag_bits & 0x800:
        return info.filename.encode("utf-8", "surrogateescape")
    return info.filename.encode("cp437")


def _read_zip_entries(path):
    """Read (stored name, is directory) pairs from a ZIP archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ArchiveError("corrupt archive: %s" % path) from exc
    except OSError as exc:
        raise ArchiveError("cannot read archive: %s" % path) from exc
    entries = []
    for info in infos:
        try:
            stored = _raw_zip_name(info).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchiveError(
                "entry name is not valid UTF-8 in archive: %s" % path
            ) from exc
        entries.append((stored, info.is_dir()))
    return entries


def _read_tar_entries(path, mode):
    """Read (stored name, is directory) pairs from a TAR archive."""
    try:
        archive = tarfile.open(path, mode, encoding="utf-8", errors="strict")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError("cannot open archive: %s" % path) from exc
    try:
        entries = []
        for member in archive:
            entries.append((member.name, member.isdir()))
        return entries
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError("corrupt archive: %s" % path) from exc
    except UnicodeError as exc:
        raise ArchiveError(
            "entry name is not valid UTF-8 in archive: %s" % path
        ) from exc
    finally:
        archive.close()


def _read_tar_if_supported(path):
    """Return TAR entries, or None when the content is not a TAR archive."""
    for mode in ("r:", "r:gz"):
        try:
            probe = tarfile.open(path, mode, encoding="utf-8", errors="strict")
        except (tarfile.TarError, OSError, EOFError, UnicodeError):
            continue
        probe.close()
        return _read_tar_entries(path, mode)
    return None


def _sniff_corrupt(path):
    """Decide whether unrecognized content looks truncated/corrupt."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_USTAR_MAGIC_OFFSET + len(_USTAR_MAGIC))
    except OSError:
        return False
    if head.startswith(_ZIP_MAGIC) or head.startswith(_GZIP_MAGIC):
        return True
    return len(head) == _USTAR_MAGIC_OFFSET + len(_USTAR_MAGIC) and (
        head[_USTAR_MAGIC_OFFSET:] == _USTAR_MAGIC
    )


def list_archive_entries(path):
    """Return printable listing lines for the archive at ``path``.

    Lines are sorted by Unicode code point over the stored verbatim entry
    strings. Raises :class:`ArchiveError` for any archive problem.
    """
    if not os.path.exists(path):
        raise ArchiveError("file not found: %s" % path)
    if not os.path.isfile(path):
        raise ArchiveError("not a regular file: %s" % path)
    try:
        is_zip = zipfile.is_zipfile(path)
    except OSError as exc:
        raise ArchiveError("cannot read archive: %s" % path) from exc
    if is_zip:
        entries = _read_zip_entries(path)
    else:
        entries = _read_tar_if_supported(path)
        if entries is None:
            if _sniff_corrupt(path):
                raise ArchiveError("corrupt archive: %s" % path)
            raise ArchiveError("unsupported archive format: %s" % path)
    entries.sort(key=lambda entry: entry[0])
    lines = []
    for stored, is_dir in entries:
        if is_dir and not stored.endswith("/"):
            lines.append(stored + "/")
        else:
            lines.append(stored)
    return lines


def main(argv=None, stdout=None, stderr=None):
    """Entry point. Returns the process exit status."""
    if argv is None:
        argv = sys.argv
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    action, value = _parse_args(argv)
    if action == "help":
        stdout.write(HELP_TEXT)
        return EXIT_OK
    if action == "usage":
        stderr.write("error: %s\n" % value)
        return EXIT_USAGE_ERROR
    try:
        lines = list_archive_entries(value)
    except ArchiveError as exc:
        stderr.write("error: %s\n" % exc)
        return EXIT_ARCHIVE_ERROR
    for line in lines:
        stdout.write(line + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
