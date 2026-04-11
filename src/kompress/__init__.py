from __future__ import annotations

import gzip
import io
import sys
import warnings
from pathlib import Path
from typing import IO

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
    return pp.name.endswith((Ext.xz, Ext.zip, Ext.lz4, Ext.zstd, Ext.zst, Ext.targz, Ext.gz))


class CPath(Path):
    """
    Hacky way to support compressed files.
    If you can think of a better way to do this, please let me know! https://github.com/karlicoss/HPI/issues/20
    """

    def __new__(cls, *args, **kwargs):
        # TODO shortcut if args[0] is already Cpath?

        path = Path(*args)
        if path.name.endswith(Ext.zip):
            if path.exists():
                # if path doesn't exist, zipfile can't open it to read the index etc
                # so it's the best we can do in this case?
                # TODO move this into ZipPath.__new__?
                return ZipPath(path)
        if path.name.endswith(Ext.targz):
            return TarPath(path)
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

    # most compressed-file libraries treat 'r' as 'rb', unlike pathlib where 'r' means 'rt'
    # normalize once here so each branch doesn't have to repeat it
    if mode == 'r' and is_compressed(name):
        mode = 'rt'

    if name.endswith((Ext.zstd, Ext.zst)):
        if sys.version_info[:2] >= (3, 14):
            from compression import zstd

            return zstd.open(path, mode=mode, **kwargs)  # type: ignore[call-overload]
        else:
            # see https://github.com/astral-sh/ty/issues/2681 about multiple unused-ignore-comment...
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

        return lzma.open(pp, mode=mode, **kwargs)
    elif name.endswith(Ext.lz4):
        import lz4.frame  # type: ignore[import-untyped]

        return lz4.frame.open(str(pp), mode=mode, **kwargs)
    elif name.endswith(Ext.gz):
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


