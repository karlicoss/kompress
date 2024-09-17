"""
Helper utils for zippath adapter
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

RootName = str
DirName = str
FileName = str
Entry = Tuple[RootName, List[Tuple[DirName, 'Entry']], List[FileName]]


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

        if is_dir:
            split = p_parent.rsplit(separator, maxsplit=1)
            if len(split) == 1:
                target_root = '.'
                [dirname] = split
            else:
                # todo hmm can we avoid extra split?
                target_root, dirname = p_parent.rsplit(separator, maxsplit=1)
        else:
            target_root = p_parent

        while True:
            assert stack_pos >= 0, (p, stack)
            parent_root, parent_dirs, parent_files = stack[stack_pos]
            if target_root == parent_root:
                break
            stack_pos -= 1

        if is_dir:  # new dir detected!
            new_entry: Entry = (p_parent, [], [])
            stack.append(new_entry)
            stack_pos = len(stack) - 1

            parent_dirs.append((dirname, new_entry))  # type: ignore[possibly-undefined]
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
        # fmt: off
        ('.'  , ['aaa'], []),
        ('aaa', []     , []),
        # fmt: on
    ]

    # dir with one dir with one file
    assert list(walk_paths(['aaa/', 'aaa/bbb/', 'aaa/bbb/fff'], separator='/')) == [
        # fmt: off
        ('.'      , ['aaa'], []),
        ('aaa'    , ['bbb'], []),
        ('aaa/bbb', []     , ['fff']),
        # fmt: on
    ]


def test_walk_paths_against_stdlib() -> None:
    import sys

    import pytest

    if sys.version_info[:2] < (3, 12):
        pytest.skip("pathlibe.Path.walk is only present from python 3.12")

    def as_paths(root: Path) -> Iterator[str]:
        for r, dirs, files in root.walk():
            rr = r.relative_to(root)
            for d in dirs:
                yield f'{rr/ d}{os.sep}'
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
