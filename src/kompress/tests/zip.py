from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import cast

from .. import ZipPath

structure_data: Path = Path(__file__).parent / "structure_data"


def test_zippath_direct_member_constructor_read_modes() -> None:
    path = ZipPath(structure_data / 'gdpr_export.zip', 'gdpr_export/messages/index.csv')

    assert path.read_text() == 'test message\n'
    assert path.open(mode='rb').read() == b'test message\n'
    assert path.open(mode='r').read() == 'test message\n'


def test_missing_zip_member_constructor_falls_back_to_regular_path(tmp_path: Path) -> None:
    target = tmp_path / 'missing.zip'

    path = cast(Path, ZipPath(target, 'member.txt'))

    assert not target.exists()
    assert type(path) is type(tmp_path)
    assert path == target / 'member.txt'
    assert not path.exists()


def test_zip_stat_uses_archive_timestamps_when_datetime_is_default(tmp_path: Path) -> None:
    """
    ZIP entries can use 1980-01-01 as a placeholder when Python can't read their real timestamp.
    In that case, ZipPath.stat() borrows the archive file's timestamps instead of reporting the placeholder date.
    """
    target = tmp_path / 'archive.zip'
    info = zipfile.ZipInfo('file.txt')

    with zipfile.ZipFile(target, 'w') as z:
        z.writestr(info, b'abc')

    path = ZipPath(target) / 'file.txt'

    stat = path.stat()
    archive_stat = target.stat()
    assert stat.st_size == len(b'abc')
    assert stat.st_atime == archive_stat.st_atime
    assert stat.st_mtime == archive_stat.st_mtime
    if sys.platform == 'win32':
        assert stat[9] == archive_stat.st_birthtime
    else:
        assert stat.st_ctime == archive_stat.st_ctime
