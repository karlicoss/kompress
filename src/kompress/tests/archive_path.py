from __future__ import annotations

import io
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from .. import CPath, TarPath, ZipPath

structure_data: Path = Path(__file__).parent / "structure_data"


ArchiveEntries = dict[str, bytes | None]


GDPR_EXPORT_ENTRIES: ArchiveEntries = {
    'gdpr_export/'                       : None,
    'gdpr_export/comments/'              : None,
    'gdpr_export/comments/comments.json' : b'',
    'gdpr_export/profile/'               : None,
    'gdpr_export/profile/settings.json'  : b'',
    'gdpr_export/messages/'              : None,
    'gdpr_export/messages/index.csv'     : b'test message\n',
}  # fmt: skip


def _write_directory(target: Path, entries: ArchiveEntries) -> Path:
    target.mkdir()
    for name, data in entries.items():
        path = target / name
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return target


def _normalized_walk(root: Path) -> list[tuple[Path, list[str], list[str]]]:
    """
    Compare walk output across backends without depending on absolute temp paths or filesystem ordering.
    """
    return sorted((r.relative_to(root), sorted(dirs), sorted(files)) for r, dirs, files in root.walk())


@pytest.fixture(params=['directory', 'zip', 'tar.gz'])
def path_factory(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[ArchiveEntries], Path]:
    kind = request.param

    def make_path(entries: ArchiveEntries) -> Path:
        if kind == 'directory':
            return _write_directory(tmp_path / 'directory', entries)

        target = tmp_path / f'archive.{kind}'

        if kind == 'zip':
            with zipfile.ZipFile(target, 'w') as z:
                for name, data in entries.items():
                    if data is None:
                        z.writestr(name.rstrip('/') + '/', '')
                    else:
                        z.writestr(name, data)
        elif kind == 'tar.gz':
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
            raise AssertionError(kind)

        assert target.exists()
        return CPath(target)

    return make_path


@pytest.fixture(params=['directory', 'zip', 'tar.gz'])
def gdpr_export(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    if request.param == 'directory':
        return _write_directory(tmp_path / 'gdpr_export_directory', GDPR_EXPORT_ENTRIES)

    target = structure_data / f'gdpr_export.{request.param}'
    assert target.exists(), target  # precondition
    return CPath(target)


def test_walk_empty(path_factory: Callable[[ArchiveEntries], Path]) -> None:
    archive = path_factory({})

    # this is consistent with pathlib.Path.walk over empty dir
    assert _normalized_walk(archive) == [
        (Path(), [], []),
    ]


def test_walk_1(path_factory: Callable[[ArchiveEntries], Path]) -> None:
    archive = path_factory({
        'file2': b'data2',
        'file1': b'data2',
    })  # fmt: skip

    assert _normalized_walk(archive) == [
        (Path(), [], ['file1', 'file2']),
    ]


def test_walk_2(path_factory: Callable[[ArchiveEntries], Path]) -> None:
    archive = path_factory({
        'empty_dir/' : None,
        'file'       : b'alala',
        'aaa/'       : None,
        'aaa/bbb'    : b'some_data',
        'aaa/ccc/'   : None,
        'aaa/ccc/ddd': b'some_data_2',
    })  # fmt: skip

    assert _normalized_walk(archive) == [
        (Path()           , ['aaa', 'empty_dir'], ['file']),
        (Path('aaa')      , ['ccc']             , ['bbb']),
        (Path('aaa/ccc')  , []                  , ['ddd']),
        (Path('empty_dir'), []                  , []),
    ]  # fmt: skip

    # testcase when we aren't starting from root
    assert _normalized_walk(archive / 'aaa') == [
        (Path()         , ['ccc'], ['bbb']),
        (Path('ccc')    , []     , ['ddd']),
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

    # The root path still has normal pathlib parent semantics: its parent is the containing directory.
    # Once inside that root, parent/join/glob should preserve the path type.
    root_parent = gdpr_export.parent
    expected_root_parent = Path(str(gdpr_export)).parent
    assert root_parent == expected_root_parent

    assert comments.parent == gdpr_export / 'gdpr_export'
    assert isinstance(comments.parent, archive_path_type)

    joined = comments.joinpath('comments.json')
    assert joined == comments / 'comments.json'
    assert isinstance(joined, archive_path_type)

    matched = list(comments.glob('*.json'))
    assert matched == [comments / 'comments.json']
    assert all(isinstance(p, archive_path_type) for p in matched)


def test_file_type_methods(gdpr_export: Path) -> None:
    assert gdpr_export.exists()
    assert gdpr_export.is_dir()
    assert not gdpr_export.is_file()

    directory = gdpr_export / 'gdpr_export' / 'comments'
    assert directory.exists()
    assert directory.is_dir()
    assert not directory.is_file()

    file = gdpr_export / 'gdpr_export' / 'messages' / 'index.csv'
    assert file.exists()
    assert file.is_file()
    assert not file.is_dir()

    missing = gdpr_export / 'missing'
    assert not missing.exists()
    assert not missing.is_dir()
    assert not missing.is_file()


def test_rglob_relative_to_iterdir(gdpr_export: Path) -> None:
    archive_path_type = type(gdpr_export)

    jsons = [p.relative_to(gdpr_export / 'gdpr_export') for p in gdpr_export.rglob('*.json')]
    assert jsons == [
        Path('comments', 'comments.json'),
        Path('profile', 'settings.json'),
    ]

    assert (gdpr_export / 'gdpr_export' / 'comments' / 'comments.json').relative_to(
        gdpr_export / 'gdpr_export',
    ) == Path(
        'comments',
        'comments.json',
    )

    assert list(gdpr_export.rglob('mes*')) == [gdpr_export / 'gdpr_export' / 'messages']

    iterdir_res = sorted((gdpr_export / 'gdpr_export').iterdir())
    assert [p.name for p in iterdir_res] == ['comments', 'messages', 'profile']
    assert all(isinstance(p, archive_path_type) for p in iterdir_res)


@pytest.mark.parametrize(
    ('pattern', 'expected'),
    [
        (
            '*',
            [
                'root',
                'root/aaa',
                'root/aaa/bbb.txt',
                'root/aaa/ccc',
                'root/aaa/ccc/ddd.json',
                'root/empty_dir',
                'root/file.txt',
                'root/messages',
                'root/messages/index.csv',
                'root/profile',
                'root/profile/settings.json',
            ],
        ),
        (
            '*.json',
            [
                'root/aaa/ccc/ddd.json',
                'root/profile/settings.json',
            ],
        ),
        (
            'mes*',
            [
                'root/messages',
            ],
        ),
        (
            '*/index.csv',
            [
                'root/messages/index.csv',
            ],
        ),
        (
            'aaa/*',
            [
                'root/aaa/bbb.txt',
                'root/aaa/ccc',
            ],
        ),
        (
            '**/*.json',
            [
                'root/aaa/ccc/ddd.json',
                'root/profile/settings.json',
            ],
        ),
        (
            '*/ccc/*',
            [
                'root/aaa/ccc/ddd.json',
            ],
        ),
    ],
)
def test_rglob_patterns(
    path_factory: Callable[[ArchiveEntries], Path],
    pattern: str,
    expected: list[str],
) -> None:
    entries: ArchiveEntries = {
        'root/'                       : None,
        'root/empty_dir/'             : None,
        'root/file.txt'               : b'file',
        'root/aaa/'                   : None,
        'root/aaa/bbb.txt'            : b'bbb',
        'root/aaa/ccc/'               : None,
        'root/aaa/ccc/ddd.json'       : b'{}',
        'root/messages/'              : None,
        'root/messages/index.csv'     : b'index',
        'root/profile/'               : None,
        'root/profile/settings.json'  : b'{}',
    }  # fmt: skip
    root = path_factory(entries)

    assert sorted(p.relative_to(root) for p in root.rglob(pattern)) == [Path(p) for p in expected]


@pytest.mark.parametrize('filename', ['missing.zip', 'missing.tar.gz'])
def test_missing_archive_is_cpath(tmp_path: Path, filename: str) -> None:
    target = tmp_path / filename

    path = CPath(target)

    assert not target.exists()
    assert isinstance(path, CPath)
    assert not path.exists()


@pytest.mark.parametrize(
    ('path_type', 'filename'),
    [
        (ZipPath, 'missing.zip'),
        (TarPath, 'missing.tar.gz'),
    ],
)
def test_missing_archive_path_falls_back_to_regular_path(
    tmp_path: Path,
    path_type: type[Path],
    filename: str,
) -> None:
    target = tmp_path / filename

    path = path_type(target)

    assert not target.exists()
    assert type(path) is type(tmp_path / filename)
    assert not path.exists()


def test_missing_zippath_with_member_falls_back_to_regular_path(tmp_path: Path) -> None:
    target = tmp_path / 'missing.zip'

    path = cast(Path, ZipPath(target, 'member.txt'))

    assert not target.exists()
    assert type(path) is type(tmp_path)
    assert path == target / 'member.txt'
    assert not path.exists()


def test_stat_size(gdpr_export: Path) -> None:
    path = gdpr_export / 'gdpr_export' / 'messages' / 'index.csv'

    assert path.stat().st_size == len(path.read_bytes())


def test_zip_stat_uses_archive_file_when_datetime_is_default(tmp_path: Path) -> None:
    """
    ZIP entries can use 1980-01-01 as a placeholder when Python can't read their real timestamp.
    In that case, ZipPath.stat() falls back to the archive file's stat instead of reporting the placeholder date.
    """
    target = tmp_path / 'archive.zip'
    info = zipfile.ZipInfo('file.txt')

    with zipfile.ZipFile(target, 'w') as z:
        z.writestr(info, b'abc')

    path = CPath(target) / 'file.txt'

    assert path.stat() == target.stat()
