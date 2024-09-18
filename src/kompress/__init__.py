from __future__ import annotations

import gzip
import io
import os
import pathlib
import sys
import tarfile
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


def _zstd_open(path: Path, *args, **kwargs) -> IO:  # noqa: ARG001
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


class CPath(Path):
    """
    Hacky way to support compressed files.
    If you can think of a better way to do this, please let me know! https://github.com/karlicoss/HPI/issues/20

    Ugh. So, can't override Path because of some _flavour thing.
    Path only has _accessor and _closed slots, so can't directly set .open method
    _accessor.open has to return file descriptor, doesn't work for compressed stuff.
    """

    if sys.version_info[:2] < (3, 12):
        # older version of python need _flavour defined
        _flavour = pathlib._windows_flavour if os.name == 'nt' else pathlib._posix_flavour  # type: ignore[attr-defined]

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

    def open(self, *args, **kwargs):  # noqa: ARG002
        kopen_kwargs = {}
        mode = kwargs.get('mode')
        if mode is not None:
            kopen_kwargs['mode'] = mode
        encoding = kwargs.get('encoding')
        if encoding is not None:
            kopen_kwargs['encoding'] = encoding
        # TODO assert read only?
        return _cpath_open(str(self), **kopen_kwargs)


def _cpath_open(path: Path | str, *args, mode: str = 'rt', **kwargs) -> IO:
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
    elif name.endswith((Ext.zstd, Ext.zst)):
        kwargs['mode'] = mode
        return _zstd_open(pp, *args, **kwargs)
    elif name.endswith(Ext.targz):
        # TODO don't think .tar.gz can be just a raw file? I think it's always sort of a directory (possibly with a single file)
        # TODO pass mode?
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
