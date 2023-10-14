# TODO add a note that these were in HPI originally?

from __future__ import annotations

from datetime import datetime
from functools import total_ordering
import gzip
import io
import os
import pathlib
from pathlib import Path
import posixpath
import sys
import tarfile
from typing import (
    TYPE_CHECKING,
    Iterator,
    IO,
    List,
    Sequence,
    Union,
)
import zipfile


PathIsh = Union[Path, str]


class Ext:
    # fmt: off
    xz    = '.xz'
    zip   = '.zip'
    lz4   = '.lz4'
    zstd  = '.zstd'
    zst   = '.zst'
    targz = '.tar.gz'
    gz    = '.gz'
    # fmt: on


def is_compressed(p: PathIsh) -> bool:
    pp = p if isinstance(p, Path) else Path(p)
    # todo kinda lame way for now.. use mime ideally?
    # should cooperate with kompress.kopen?
    return any(pp.name.endswith(ext) for ext in {Ext.xz, Ext.zip, Ext.lz4, Ext.zstd, Ext.zst, Ext.targz, Ext.gz})


def _zstd_open(path: Path, *args, **kwargs) -> IO:
    import zstandard

    fh = path.open('rb')
    dctx = zstandard.ZstdDecompressor()
    reader = dctx.stream_reader(fh)

    mode = kwargs.get('mode', 'rt')
    if mode == 'rb':
        return reader
    else:
        # must be text mode
        kwargs.pop('mode')  # TextIOWrapper doesn't like it
        return io.TextIOWrapper(reader, **kwargs)  # meh


# TODO dunno, I guess it should be open and exists after all? similar to os.path
# TODO use the 'dependent type' trick for return type?
def kopen(path: PathIsh, *args, mode: str = 'rt', **kwargs) -> IO:
    # just in case, but I think this shouldn't be necessary anymore
    # since when we call .read_text, encoding is passed already
    if mode in {'r', 'rt'}:
        encoding = kwargs.get('encoding', 'utf8')
    else:
        encoding = None
    kwargs['encoding'] = encoding

    pp = Path(path)
    name = pp.name
    if name.endswith(Ext.xz):
        import lzma

        # ugh. for lzma, 'r' means 'rb'
        # https://github.com/python/cpython/blob/d01cf5072be5511595b6d0c35ace6c1b07716f8d/Lib/lzma.py#L97
        # whereas for regular open, 'r' means 'rt'
        # https://docs.python.org/3/library/functions.html#open
        if mode == 'r':
            mode = 'rt'
        kwargs['mode'] = mode
        return lzma.open(pp, *args, **kwargs)
    elif name.endswith(Ext.zip):
        zpath = ZipPath(pp)
        [subpath] = args  # meh?
        if sys.version_info[:2] == (3, 8):
            if mode == 'rt':
                mode = 'r'  # ugh, 3.8 doesn't support rt here
        # TODO pass **kwargs later to support encoding? kinda annoying, 3.8 doesn't support it
        return (zpath / subpath).open(mode=mode)
    elif name.endswith(Ext.lz4):
        import lz4.frame  # type: ignore

        return lz4.frame.open(str(pp), mode, *args, **kwargs)
    elif name.endswith(Ext.zstd) or name.endswith(Ext.zst):
        kwargs['mode'] = mode
        return _zstd_open(pp, *args, **kwargs)
    elif name.endswith(Ext.targz):
        # FIXME pass mode?
        tf = tarfile.open(pp)
        # TODO pass encoding?
        x = tf.extractfile(*args)
        assert x is not None
        return x
    elif name.endswith(Ext.gz):
        # for gzip 'r' means 'rb' returns a gzip.Gzipfile (in binary mode)
        # here, 'r' defaults to 'rt', to read as text
        #
        # https://docs.python.org/3/library/gzip.html#gzip.open
        #
        # if you supply mode 'rb', this *will* return bytes, but
        # sort of defeats the point of kopen
        if mode == 'r':
            mode = 'rt'

        kwargs['mode'] = mode

        # gzip does not support encoding in binary mode
        if 'b' in mode:
            del kwargs['encoding']

        # gzip.open already returns a io.TextIOWrapper if encoding is specified
        # and its not in binary mode
        return gzip.open(pp, *args, **kwargs)
    else:
        return pp.open(mode, *args, **kwargs)


if TYPE_CHECKING:
    # otherwise mypy can't figure out that BasePath is a type alias..
    BasePath = pathlib.Path
else:
    BasePath = pathlib.WindowsPath if os.name == 'nt' else pathlib.PosixPath


class CPath(BasePath):
    """
    Hacky way to support compressed files.
    If you can think of a better way to do this, please let me know! https://github.com/karlicoss/HPI/issues/20

    Ugh. So, can't override Path because of some _flavour thing.
    Path only has _accessor and _closed slots, so can't directly set .open method
    _accessor.open has to return file descriptor, doesn't work for compressed stuff.
    """

    def __new__(cls, *args, **kwargs):
        path = Path(*args)
        if path.name.endswith(Ext.zip):
            # We need a special case here, since zip always needs a subpath
            # If we just construct CPath(zip_archive) / "path/inside/zip"
            # , then it's hard for kopen to know if it's a zip without looking at individual path parts
            # This way it's a bit more explicit.
            # possibly useful for tar.gz as well?
            return ZipPath(path)
        return super().__new__(cls, *args, **kwargs)

    def open(self, *args, **kwargs):
        kopen_kwargs = {}
        mode = kwargs.get('mode')
        if mode is not None:
            kopen_kwargs['mode'] = mode
        encoding = kwargs.get('encoding')
        if encoding is not None:
            kopen_kwargs['encoding'] = encoding
        # TODO assert read only?
        return kopen(str(self), **kopen_kwargs)


open = kopen  # TODO deprecate


# meh
# TODO ideally switch to ZipPath or smth similar?
# nothing else supports subpath properly anyway
def kexists(path: PathIsh, subpath: str) -> bool:
    try:
        kopen(path, subpath)
        return True
    except Exception:
        return False


@total_ordering
class ZipPath(zipfile.Path):
    # NOTE: is_dir/is_file might not behave as expected, the base class checks it only based on the slash in path

    _flavour = posixpath  # this is necessary for some pathlib operations (in particular python 3.12)

    # seems that root/at are not exposed in the docs, so might be an implementation detail
    root: zipfile.ZipFile
    at: str

    def __new__(cls, *args, **kwargs):
        if len(args) == 1 and isinstance(args[0], ZipPath):
            # hack to make sure ZipPath(ZipPath(...)) works
            zp = args[0]
            return zipfile.Path(zp.filepath, zp.at)
        return super().__new__(cls)

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

    def _as_dir(self) -> zipfile.Path:
        # note: seems that zip always uses forward slash, regardless OS?
        return zipfile.Path(self.root, self.at + '/')

    def rglob(self, glob: str) -> Sequence[ZipPath]:
        # note: not 100% sure about the correctness, but seem fine?
        # Path.match() matches from the right, so need to
        rpaths = [p for p in self.root.namelist() if p.startswith(self.at)]
        rpaths = [p for p in rpaths if Path(p).match(glob)]
        return [ZipPath(self.root, p) for p in rpaths]

    def relative_to(self, other: ZipPath) -> Path:
        assert self.filepath == other.filepath, (self.filepath, other.filepath)
        return self.subpath.relative_to(other.subpath)

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
        params = dict(
            st_mode=0,
            st_ino=0,
            st_dev=0,
            st_nlink=1,
            st_uid=1000,
            st_gid=1000,
            st_size=0,  # todo compute it properly?
            st_atime=ts,
            st_mtime=ts,
            st_ctime=ts,
        )
        return os.stat_result(tuple(params.values()))

    @property
    def suffixes(self) -> List[str]:
        return Path(self.parts[-1]).suffixes

    @property
    def suffix(self) -> str:
        return Path(self.parts[-1]).suffix
