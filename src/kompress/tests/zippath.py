from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from .. import ZipPath

structure_data: Path = Path(__file__).parent / "structure_data"


def test_walk_empty(tmp_path: Path) -> None:
    path = tmp_path / 'empty.zip'
    with ZipFile(path, 'w'):
        pass
    assert path.exists()
    zp = ZipPath(path)

    # this is consistent with pathlib.Path.walk over empty dir
    assert list(zp.walk()) == [
        (zp / '.', [], []),
    ]


def test_walk_1(tmp_path: Path) -> None:
    path = tmp_path / 'empty.zip'
    with ZipFile(path, 'w') as z:
        z.writestr('file2', 'data2')
        z.writestr('file1', 'data2')
    assert path.exists()
    zp = ZipPath(path)

    assert list(zp.walk()) == [
        (zp, [], ['file1', 'file2']),
    ]


def test_walk_2(tmp_path: Path) -> None:
    path = tmp_path / 'empty.zip'
    with ZipFile(path, 'w') as z:
        z.writestr('empty_dir/', '')
        z.writestr('file', 'alala')
        z.writestr('aaa/bbb', 'some_data')
        z.writestr('aaa/ccc/ddd', 'some_data_2')
    assert path.exists()
    zp = ZipPath(path)

    assert list(zp.walk()) == [
        # fmt: off
        (zp              , ['aaa', 'empty_dir'], ['file']),
        (zp / 'aaa'      , ['ccc']             , ['bbb']),
        (zp / 'aaa/ccc'  , []                  , ['ddd']),
        (zp / 'empty_dir', []                  , []),
        # fmt: on
    ]

    # testcase when we aren't starting from root
    assert list((zp / 'aaa').walk()) == [
        # fmt: off
        (zp / 'aaa'      , ['ccc']             , ['bbb']),
        (zp / 'aaa/ccc'  , []                  , ['ddd']),
        # fmt: on
    ]

    # check that .walk respects modifying dirs in-place, like regular pathlib
    all_files = []
    for _r, dirs, files in zp.walk():
        if 'ccc' in dirs:
            dirs.remove('ccc')
        all_files.extend(files)
    assert all_files == ['file', 'bbb']


def test_walk_gdpr_export() -> None:
    target = structure_data / 'gdpr_export.zip'
    assert target.exists(), target  # precondition

    zp = ZipPath(target)

    def _check_walk(z):
        for r, dirs, files in z.walk():
            assert r.exists()
            yield r
            for d in dirs:
                assert (r / d).exists()
            for f in files:
                assert (r / f).exists()
                yield (r / f)

    results = list(_check_walk(zp))
    assert len(results) == 8
