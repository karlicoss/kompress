from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .. import CPath, Ext, TarPath


@pytest.mark.skipif(sys.version_info[:2] >= (3, 14), reason=".tar.zst is supported by tarfile on Python 3.14+")
def test_tar_zst_requires_python_314(tmp_path: Path) -> None:
    target = tmp_path / f'archive{Ext.tarzst}'
    target.write_bytes(b'')

    with pytest.raises(RuntimeError, match=r"\.tar\.zst requires Python 3\.14\+"):
        TarPath(target)

    with pytest.raises(RuntimeError, match=r"\.tar\.zst requires Python 3\.14\+"):
        CPath(target)
