import io
import gzip
import lzma
from pathlib import Path
import zipfile
import sys

import pytest

from .. import kopen, kexists, CPath, ZipPath


structure_data: Path = Path(__file__).parent / "structure_data"


def test_kopen(tmp_path: Path) -> None:
    "Plaintext handled transparently"
    # fmt: off
    assert kopen(tmp_path / 'file'   ).read() == 'just plaintext'
    assert kopen(tmp_path / 'file.xz').read() == 'compressed text'
    # fmt: on


def test_zip(tmp_path: Path) -> None:
    # zips always contain a file inside it, so require a bit of a special handling
    # e.g. need to pass a 'subpath' into kopen
    subpath = 'path/in/archive'

    if sys.version_info[:2] == (3, 8):
        # seems that zippath used to return bytes in 3.8
        assert kopen(tmp_path / 'file.zip', subpath).read() == b'data in zip'
    else:
        assert kopen(tmp_path / 'file.zip', subpath).read() == 'data in zip'

    # CPath should dispatch zips to ZipPath
    cpath = CPath(tmp_path / 'file.zip')
    assert isinstance(cpath, ZipPath)

    assert (cpath / subpath).read_text() == 'data in zip'

    # make sure construction from parts works as expected
    assert isinstance(CPath(*cpath.parts), ZipPath)

    assert isinstance(CPath(cpath), ZipPath)


def test_kexists(tmp_path: Path) -> None:
    # TODO also test top level?
    # fmt: off
    assert     kexists(str(tmp_path / 'file.zip'), 'path/in/archive')
    assert not kexists(str(tmp_path / 'file.zip'), 'path/notin/archive')
    # fmt: on

    # TODO not sure about this?
    assert not kexists(tmp_path / 'nosuchzip.zip', 'path/in/archive')


@pytest.mark.parametrize(
    'file,expected',
    [
        ('file', 'just plaintext'),
        ('file.xz', 'compressed text'),
    ],
)
def test_cpath(file: str, expected: str, tmp_path: Path) -> None:
    # check different ways of constructing the path
    path = tmp_path / file
    for args in [
        [str(path)],
        [path],
        [CPath(path)],
        path.parts,
    ]:
        Path(*args)  # type: ignore[misc] # just a sanity check that regular Path can be constructed this way
        CPath(*args).read_text() == expected  # type: ignore[misc]


@pytest.fixture(autouse=True)
def prepare(tmp_path: Path):
    (tmp_path / 'file').write_text('just plaintext')
    with (tmp_path / 'file.xz').open('wb') as f:
        with lzma.open(f, 'w') as lzf:
            lzf.write(b'compressed text')
    with zipfile.ZipFile(tmp_path / 'file.zip', 'w') as zf:
        zf.writestr('path/in/archive', 'data in zip')
    try:
        yield None
    finally:
        pass


def test_zippath(tmp_path: Path) -> None:
    zp = ZipPath(tmp_path / 'file.zip', 'path/in/archive')

    assert zp.read_text() == 'data in zip'

    if sys.version_info[:2] == (3, 8):
        assert zp.open(mode='r').read() == b'data in zip'
    else:
        assert zp.open(mode='rb').read() == b'data in zip'

        assert zp.open(mode='r').read() == 'data in zip'  # type: ignore[comparison-overlap]
        # 3.8 didn't support rt
        assert zp.open(mode='rt').read() == 'data in zip'  # type: ignore[comparison-overlap,arg-type]

    target = structure_data / 'gdpr_export.zip'
    assert target.exists(), target  # precondition

    zp = ZipPath(target)

    ZipPath(zp)  # make sure it doesn't crash

    # magic! convenient to make third party libraries agnostic of ZipPath
    assert isinstance(zp, Path)
    assert isinstance(zp, ZipPath)
    assert isinstance(zp / 'subpath', Path)
    # TODO maybe change __str__/__repr__? since it's a bit misleading:
    # Path('/code/hpi/tests/core/structure_data/gdpr_export.zip', 'gdpr_export/')

    assert ZipPath(target) == ZipPath(target)
    assert zp.absolute() == zp

    # shouldn't crash
    hash(zp)

    assert zp.exists()
    assert (zp / 'gdpr_export' / 'comments').exists()
    # check str constructor just in case
    assert (ZipPath(str(target)) / 'gdpr_export' / 'comments').exists()
    assert not (ZipPath(str(target)) / 'whatever').exists()

    matched = list(zp.rglob('*'))
    assert len(matched) > 0
    assert all(p.filepath == target for p in matched), matched

    rpaths = [p.relative_to(zp) for p in matched]
    gdpr_export = Path('gdpr_export')
    # fmt: off
    assert rpaths == [
        gdpr_export,
        gdpr_export / 'comments',
        gdpr_export / 'comments' / 'comments.json',
        gdpr_export / 'profile',
        gdpr_export / 'profile' / 'settings.json',
        gdpr_export / 'messages',
        gdpr_export / 'messages' / 'index.csv',
    ], rpaths
    # fmt: on

    # TODO hmm this doesn't work atm, whereas Path does
    # not sure if it should be defensive or something...
    # ZipPath('doesnotexist')
    # same for this one
    # assert ZipPath(Path('test'), 'whatever').absolute() == ZipPath(Path('test').absolute(), 'whatever')

    assert (ZipPath(target) / 'gdpr_export' / 'comments').exists()

    jsons = [p.relative_to(zp / 'gdpr_export') for p in zp.rglob('*.json')]
    # fmt: off
    assert jsons == [
        Path('comments', 'comments.json'),
        Path('profile' , 'settings.json'),
    ]
    # fmt: on

    # NOTE: hmm interesting, seems that ZipPath is happy with forward slash regardless OS?
    assert list(zp.rglob('mes*')) == [ZipPath(target, 'gdpr_export/messages')]

    iterdir_res = list((zp / 'gdpr_export').iterdir())
    assert len(iterdir_res) == 3
    assert all(isinstance(p, Path) for p in iterdir_res)

    # date recorded in the zip archive
    assert (zp / 'gdpr_export' / 'comments' / 'comments.json').stat().st_mtime > 1625000000
    # TODO ugh.
    # unzip -l shows the date  as 2021-07-01 09:43
    # however, python reads it as 2021-07-01 01:43 ??
    # don't really feel like dealing with this for now, it's not tz aware anyway

    json_gz = zp / 'gdpr_export' / 'comments' / 'comments.json.gz'
    assert json_gz.suffixes == ['.json', '.gz']
    assert json_gz.suffix == '.gz'


def test_gz(tmp_path: Path) -> None:
    gzf = tmp_path / 'file.gz'
    with gzip.open(gzf, 'wb') as f:
        f.write(b'compressed text')

    # test against gzip magic number
    assert gzf.read_bytes()[:2] == b'\x1f\x8b'

    with kopen(gzf) as f:
        assert hasattr(f, 'read')
        assert hasattr(f, 'readable')
        assert f.readable()
        assert not f.writable()
        assert f.read() == 'compressed text'  # if not specified, defaults to rt

    with kopen(gzf, mode='rb') as f:
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
