from __future__ import annotations

import os
import zipfile
from datetime import datetime
from functools import total_ordering
from pathlib import Path
from typing import Iterator, Sequence

from .utils import walk_paths


@total_ordering
class ZipPath(zipfile.Path):
    # NOTE: is_dir/is_file might not behave as expected, the base class checks it only based on the slash in path

    _flavour = os.path  # this is necessary for some pathlib operations (in particular python 3.12)

    # seems that root/at are not exposed in the docs, so might be an implementation detail
    root: zipfile.CompleteDirs
    at: str

    def __init__(self, root: str | Path | zipfile.ZipFile | ZipPath, at: str = "") -> None:
        root_: str | Path | zipfile.ZipFile
        if isinstance(root, ZipPath):
            # hack to make sure ZipPath(ZipPath(...)) works
            root_ = root.root
            at_ = root.at
        else:
            root_ = root
            at_ = at

        super().__init__(root_, at_)

    @property
    def filepath(self) -> Path:
        res = self.root.filename
        assert res is not None  # make mypy happy
        assert isinstance(res, str)
        return Path(res)

    @property
    def subpath(self) -> Path:
        return Path(self.at)

    def absolute(self) -> ZipPath:
        return ZipPath(self.filepath.absolute(), self.at)

    def expanduser(self) -> ZipPath:
        return ZipPath(self.filepath.expanduser(), self.at)

    def exists(self) -> bool:
        if self.at == '':
            # special case, the base class returns False in this case for some reason
            return self.filepath.exists()
        return super().exists() or self._as_dir().exists()
        # TODO hmm seems that base class has special treatment for .at argument during construction,
        # it actually checks if it's a file or a dir, and in case of dir, appends '/'?
        # maybe use resolve_dir thing from base class??

    def _as_dir(self) -> zipfile.Path:
        # note: seems that zip always uses forward slash, regardless OS?
        return zipfile.Path(self.root, self.at + '/')

    def rglob(self, glob: str) -> Iterator[ZipPath]:
        # note: not 100% sure about the correctness, but seem fine?
        # Path.match() matches from the right, so need to
        rpaths = (p for p in self.root.namelist() if p.startswith(self.at))
        rpaths = (p for p in rpaths if Path(p).match(glob))
        return (ZipPath(self.root, p) for p in rpaths)

    # TODO remove unused-ignore after 3.8
    def relative_to(self, other: ZipPath, *extra: str | os.PathLike[str]) -> Path:  # type: ignore[override,unused-ignore]
        assert self.filepath == other.filepath, (self.filepath, other.filepath)
        return self.subpath.relative_to(other.subpath, *extra)

    @property
    def parts(self) -> Sequence[str]:
        return self._parts

    @property
    def _parts(self) -> Sequence[str]:
        # a bit of an implementation detail, but sometimes it's used by pathlib
        # messy, but might be ok..
        return self.filepath.parts + self.subpath.parts

    @property
    def _raw_paths(self) -> Sequence[str]:
        # used in 3.12 for some operations
        return self._parts

    def __truediv__(self, key) -> ZipPath:
        # need to implement it so the return type is not zipfile.Path
        if isinstance(key, Path):
            # zipfile always uses / separator
            key = '/'.join(key.parts)
        tmp = zipfile.Path(self.root) / self.at / key
        return ZipPath(self.root, tmp.at)

    def iterdir(self) -> Iterator[ZipPath]:
        for s in self._as_dir().iterdir():
            yield ZipPath(s.root, s.at)  # type: ignore[attr-defined]

    @property
    def stem(self) -> str:
        return self.subpath.stem

    @property  # type: ignore[misc]
    def __class__(self):
        return Path

    def __eq__(self, other) -> bool:
        # hmm, super class doesn't seem to treat as equals unless they are the same object
        if not isinstance(other, ZipPath):
            return False
        return (self.filepath, self.subpath) == (other.filepath, other.subpath)

    def __lt__(self, other) -> bool:
        if not isinstance(other, ZipPath):
            return False
        return (self.filepath, self.subpath) < (other.filepath, other.subpath)

    def __hash__(self) -> int:
        return hash((self.filepath, self.subpath))

    def stat(self) -> os.stat_result:
        # NOTE: zip datetimes have no notion of time zone, usually they just keep local time?
        # see https://en.wikipedia.org/wiki/ZIP_(file_format)#Structure
        dt = datetime(*self.root.getinfo(self.at).date_time)
        ts = int(dt.timestamp())
        params = {
            'st_mode': 0,
            'st_ino': 0,
            'st_dev': 0,
            'st_nlink': 1,
            'st_uid': 1000,
            'st_gid': 1000,
            'st_size': 0,  # todo compute it properly?
            'st_atime': ts,
            'st_mtime': ts,
            'st_ctime': ts,
        }
        return os.stat_result(tuple(params.values()))

    @property
    def suffixes(self) -> list[str]:
        return Path(self.parts[-1]).suffixes

    @property
    def suffix(self) -> str:
        return Path(self.parts[-1]).suffix

    def walk(
        self,
        *,
        top_down: bool = True,
        on_error=None,
        follow_symlinks: bool = False,  # noqa: ARG002
    ) -> Iterator[tuple[ZipPath, list[str], list[str]]]:
        assert top_down, "specifying top_down isn't supported for zipfile.Path yet"
        assert on_error is None, "on_error isn't supported for zipfile.Path yet"

        at = self.at
        names = []
        for n in self.root.namelist():
            if not n.startswith(at):
                continue
            rest = n[len(at) :]
            if rest != '':
                # no need to append the subdir itself?
                names.append(rest)
        names.sort()

        # note: seems that zip always uses forward slash, regardless OS?
        for r, dirs, files in walk_paths(names, separator='/'):
            # make sure we don't construct ZipPath with at='.'... this behaves weird
            rr = self if r == '.' else self / r
            yield rr, dirs, files
