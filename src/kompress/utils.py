"""
Helper utils for zippath adapter
"""

from __future__ import annotations

import fnmatch
import os
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol, Self


def check_read_mode(*, mode: str, path: object) -> None:
    if mode not in {'r', 'rt', 'tr', 'rb', 'br'}:
        raise ValueError(f"{type(path).__name__}.open() does not support mode {mode!r}")


class _GlobPath(Protocol):
    @property
    def name(self) -> str: ...

    def is_dir(self) -> bool: ...

    def iterdir(self) -> Iterator[Self]: ...


def archive_glob[GlobPathT: _GlobPath](
    root: GlobPathT,
    pattern: str | os.PathLike[str],
    *,
    recursive: bool,
    **kwargs,
) -> Iterator[GlobPathT]:
    case_sensitive: bool | None = kwargs.pop('case_sensitive', None)
    kwargs.pop('recurse_symlinks', False)
    if kwargs:
        unexpected = next(iter(kwargs))
        raise TypeError(f"glob() got an unexpected keyword argument {unexpected!r}")

    pattern_str = os.fspath(pattern)
    if pattern_str == '':
        raise ValueError("Unacceptable pattern: ''")

    pattern_path = Path(pattern_str)
    if pattern_path.is_absolute():
        raise NotImplementedError("Non-relative patterns are unsupported")

    # Repeating os.sep when os.altsep is unavailable avoids platform-specific optional typing.
    separators = (os.sep, os.altsep or os.sep)
    directory_only = pattern_str.endswith(separators)
    pattern_parts = pattern_path.parts
    if recursive:
        pattern_parts = ('**', *pattern_parts)

    def matches(name: str, part: str) -> bool:
        if case_sensitive is None:
            return fnmatch.fnmatch(name, part)
        if case_sensitive:
            return fnmatch.fnmatchcase(name, part)
        return fnmatch.fnmatchcase(name.casefold(), part.casefold())

    # Each state is handled once, preventing repeated `**` components from doing duplicate work.
    states: list[tuple[GlobPathT, int]] = [(root, 0)]
    visited: set[tuple[GlobPathT, int]] = set()
    while states:
        current, pattern_pos = states.pop()
        state = (current, pattern_pos)
        if state in visited:
            continue
        visited.add(state)

        if pattern_pos == len(pattern_parts):
            if not directory_only or current.is_dir():
                yield current
            continue
        if not current.is_dir():
            continue

        part = pattern_parts[pattern_pos]
        children = list(current.iterdir())
        if part == '**':
            # Add descendants first so the zero-segment state is popped and handled first.
            states.extend((child, pattern_pos) for child in reversed(children) if child.is_dir())
            if pattern_pos == len(pattern_parts) - 1 and sys.version_info[:2] >= (3, 13):
                # Since Python 3.13, a terminal `**` yields files as well as directories.
                states.extend((child, pattern_pos + 1) for child in reversed(children))
            states.append((current, pattern_pos + 1))
        else:
            states.extend((child, pattern_pos + 1) for child in reversed(children) if matches(child.name, part))


RootName = str
DirName = str
FileName = str
Entry = tuple[RootName, list[tuple[DirName, 'Entry']], list[FileName]]


def walk_paths(
    paths: Iterable[str],
    separator: str,
) -> Iterator[tuple[RootName, list[DirName], list[FileName]]]:
    # this is basically a tree, so we can walk it later, potentially skipping dirs modified in-place by the callee
    stack: list[Entry] = [('.', [], [])]
    stack_pos = 0

    for p in paths:
        split = p.rsplit(separator, maxsplit=1)
        if len(split) == 1:
            p_parent = '.'
            [p_name] = split
        else:
            p_parent, p_name = split

        is_dir = p_name == ''
        dirname: str | None = None

        if is_dir:
            split = p_parent.rsplit(separator, maxsplit=1)
            if len(split) == 1:
                target_root = '.'
                [dirname] = split
            else:
                # todo hmm can we avoid extra split?
                target_root, dirname = split
        else:
            target_root = p_parent

        while True:
            assert stack_pos >= 0, (p, stack)
            parent_root, parent_dirs, parent_files = stack[stack_pos]
            if target_root == parent_root:
                break
            stack_pos -= 1

        if is_dir:  # new dir detected!
            assert dirname is not None
            new_entry: Entry = (p_parent, [], [])
            stack.append(new_entry)
            stack_pos = len(stack) - 1

            parent_dirs.append((dirname, new_entry))
        else:
            assert stack_pos != -1, (p, stack)
            parent_files.append(p_name)

    def _traverse(entry: Entry) -> Iterator[tuple[RootName, list[DirName], list[FileName]]]:
        (root, dir_entries, files) = entry
        child_dirs = dict(dir_entries)
        dirnames = list(child_dirs.keys())

        dirnames.sort()
        files.sort()
        yield root, dirnames, files

        # traverse dirnames, not dir_entries! since we want to respect if the callee modifies them
        for d in dirnames:
            yield from _traverse(child_dirs[d])

    yield from _traverse(stack[0])


def test_walk_paths_basic() -> None:
    # not sure about this one but this is kinda compatible with pathlib.Path.glob behaviour
    assert list(walk_paths([], separator=os.sep)) == [
        ('.', [], []),
    ]

    # just two files with no extra dirs
    assert list(walk_paths(['aaa', 'bbb'], separator=os.sep)) == [
        ('.', [], ['aaa', 'bbb']),
    ]

    # one empty dir
    assert list(walk_paths(['aaa/'], separator='/')) == [
        ('.'  , ['aaa'], []),
        ('aaa', []     , []),
    ]  # fmt: skip

    # dir with one dir with one file
    assert list(walk_paths(['aaa/', 'aaa/bbb/', 'aaa/bbb/fff'], separator='/')) == [
        ('.'      , ['aaa'], []),
        ('aaa'    , ['bbb'], []),
        ('aaa/bbb', []     , ['fff']),
    ]  # fmt: skip


def test_walk_paths_against_stdlib() -> None:
    def as_paths(root: Path) -> Iterator[str]:
        for r, dirs, files in root.walk():
            rr = r.relative_to(root)
            for d in dirs:
                yield f'{rr / d}{os.sep}'
            for f in files:
                yield str(rr / f)

    def check_against_builtin(root: Path) -> None:
        expected = []
        for r, dirs, files in root.walk():
            dirs.sort()
            files.sort()
            expected.append((str(r.relative_to(root)), dirs, files))
        assert len(expected) > 1  # just in case

        paths = sorted(as_paths(root))
        actual = list(walk_paths(paths, separator=os.sep))

        assert expected == actual

    git_path = Path(__file__).absolute().parent.parent.parent / '.git'
    assert git_path.exists(), git_path
    check_against_builtin(git_path)
