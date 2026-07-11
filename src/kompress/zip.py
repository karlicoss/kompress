from __future__ import annotations

import os
import posixpath  # zip internal paths always use forward slashes
import sys
import warnings
import zipfile
from collections.abc import Iterator, Sequence
from datetime import datetime
from functools import total_ordering
from pathlib import Path
from typing import Self

from .utils import archive_glob, walk_paths


def _without_dot_segments(at: str) -> str:
    if at in {'', '.'}:
        return ''

    trailing_slash = at.endswith('/')
    parts = [part for part in at.split('/') if part not in {'', '.'}]
    normalized = '/'.join(parts)
    if trailing_slash and normalized != '':
        normalized += '/'
    return normalized


@total_ordering
class ZipPath(zipfile.Path):
    # NOTE: is_dir/is_file might not behave as expected, the base class checks it only based on the slash in path
    # TODO: maybe change __str__/__repr__; inherited zipfile.Path output is misleading here,
    # e.g. Path('.../gdpr_export.zip', '') and a trailing slash for the archive root.

    _flavour = os.path  # this is necessary for some pathlib operations (in particular python 3.12)
    parser = os.path  # same but for 3.13

    # seems that root/at are not exposed in the docs, so might be an implementation detail
    root: zipfile.CompleteDirs
    at: str

    def __new__(cls, root: str | Path | zipfile.ZipFile | ZipPath, at: str = "") -> Self:
        if isinstance(root, ZipPath | zipfile.ZipFile):
            return super().__new__(cls)

        path = Path(root)
        if not path.exists():
            return path / at  # type: ignore[return-value]  # ty: ignore[invalid-return-type]

        return super().__new__(cls)

    def __init__(self, root: str | Path | zipfile.ZipFile | ZipPath, at: str = "") -> None:
        root_: str | Path | zipfile.ZipFile
        if isinstance(root, ZipPath):
            # hack to make sure ZipPath(ZipPath(...)) works
            root_ = root.root
            at_ = root.at
        else:
            root_ = root
            at_ = _without_dot_segments(at)

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

    def _next(self, at: str) -> ZipPath:
        return ZipPath(self.root, at)

    def glob(self, pattern: str | os.PathLike[str], **kwargs) -> Iterator[ZipPath]:
        yield from archive_glob(self, pattern, recursive=False, **kwargs)

    def rglob(self, pattern: str | os.PathLike[str], **kwargs) -> Iterator[ZipPath]:
        yield from archive_glob(self, pattern, recursive=True, **kwargs)

    def relative_to(self, other: ZipPath, *extra: str | os.PathLike[str]) -> Path:  # type: ignore[override]  # ty: ignore[invalid-method-override]
        assert self.filepath == other.filepath, (self.filepath, other.filepath)
        return self.subpath.relative_to(other.subpath.joinpath(*extra))

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

    def __truediv__(self, add) -> ZipPath:
        # need to implement it so the return type is not zipfile.Path
        if isinstance(add, Path):
            # zipfile always uses / separator
            add = '/'.join(add.parts)
        tmp = zipfile.Path(self.root) / self.at / add
        return ZipPath(self.root, tmp.at)

    def iterdir(self) -> Iterator[ZipPath]:
        for s in self._as_dir().iterdir():
            yield ZipPath(s.root, s.at)

    @property
    def stem(self) -> str:
        return self.subpath.stem

    @property
    def parent(self):
        if self.at == '':
            return self.filepath.parent

        parent_at = posixpath.dirname(self.at.rstrip('/'))
        if parent_at != '':
            parent_at += '/'
        return self._next(parent_at)

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
        info = self.root.getinfo(self.at)
        date_time = info.date_time
        if date_time[0] == 1980:
            # This is the min/default date in zip (or at least in python wrapper)
            # Most likely this means that the zip doesn't contain actual datetime info in "central directory"
            #  , but instead uses some OS extension ("Extended Timestamp"?)
            # In particular, that started happening to google takeouts sinse Feb 2024
            # Doesn't look like these are supported for python at the moment, see https://github.com/python/cpython/issues/49707
            # So the best we can do in that case (which still might be wrong) is taking the archive's timestamps.
            archive_stat = self.filepath.stat()
            atime = archive_stat.st_atime
            mtime = archive_stat.st_mtime
            if sys.platform == 'win32':
                ctime = archive_stat.st_birthtime
            else:
                ctime = archive_stat.st_ctime
        else:
            # TODO: prefer the UT extra field when present, to better match an extracted regular file.
            # For gdpr_export/comments/comments.json, unzip -l reports 2021-07-01 09:43 from that
            # extra field, while zipfile exposes the central-directory DOS timestamp as 01:43.
            dt = datetime(*info.date_time)
            ts = int(dt.timestamp())
            atime = ts
            mtime = ts
            ctime = ts
        params = {
            'st_mode': 0,
            'st_ino': 0,
            'st_dev': 0,
            'st_nlink': 1,
            'st_uid': 1000,
            'st_gid': 1000,
            'st_size': info.file_size,
            'st_atime': atime,
            'st_mtime': mtime,
            'st_ctime': ctime,
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
