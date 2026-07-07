from __future__ import annotations

import gzip
import io
import sys
import warnings
from pathlib import Path
from typing import IO, TYPE_CHECKING

from .tar import TarPath
from .zip import ZipPath


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


def is_compressed(p: Path | str) -> bool:
    pp = p if isinstance(p, Path) else Path(p)
    # todo kinda lame way for now.. use mime ideally?
    # should cooperate with kompress.kopen?
    return pp.name.endswith((Ext.xz, Ext.zip, Ext.lz4, Ext.zstd, Ext.zst, Ext.targz, Ext.gz))


class CPath(Path):
    """
    Hacky way to support compressed files.
    If you can think of a better way to do this, please let me know! https://github.com/karlicoss/HPI/issues/20

    Ugh. So, can't override Path because of some _flavour thing.
    Path only has _accessor and _closed slots, so can't directly set .open method
    _accessor.open has to return file descriptor, doesn't work for compressed stuff.
    """

    def __new__(cls, *args, **kwargs):
        # TODO shortcut if args[0] is already Cpath?

        path = Path(*args)
        # Missing archive wrappers fall back to plain Path; CPath should stay CPath in that case.
        if path.name.endswith(Ext.zip):
            zip_path = ZipPath(path)
            if isinstance(zip_path, ZipPath):
                return zip_path
        if path.name.endswith(Ext.targz):
            tar_path = TarPath(path)
            if isinstance(tar_path, TarPath):
                return tar_path
        return super().__new__(cls, *args, **kwargs)

    def open(  # type: ignore[override]  # ty: ignore[invalid-method-override]
        self,
        mode: str = 'r',
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        **kwargs,
    ):
        if buffering not in {-1, 0}:
            # buffering is unsupported by most compressed formats
            # -1 is 'default', 0 means 'buffering off' (pathlib passes it since 3.14)
            warnings.warn(f"while opening {self}: CPath doesn't support buffering", stacklevel=2)
        # simply forward the rest of positional args
        kwargs['encoding'] = encoding
        kwargs['errors'] = errors
        return _cpath_open(
            path=str(self),
            mode=mode,
            **kwargs,
        )


def _cpath_open(*, path: Path | str, mode: str, **kwargs) -> IO:
    if 'w' in mode:
        raise RuntimeError(f"Tring to open {path} in {mode=}. CPath only supports reading.")

    pp = Path(path)
    name = pp.name
    if name.endswith((Ext.zstd, Ext.zst)):
        if sys.version_info[:2] >= (3, 14):
            from compression import zstd

            # ugh. default r for zstd is rb
            # see https://docs.python.org/3.15/library/compression.zstd.html#compression.zstd.open
            if mode == 'r':
                mode = 'rt'

            return zstd.open(path, mode=mode, **kwargs)  # type: ignore[call-overload]
        else:
            # ty checks this branch on Python 3.14 even though zstandard is only installed on <3.14.
            # The duplicated unused-ignore-comment keeps this valid both when unresolved-import is used and unused.
            # See https://github.com/astral-sh/ty/issues/2681
            import zstandard as zstd  # ty: ignore[unresolved-import,unused-ignore-comment,unused-ignore-comment]

            fh = pp.open('rb')
            dctx = zstd.ZstdDecompressor()
            reader = dctx.stream_reader(fh)

            if mode == 'rb':
                return reader
            else:
                # must be text mode
                # NOTE: no need to pass mode, TextIOWrapper doesn't like it
                return io.TextIOWrapper(reader, **kwargs)  # meh
    elif name.endswith(Ext.xz):
        import lzma

        # for lzma, 'r' means 'rb'
        # https://github.com/python/cpython/blob/d01cf5072be5511595b6d0c35ace6c1b07716f8d/Lib/lzma.py#L97
        # whereas for Path.open, 'r' means 'rt'
        if mode == 'r':
            mode = 'rt'
        return lzma.open(pp, mode=mode, **kwargs)
    elif name.endswith(Ext.lz4):
        import lz4.frame  # type: ignore[import-untyped]

        if mode == 'r':
            # lz4 uses rb by default
            # whereas for Path.open, 'r' means 'rt'
            mode = 'rt'

        return lz4.frame.open(str(pp), mode=mode, **kwargs)
    elif name.endswith(Ext.gz):
        if mode == 'r':
            # for gzip 'r' means 'rb' returns a gzip.Gzipfile (in binary mode)
            # whereas for Path.open, 'r' means 'rt'
            mode = 'rt'

        # gzip does not support encoding in binary mode
        # TODO tbh, open() docs are saying encoding shouldn't be passed in binary mode, not sure what this was for?
        if 'b' in mode:
            del kwargs['encoding']

        # gzip.open already returns a io.TextIOWrapper if encoding is specified
        # and its not in binary mode
        return gzip.open(pp, mode=mode, **kwargs)  # type: ignore[return-value]  # ty: ignore[invalid-return-type]
    elif name.endswith(Ext.zip):
        # this should be handled by ZipPath (see CPath.__new__)
        raise RuntimeError("shouldn't happen")
    elif name.endswith(Ext.targz):
        # this should be handled by TarPath (see CPath.__new__)
        raise RuntimeError("shouldn't happen")
    else:
        return pp.open(mode=mode, **kwargs)


if not TYPE_CHECKING:
    # FIXME deprecate properly
    # still used in promnesia legacy takeout module? could migrate off
    # ah ok, promnesia works off my.core.kompress (which is itself deprecated)
    # so we could perhaps add kopen/kexists adapters that just do Cpath(first_arg) / Path(rest)?
    # pass kwargs to open? like mode/encoding

    from .compat import deprecated

    @deprecated('use Cpath(...).open() instead')
    def kopen(path, *args, **kwargs):
        cpath = CPath(path) / Path(*args)
        return cpath.open(**kwargs)

    @deprecated('use Cpath(...).open() instead')
    def open(*args, **kwargs):  # noqa: A001
        return kopen(*args, **kwargs)

    @deprecated('use Cpath(...).exists() instead')
    def kexists(path, *args) -> bool:
        cpath = CPath(path) / Path(*args)
        return cpath.exists()
