"""
Shared archive-path tests.

These cases are expected to pass for regular directories, ZIP archives, and tar-style archives.
Format-specific edge cases belong next to the corresponding path implementation instead.
"""

from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from .. import CPath, TarPath, ZipPath, is_compressed

ArchiveEntries = dict[str, bytes | None]

TAR_WRITE_MODES = {
    'tar'     : 'w',
    'tgz'     : 'w:gz',
    'tar.gz'  : 'w:gz',
    'tar.bz2' : 'w:bz2',
    'tar.xz'  : 'w:xz',
}  # fmt: skip
if sys.version_info[:2] >= (3, 14):
    TAR_WRITE_MODES['tar.zst'] = 'w|zst'

TAR_ARCHIVE_KINDS = list(TAR_WRITE_MODES)
GDPR_EXPORT_KINDS = ['directory', 'zip', *TAR_ARCHIVE_KINDS]


GDPR_EXPORT_ENTRIES: ArchiveEntries = {
    'gdpr_export/'                       : None,
    'gdpr_export/comments/'              : None,
    'gdpr_export/comments/comments.json' : b'',
    'gdpr_export/profile/'               : None,
    'gdpr_export/profile/settings.json'  : b'',
    'gdpr_export/messages/'              : None,
    'gdpr_export/messages/index.csv'     : b'test message\n',
}  # fmt: skip


PATTERN_ENTRIES: ArchiveEntries = {
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


def _write_archive(target: Path, kind: str, entries: ArchiveEntries) -> Path:
    if kind == 'zip':
        with zipfile.ZipFile(target, 'w') as z:
            for name, data in entries.items():
                if data is None:
                    z.writestr(name.rstrip('/') + '/', '')
                else:
                    z.writestr(name, data)
    elif kind in TAR_WRITE_MODES:
        # Type checkers can't narrow the mode after a dict lookup, but all values are valid tarfile modes.
        with tarfile.open(target, TAR_WRITE_MODES[kind]) as t:  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]
            for name, data in entries.items():
                if data is None:
                    info = tarfile.TarInfo(name.rstrip('/'))
                    info.type = tarfile.DIRTYPE
                else:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)

                info.mode = 0o755 if data is None else 0o644
                # Keep generated tar entries away from the epoch so stat timestamp tests catch real member metadata.
                info.mtime = 1625100000
                if data is None:
                    t.addfile(info)
                else:
                    t.addfile(info, io.BytesIO(data))
    else:
        raise AssertionError(kind)

    assert target.exists()
    return target


def _gdpr_export_target(kind: str, tmp_path: Path) -> Path:
    if kind == 'directory':
        return _write_directory(tmp_path / 'gdpr_export_directory', GDPR_EXPORT_ENTRIES)

    target = _write_archive(tmp_path / f'gdpr_export.{kind}', kind, GDPR_EXPORT_ENTRIES)
    assert target.exists(), target  # precondition
    return target


def _normalized_walk(root: Path) -> list[tuple[Path, list[str], list[str]]]:
    """
    Compare walk output across backends without depending on absolute temp paths or filesystem ordering.
    """
    return sorted((r.relative_to(root), sorted(dirs), sorted(files)) for r, dirs, files in root.walk())


@pytest.fixture(params=GDPR_EXPORT_KINDS)
def path_factory(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[ArchiveEntries], Path]:
    kind = request.param

    def make_path(entries: ArchiveEntries) -> Path:
        if kind == 'directory':
            return _write_directory(tmp_path / 'directory', entries)

        target = tmp_path / f'archive.{kind}'
        return CPath(_write_archive(target, kind, entries))

    return make_path


@pytest.fixture(params=GDPR_EXPORT_KINDS)
def gdpr_export(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    kind = request.param
    target = _gdpr_export_target(kind, tmp_path)
    return target if kind == 'directory' else CPath(target)


def test_file_read_modes(gdpr_export: Path) -> None:
    member = 'gdpr_export/messages/index.csv'
    path = gdpr_export / member

    assert path.read_text() == 'test message\n'
    assert path.read_bytes() == b'test message\n'

    assert path.open(mode='rb').read() == b'test message\n'
    assert path.open(mode='br').read() == b'test message\n'
    assert path.open(mode='r').read() == 'test message\n'
    assert path.open(mode='rt').read() == 'test message\n'
    assert path.open(mode='tr').read() == 'test message\n'


def test_open_positional_arguments_match_pathlib(gdpr_export: Path) -> None:
    path = gdpr_export / 'gdpr_export/messages/index.csv'

    with path.open('r', -1, 'utf-8', 'strict', '\n') as f:
        assert f.read() == 'test message\n'


def test_open_positional_text_options(path_factory: Callable[[ArchiveEntries], Path]) -> None:
    path = path_factory({'file': b'\xff\r\n'}) / 'file'

    with path.open('r', -1, 'utf-8', 'replace', '') as f:
        assert f.read() == '\N{REPLACEMENT CHARACTER}\r\n'


@pytest.mark.parametrize('kind', ['zip', 'tar'])
@pytest.mark.parametrize('mode', ['w', 'a', 'x', 'r+'])
def test_archive_paths_reject_write_modes(tmp_path: Path, kind: str, mode: str) -> None:
    archive = CPath(_write_archive(tmp_path / f'archive.{kind}', kind, {'file': b'data'}))
    path = archive / 'file'

    with pytest.raises(ValueError) as exc:
        path.open(mode)
    assert str(exc.value) == f"{type(path).__name__}.open() does not support mode {mode!r}"


@pytest.mark.parametrize(
    ('kind', 'path_type'),
    [
        pytest.param  ('directory', CPath  , id='directory'),
        pytest.param  ('zip'      , ZipPath, id='zip'      ),
        *(pytest.param(kind       , TarPath, id=kind       ) for kind in TAR_ARCHIVE_KINDS),
    ],
)  # fmt: skip
def test_cpath_existing_path_dispatch(
    tmp_path: Path,
    kind: str,
    path_type: type[Path],
) -> None:
    target = _gdpr_export_target(kind, tmp_path)
    if kind != 'directory':
        assert is_compressed(target)

    cpath = CPath(target)
    assert isinstance(cpath, path_type)

    file = cpath / 'gdpr_export/messages/index.csv'
    assert file.exists()
    assert file.read_text() == 'test message\n'

    assert isinstance(CPath(*cpath.parts), path_type)
    assert isinstance(CPath(cpath), path_type)


@pytest.mark.parametrize(
    ('kind', 'path_type'),
    [
        pytest.param  ('directory', Path   , id='directory'),
        pytest.param  ('zip'      , ZipPath, id='zip'      ),
        *(pytest.param(kind       , TarPath, id=kind       ) for kind in TAR_ARCHIVE_KINDS),
    ],
)  # fmt: skip
def test_pathlib_compatibility(
    tmp_path: Path,
    kind: str,
    path_type: type[Path],
) -> None:
    target = _gdpr_export_target(kind, tmp_path)
    archive: Path | ZipPath | TarPath
    if kind == 'zip':
        archive = ZipPath(target)
        ZipPath(archive)  # make sure double wrapping doesn't crash
    elif kind in TAR_ARCHIVE_KINDS:
        archive = TarPath(target)
        TarPath(archive)  # make sure double wrapping doesn't crash
    else:
        archive = Path(target)
        Path(archive)  # make sure double wrapping doesn't crash

    # magic! convenient to make third party libraries agnostic of CPath/ZipPath etc
    assert isinstance(archive, Path)
    assert isinstance(archive, path_type)
    assert isinstance(archive / 'subpath', Path)

    assert path_type(target) == path_type(target)
    assert archive.absolute() == archive
    assert archive / '.' == archive

    # shouldn't crash
    hash(archive)

    assert (archive / Path('gdpr_export', 'comments')).exists()
    # check str constructor just in case
    archive_from_str = path_type(str(target))
    assert (archive_from_str / 'gdpr_export' / 'comments').exists()
    assert not (archive_from_str / 'whatever').exists()


def test_dot_segments(gdpr_export: Path) -> None:
    assert gdpr_export / '.' == gdpr_export
    assert (gdpr_export / '.' / 'gdpr_export').exists()
    assert (gdpr_export / 'gdpr_export' / './comments').exists()


def test_implicit_parent_directories(path_factory: Callable[[ArchiveEntries], Path]) -> None:
    """
    Archive formats can store a nested file without separate entries for its parent directories.
    Archive-backed paths should synthesize those directories and expose the same tree as pathlib.
    """
    archive = path_factory(
        {
            'nested/deeper/file.txt': b'data',
        }
    )

    nested = archive / 'nested'
    deeper = nested / 'deeper'
    file = deeper / 'file.txt'

    assert nested.is_dir()
    assert deeper.is_dir()
    assert file.is_file()
    assert file.read_bytes() == b'data'
    assert _normalized_walk(archive) == [
        (Path()               , ['nested'], []),
        (Path('nested')       , ['deeper'], []),
        (Path('nested/deeper'), []        , ['file.txt']),
    ]  # fmt: skip


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


def test_glob_star_is_not_recursive(gdpr_export: Path) -> None:
    root = gdpr_export / 'gdpr_export'

    assert sorted(path.relative_to(root) for path in root.glob('*')) == [
        Path('comments'),
        Path('messages'),
        Path('profile'),
    ]


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

    missing_nested = gdpr_export / 'gdpr_export' / 'path' / 'notin' / 'archive'
    assert not missing_nested.exists()
    assert not missing_nested.is_dir()
    assert not missing_nested.is_file()


def test_suffixes(gdpr_export: Path) -> None:
    path = gdpr_export / 'gdpr_export' / 'comments' / 'comments.json.gz'

    assert path.suffixes == ['.json', '.gz']
    assert path.suffix == '.gz'


def test_tree_navigation(gdpr_export: Path) -> None:
    """
    Exercise pathlib-style navigation over an archive-backed directory.

    This groups a few related operations over the same GDPR export tree: recursive globbing,
    converting archive members back to paths relative to an archive subdirectory, matching a
    directory by pattern, and listing immediate children while preserving the archive path type.
    """
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


def test_path_identity_and_ordering(gdpr_export: Path) -> None:
    root = gdpr_export / 'gdpr_export'
    comments = root / 'comments'
    messages = root / 'messages'
    profile = root / 'profile'

    assert comments != gdpr_export

    # Shouldn't crash for archive-backed paths.
    hash(comments)

    assert messages.parts == (*gdpr_export.parts, 'gdpr_export', 'messages')
    assert messages < profile


@pytest.mark.parametrize(
    ('pattern', 'expected'),
    [
        ('*'          , ['aaa', 'empty_dir', 'file.txt', 'messages', 'profile']),
        ('*.txt'      , ['file.txt']),
        ('aaa/*'      , ['aaa/bbb.txt', 'aaa/ccc']),
        ('*/*.json'   , ['profile/settings.json']),
        ('**/*.json'  , ['aaa/ccc/ddd.json', 'profile/settings.json']),
        ('**/file.txt', ['file.txt']),
        ('*/'         , ['aaa', 'empty_dir', 'messages', 'profile']),
    ],
)  # fmt: skip
def test_glob_patterns(
    path_factory: Callable[[ArchiveEntries], Path],
    pattern: str,
    expected: list[str],
) -> None:
    root = path_factory(PATTERN_ENTRIES) / 'root'

    assert sorted(p.relative_to(root) for p in root.glob(pattern)) == [Path(p) for p in expected]


@pytest.mark.parametrize(
    ('method', 'expected'),
    [
        ('glob' , ['file.txt']),
        ('rglob', ['aaa/bbb.txt', 'file.txt']),
    ],
)  # fmt: skip
def test_pathlike_glob_pattern(
    path_factory: Callable[[ArchiveEntries], Path],
    method: str,
    expected: list[str],
) -> None:
    """Archive paths accept path-like glob patterns on every supported Python version."""
    root = path_factory(PATTERN_ENTRIES) / 'root'
    if not isinstance(root, (TarPath, ZipPath)) and sys.version_info[:2] < (3, 13):
        pytest.skip('pathlib.Path requires Python 3.13+ for path-like glob patterns')

    matches = getattr(root, method)(Path('*.txt'))
    assert sorted(p.relative_to(root) for p in matches) == [Path(p) for p in expected]


@pytest.mark.parametrize(
    ('method', 'pattern', 'expected_before_313', 'expected_from_313'),
    [
        (
            'glob',
            '**',
            ['.', 'aaa', 'aaa/ccc', 'empty_dir', 'messages', 'profile'],
            [
                '.',
                'aaa',
                'aaa/bbb.txt',
                'aaa/ccc',
                'aaa/ccc/ddd.json',
                'empty_dir',
                'file.txt',
                'messages',
                'messages/index.csv',
                'profile',
                'profile/settings.json',
            ],
        ),
        (
            'glob',
            'aaa/**',
            ['aaa', 'aaa/ccc'],
            ['aaa', 'aaa/bbb.txt', 'aaa/ccc', 'aaa/ccc/ddd.json'],
        ),
        (
            'rglob',
            '**',
            ['.', 'aaa', 'aaa/ccc', 'empty_dir', 'messages', 'profile'],
            [
                '.',
                'aaa',
                'aaa/bbb.txt',
                'aaa/ccc',
                'aaa/ccc/ddd.json',
                'empty_dir',
                'file.txt',
                'messages',
                'messages/index.csv',
                'profile',
                'profile/settings.json',
            ],
        ),
    ],
)
def test_terminal_recursive_glob(
    path_factory: Callable[[ArchiveEntries], Path],
    method: str,
    pattern: str,
    expected_before_313: list[str],
    expected_from_313: list[str],
) -> None:
    """A terminal ``**`` also matches files starting with Python 3.13."""
    root = path_factory(PATTERN_ENTRIES) / 'root'
    expected = expected_from_313 if sys.version_info[:2] >= (3, 13) else expected_before_313

    matches = getattr(root, method)(pattern)
    assert sorted(p.relative_to(root) for p in matches) == [Path(p) for p in expected]


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
    root = path_factory(PATTERN_ENTRIES)

    assert sorted(p.relative_to(root) for p in root.rglob(pattern)) == [Path(p) for p in expected]


def test_rglob_from_subdirectory(path_factory: Callable[[ArchiveEntries], Path]) -> None:
    root = path_factory(PATTERN_ENTRIES) / 'root' / 'aaa'

    assert [p.relative_to(root) for p in root.rglob('*.json')] == [Path('ccc/ddd.json')]


@pytest.mark.parametrize('filename', ['missing.zip', *(f'missing.{kind}' for kind in TAR_ARCHIVE_KINDS)])
def test_missing_archive_is_cpath(tmp_path: Path, filename: str) -> None:
    target = tmp_path / filename

    path = CPath(target)

    assert not target.exists()
    assert isinstance(path, CPath)
    assert not path.exists()
    assert not (path / 'path/in/archive').exists()


@pytest.mark.parametrize('filename', ['missing.zip', *(f'missing.{kind}' for kind in TAR_ARCHIVE_KINDS)])
def test_open_missing_archive_raises_file_not_found(tmp_path: Path, filename: str) -> None:
    target = tmp_path / filename

    with pytest.raises(FileNotFoundError) as exc:
        CPath(target).open()
    assert exc.value.filename == str(target)


@pytest.mark.parametrize(
    ('path_type', 'filename'),
    [
        (ZipPath, 'missing.zip'),
        *((TarPath, f'missing.{kind}') for kind in TAR_ARCHIVE_KINDS),
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

    member = path / 'member.txt'
    assert type(member) is type(tmp_path / filename)
    assert member == target / 'member.txt'
    assert not member.exists()


def test_stat_size(gdpr_export: Path) -> None:
    path = gdpr_export / 'gdpr_export' / 'messages' / 'index.csv'

    assert path.stat().st_size == len(path.read_bytes())


def test_stat_reports_file_datetime(gdpr_export: Path) -> None:
    path = gdpr_export / 'gdpr_export' / 'comments' / 'comments.json'

    assert path.stat().st_mtime > 1625000000
