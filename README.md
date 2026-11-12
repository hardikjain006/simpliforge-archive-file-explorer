# Archive-file explorer

Lists the entries of a single archive file. One input mode, one output mode,
useful help text, and predictable errors.

## Command and input mode

The tool exposes exactly one input mode: one archive file given as a single
positional command-line argument.

```sh
archive-explorer <archive-file>
archive-explorer --help
archive-explorer -h
```

- `<archive-file>` is a path to one readable regular file.
- Exactly one positional argument is required. Zero arguments, more than one
  argument, or any unknown flag is a usage error (see Errors).
- There is no second input mode: no standard-input reading, no directory
  argument, no URL or network input, and no multi-archive invocation.
- `-h` and `--help` print help text (see Help) and ignore all other arguments.
- Argument parsing uses exact tokens only. If any argument is exactly `-h`
  or `--help`, help is printed (exit `0`) and all other arguments are
  ignored. Otherwise `--` is not supported and counts as an unknown flag;
  `--help=<value>` and combined short flags such as `-hx` are unknown flags
  because they are not the exact tokens `-h`/`--help`; an empty-string
  argument counts as the one positional argument (so `archive-explorer ""`
  is an archive error, exit `1`, not a usage error).

### Supported formats

- ZIP archives (`.zip`).
- TAR archives, uncompressed or gzip-compressed (`.tar`, `.tar.gz`, `.tgz`).
- Format is detected from file content, not from the file extension, so a
  correctly formed archive with an unusual extension is still accepted.
- Any other format is an unsupported-format error (see Errors).

## Output mode

The tool has exactly one output mode: the entry listing on standard output.

- One entry path per line, terminated by `\n` (LF).
- Paths use `/` as the separator on every platform.
- Entry path strings are emitted verbatim as stored in the archive headers.
  No normalization is applied before sorting and printing: leading `./`,
  leading `/`, redundant separators, and backslash characters are preserved
  as-is. Sorting and printing operate on those verbatim strings. An entry
  name that is not valid UTF-8 is an archive error (exit `1`). The `/` rule
  above means the tool never substitutes a platform-specific separator.
- A "directory entry" is a member whose archive type flag marks it as a
  directory, independent of any link target. A directory entry prints with
  exactly one trailing `/`: one is appended when the stored name lacks it
  and never doubled when the stored name already ends with one. Any other
  member prints verbatim with no `/` added (a stored trailing `/` on a
  non-directory member is preserved as-is). Examples: directory `docs`
  prints as `docs/`; symlink `latest` pointing at `lib-v2/` prints as
  `latest`; symlink `docslink` pointing at `docs/` prints as `docslink`.
- Only explicitly stored members produce output lines. Missing parent
  directories are never synthesized: an archive storing only `a/b.txt` with
  no stored `a/` entry prints exactly one line, `a/b.txt`.
- Lines are sorted in ascending order by Unicode code point over the full
  path string, so output order is deterministic and independent of the order
  entries were stored in the archive. Sorting applies to the stored verbatim
  strings before any trailing `/` is appended for printing (so stored `docs`
  sorts before stored `docs-file` even though printed `docs/` sorts after
  `docs-file`).
- There is no header, footer, summary line, or progress output on stdout.
- An archive that contains no entries succeeds and prints zero lines.
- There is no second output mode: no JSON, tree, table, file-writing, or
  quiet/verbose variant.
- Nothing is extracted, created, modified, or deleted. Nested archives are
  listed as ordinary entries and are never recursed into.

## Help text

- `archive-explorer --help` and `archive-explorer -h` print usage to standard
  output and exit with status `0`.
- The help text states the single-argument usage, the supported formats, the
  one-line-per-entry output, and where errors go, with at least one example
  invocation that lists an archive.

## Errors

All failures print one human-readable line to standard error of the form:

```text
error: <reason>
```

where `<reason>` names the problem in plain language (for example a missing
file, an unsupported format, or a corrupt archive). Standard output is zero
bytes on every failure (exit `1` or `2`); no listing, usage text, or other
bytes are printed to standard output. Exit statuses are:

| Situation | Exit status |
|---|---|
| Success, including `--help` / `-h` | `0` |
| Archive problem: file not found, not a regular file, unreadable, unsupported format, or corrupt/unlistable archive | `1` |
| Usage problem: wrong number of arguments or unknown flag | `2` |

## Boundary cases

- Missing path argument, extra arguments, or an unknown flag: usage error
  (exit `2`).
- Nonexistent path, directory path, or unreadable file: archive error
  (exit `1`).
- Empty archive: success with zero output lines (exit `0`).
- Empty file that is neither ZIP nor TAR content: unsupported-format error
  (exit `1`).
- Truncated or otherwise corrupt archive: corrupt-archive error (exit `1`).
- Entry names with Unicode characters: listed verbatim as stored (UTF-8),
  ordered by code point; an entry name that is not valid UTF-8 is an
  archive error (exit `1`).
- Duplicate entry names: one output line per archive member, adjacent after
  sorting.
- Symbolic links and other special members: listed by their own path without
  being followed; link targets are never read. A symlink never gains a
  trailing `/`, even when it points at a directory (see Output mode).
- Implicit parent directories: never synthesized; only stored members print.
- `--`, `--help=<value>`, or combined short flags such as `-hx`: usage
  error (exit `2`).
- Empty-string positional argument (`archive-explorer ""`): counts as the
  one positional argument, so it is an archive error (exit `1`).
- Large archives: entries are listed without extracting file contents.

## Non-goals

- No extraction, creation, modification, or deletion of archives.
- No recursion into nested archives.
- No additional input modes (stdin, directories, URLs, multiple files).
- No additional output modes (JSON, tree view, output files, verbosity flags).
