import gzip
import io
import lzma
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from .. import CPath, Ext, is_compressed

structure_data: Path = Path(__file__).parent / "structure_data"


@pytest.mark.parametrize(
    ('filename', 'expected'),
    [
        ('file'    , 'just plaintext'),
        ('file.xz' , 'compressed text'),
        ('file.zst', 'compressed text'),
        ('file.gz' , 'compressed text'),
        ('file.lz4', 'compressed text'),
    ],
)  # fmt: skip
def test_cpath_regular(filename: str, expected: str, tmp_path: Path) -> None:
    """
    Check different ways of interacting with CPath
    """
    path = tmp_path / filename

    with CPath(path).open() as fo:
        assert fo.read() == expected

    with CPath(path).open(mode='rt') as fo:
        assert fo.read() == expected

    with CPath(path).open('r') as fo:
        assert fo.read() == expected

    with CPath(path).open('r', encoding='utf8') as fo:
        assert fo.read() == expected

    with CPath(path).open('rb') as fo:
        assert fo.read() == expected.encode('ascii')

    assert CPath(path).read_text() == expected
    assert CPath(path).read_bytes() == expected.encode('ascii')

    for args in [
        [str(path)],
        [path],
        [CPath(path)],
        path.parts,
    ]:
        Path(*args)  # type: ignore[misc] # just a sanity check that regular Path can be constructed this way
        assert CPath(*args).read_text() == expected  # type: ignore[misc]


def test_gz(tmp_path: Path) -> None:
    gzf = tmp_path / 'file.gz'
    with gzip.open(gzf, 'wb') as f:
        f.write(b'compressed text')

    # test against gzip magic number
    assert gzf.read_bytes()[:2] == b'\x1f\x8b'

    with CPath(gzf).open() as f:
        assert hasattr(f, 'read')
        assert hasattr(f, 'readable')
        assert f.readable()
        assert not f.writable()
        assert f.read() == 'compressed text'  # if not specified, defaults to rt

    with CPath(gzf).open(mode='rb') as f:
        assert isinstance(f, gzip.GzipFile)
        assert f.read() == b'compressed text'

    # should return text
    with CPath(gzf).open(mode='r') as f:
        assert isinstance(f, io.TextIOWrapper)
        assert f.read() == 'compressed text'

    # if you specify, rt, does what you expect
    with CPath(gzf).open(mode='rt') as f:
        assert isinstance(f, io.TextIOWrapper)
        assert f.read() == 'compressed text'

    assert CPath(gzf).read_text() == 'compressed text'
    assert CPath(gzf).read_bytes() == b'compressed text'


def test_kopen_kexists(tmp_path: Path) -> None:
    """
    Testing deprecations, can remove when we remove kexists/kopen
    """
    from .. import kexists, kopen  # type: ignore[attr-defined]  # ty: ignore[unresolved-import]

    path = Path(tmp_path / 'file.zip')

    read_res = kopen(path, 'path', 'in', 'archive').read()
    assert read_res == 'data in zip'
    assert kexists(path, 'path/in/archive')
    assert not kexists(path, 'does/not/exist')


@pytest.fixture(autouse=True)
def prepare_data(tmp_path: Path):
    compressed_text = b'compressed text'

    (tmp_path / 'file').write_text('just plaintext')

    # xz
    with (tmp_path / 'file.xz').open('wb') as f:
        with lzma.open(f, 'w') as lzf:
            lzf.write(compressed_text)

    # zstd
    if sys.version_info >= (3, 14):
        from compression import zstd

        for suffix in [Ext.zst, Ext.zstd]:
            with zstd.open(tmp_path / f'file{suffix}', 'wb') as f:
                f.write(compressed_text)
    else:
        # ty checks this branch on Python 3.14 even though zstandard is only installed on <3.14.
        # The duplicated unused-ignore-comment keeps this valid both when unresolved-import is used and unused.
        # See https://github.com/astral-sh/ty/issues/2681
        import zstandard as zstd  # ty: ignore[unresolved-import,unused-ignore-comment,unused-ignore-comment]

        zst_ctx = zstd.ZstdCompressor()
        for suffix in [Ext.zst, Ext.zstd]:
            (tmp_path / f'file{suffix}').write_bytes(zst_ctx.compress(compressed_text))

    # gz
    gzf = tmp_path / 'file.gz'
    with gzip.open(gzf, 'wb') as f:
        f.write(compressed_text)

    # lz4
    import lz4.frame  # type: ignore[import-untyped]

    (tmp_path / 'file.lz4').write_bytes(lz4.frame.compress(compressed_text))

    with zipfile.ZipFile(tmp_path / 'file.zip', 'w') as zf:
        zf.writestr('path/in/archive', 'data in zip')

    with tarfile.open(tmp_path / 'file.tar.gz', 'w:gz') as tf:
        info = tarfile.TarInfo('file')
        info.size = len(compressed_text)
        tf.addfile(info, io.BytesIO(compressed_text))

    # make sure all supported extensions are covered by test
    for name, suffix in vars(Ext).items():
        if name.startswith('_') or not isinstance(suffix, str):
            continue
        path = tmp_path / f'file{suffix}'
        assert path.exists(), suffix
        assert is_compressed(path)

    assert not is_compressed(tmp_path / 'file')
