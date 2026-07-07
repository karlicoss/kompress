from __future__ import annotations

import io
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from .. import CPath

structure_data: Path = Path(__file__).parent / "structure_data"


ArchiveEntries = dict[str, bytes | None]


@pytest.fixture(params=['zip', 'tar.gz'])
def archive_factory(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[ArchiveEntries], Path]:
    suffix = request.param

    def make_archive(entries: ArchiveEntries) -> Path:
        target = tmp_path / f'archive.{suffix}'

        if suffix == 'zip':
            with zipfile.ZipFile(target, 'w') as z:
                for name, data in entries.items():
                    if data is None:
                        z.writestr(name.rstrip('/') + '/', '')
                    else:
                        z.writestr(name, data)
        elif suffix == 'tar.gz':
            with tarfile.open(target, 'w:gz') as t:
                for name, data in entries.items():
                    if data is None:
                        info = tarfile.TarInfo(name.rstrip('/'))
                        info.type = tarfile.DIRTYPE
                    else:
                        info = tarfile.TarInfo(name)
                        info.size = len(data)

                    info.mode = 0o755 if data is None else 0o644
                    if data is None:
                        t.addfile(info)
                    else:
                        t.addfile(info, io.BytesIO(data))
        else:
            raise AssertionError(suffix)

        assert target.exists()
        return CPath(target)

    return make_archive


@pytest.fixture(params=['zip', 'tar.gz'])
def gdpr_export(request: pytest.FixtureRequest) -> Path:
    target = structure_data / f'gdpr_export.{request.param}'
    assert target.exists(), target  # precondition
    return CPath(target)


def test_walk_empty(archive_factory: Callable[[ArchiveEntries], Path]) -> None:
    archive = archive_factory({})

    # this is consistent with pathlib.Path.walk over empty dir
    assert list(archive.walk()) == [
        (archive, [], []),
    ]


def test_walk_1(archive_factory: Callable[[ArchiveEntries], Path]) -> None:
    archive = archive_factory({
        'file2': b'data2',
        'file1': b'data2',
    })  # fmt: skip

    assert list(archive.walk()) == [
        (archive, [], ['file1', 'file2']),
    ]


def test_walk_2(archive_factory: Callable[[ArchiveEntries], Path]) -> None:
    archive = archive_factory({
        'empty_dir/' : None,
        'file'       : b'alala',
        'aaa/'       : None,
        'aaa/bbb'    : b'some_data',
        'aaa/ccc/'   : None,
        'aaa/ccc/ddd': b'some_data_2',
    })  # fmt: skip

    assert list(archive.walk()) == [
        (archive              , ['aaa', 'empty_dir'], ['file']),
        (archive / 'aaa'      , ['ccc']             , ['bbb']),
        (archive / 'aaa/ccc'  , []                  , ['ddd']),
        (archive / 'empty_dir', []                  , []),
    ]  # fmt: skip

    # testcase when we aren't starting from root
    assert list((archive / 'aaa').walk()) == [
        (archive / 'aaa'      , ['ccc']             , ['bbb']),
        (archive / 'aaa/ccc'  , []                  , ['ddd']),
    ]  # fmt: skip

    # check that .walk respects modifying dirs in-place, like regular pathlib
    all_files = []
    for _r, dirs, files in archive.walk():
        if 'ccc' in dirs:
            dirs.remove('ccc')
        all_files.extend(files)
    assert all_files == ['file', 'bbb']


def test_walk_gdpr_export(gdpr_export: Path) -> None:
    def _check_walk(z):
        for r, dirs, files in z.walk():
            assert isinstance(r, type(z))
            assert r.exists()
            yield r
            for d in dirs:
                assert (r / d).exists()
            for f in files:
                assert (r / f).exists()
                yield (r / f)

    results = list(_check_walk(gdpr_export))
    assert len(results) == 8


def test_parent_joinpath_glob(gdpr_export: Path) -> None:
    comments = gdpr_export / 'gdpr_export' / 'comments'
    archive_path_type = type(gdpr_export)

    # The archive root is still a real file on disk, so its parent is the containing directory.
    # Once inside the archive, parent/join/glob should preserve the archive path type.
    root_parent = gdpr_export.parent
    expected_root_parent = Path(str(gdpr_export)).parent
    assert type(root_parent) is type(expected_root_parent)
    assert root_parent == expected_root_parent

    assert comments.parent == gdpr_export / 'gdpr_export'
    assert isinstance(comments.parent, archive_path_type)

    joined = comments.joinpath('comments.json')
    assert joined == comments / 'comments.json'
    assert isinstance(joined, archive_path_type)

    matched = list(comments.glob('*.json'))
    assert matched == [comments / 'comments.json']
    assert all(isinstance(p, archive_path_type) for p in matched)


def test_stat_size(gdpr_export: Path) -> None:
    path = gdpr_export / 'gdpr_export' / 'messages' / 'index.csv'

    assert path.stat().st_size == len(path.read_bytes())
