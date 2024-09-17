from __future__ import annotations

import io
import os
import pathlib
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from tarfile import TarFile, TarInfo
from typing import Dict, Generator, Optional, Union

from typing_extensions import Self

from .utils import walk_paths

maybe_slots = {'slots': True} if sys.version_info[:2] >= (3, 10) else {}


@dataclass(**maybe_slots)
class Node:
    info: TarInfo
    children: list[Node]

    @property
    def name(self) -> str:
        return self.info.name.rsplit('/', maxsplit=1)[-1]


class TarPath(Path):

    if sys.version_info[:2] < (3, 12):
        # older version of python need _flavour defined
        _flavour = pathlib._windows_flavour if os.name == 'nt' else pathlib._posix_flavour  # type: ignore[attr-defined]

    def __new__(cls, tar: Union[str, Path, TarPath, TarFile], *, node: Optional[Node] = None) -> Self:
        if isinstance(tar, TarPath):
            # make sure TarPath(TarPath(...)) works
            assert node is None, node  # just in case
            return tar  # type: ignore[return-value]  # hmm doesn't like Self for some reason??

        if isinstance(tar, TarFile):
            # primary constructor, taking in Tarfile + Node
            path = Path(tar.name)  # type: ignore[arg-type]
            if node is not None:
                path = path / Path(node.info.name)
            res = super().__new__(cls, path)
            return res  # delegate to __init__

        # otherwise it's str | Path -- need to build a new TarFile + Node for it
        assert node is None, node  # just in case
        path = Path(tar)
        tar, node = TarPath._make_args(path)
        return cls(tar=tar, node=node)

    def __init__(self, tar: Union[str, Path, TarPath, TarFile], *, node: Optional[Node] = None) -> None:
        if hasattr(self, 'tar'):
            # already initialized via __new__
            return
        assert isinstance(tar, TarFile), tar  # make mypy happy. the other options are for __new__
        path = Path(tar.name)  # type: ignore[arg-type]
        if node is not None:
            path = path / Path(node.info.name)

        if sys.version_info[:2] >= (3, 12):
            # in older version of python Path didn't have __init__, so this just calls object.__init__
            super().__init__(path)

        self.tar = tar
        self._node = node

    @property
    def node(self) -> Node:
        n = self._node
        assert n is not None, "path doesn't exist"  # TODO kinda crap we can't report which path is that...
        return n

    @property
    def name(self) -> str:
        return self.node.name

    def is_file(self) -> bool:
        return self.node.info.isfile()

    def is_dir(self) -> bool:
        return self.node.info.isdir()

    def exists(self, **kwargs) -> bool:
        return self._node is not None  # meh

    def iterdir(self) -> Generator[TarPath, None, None]:
        node = self.node
        assert node.info.isdir()

        for entry in node.children:
            yield TarPath(tar=self.tar, node=entry)

    def __repr__(self) -> str:
        return f'{self.tar=} {self._node=}'

    def __truediv__(self, other) -> TarPath:
        assert '/' not in other  # TODO handle later
        # TODO normalise path maybe? not sure what if it contains double dots

        for c in self.node.children:
            # TODO it should probs track relative paths inside archive?
            if c.name == other:
                return TarPath(tar=self.tar, node=c)
        return TarPath(tar=self.tar, node=None)

    def open(self, mode: str = 'r', **kwargs):  # type: ignore[override]
        extracted = self.tar.extractfile(self.node.info)
        if 'b' in mode:  # meh
            return extracted
        else:
            return io.TextIOWrapper(extracted, encoding=kwargs.get('encoding'))  # type: ignore[arg-type]

    @property
    def _parts_impl(self) -> tuple[str, ...]:
        # a bit of an implementation detail, but sometimes it's used by pathlib
        # messy, but might be ok..
        tar_path = Path(self.tar.name)  # type: ignore[arg-type]
        return tar_path.parts + Path(self.node.info.name).parts

    @staticmethod
    def _make_args(path: Path) -> tuple[TarFile, Optional[Node]]:
        tf = tarfile.open(path, 'r')

        members = tf.getmembers()
        paths = []
        infos = {}
        for m in members:
            is_dir = m.isdir()
            p = m.name + ('/' if is_dir else '')
            paths.append(p)
            infos[m.name] = m

        nodes: Dict[str, Node] = {}

        def get_node(p: str) -> Node:
            node = nodes.get(p)
            if node is None:
                if p == '.':
                    info = TarInfo(name='')
                    info.type = tarfile.DIRTYPE
                else:
                    info = infos[p]
                node = Node(
                    info=info,
                    children=[],
                )
                nodes[p] = node
            return node

        for r, dirs, files in walk_paths(paths, separator='/'):
            p = f'{r}/' if r != '.' else ''
            pnode = get_node(r)
            for x in dirs + files:
                cnode = get_node(f'{p}{x}')
                pnode.children.append(cnode)

        root_node = nodes['.']  # meh
        return (tf, root_node)


# TODO unify tests with zippath?
def test_tar_dir(tmp_path: Path) -> None:
    from . import CPath  # avoid circular import

    structure_data: Path = Path(__file__).parent / 'tests/structure_data'
    target = structure_data / 'gdpr_export.tar.gz'

    assert target.exists(), target

    assert not isinstance(target, TarPath)  # just in case

    tar: Path = CPath(target)
    assert isinstance(tar, TarPath)

    tar = TarPath(tar)  # should support double wrappping
    assert isinstance(tar, TarPath)
    assert isinstance(tar, Path)

    assert tar.exists()

    # TODO what should tar.name return? tar.gz filename??
    assert str(tar) == str(target)

    [subdir] = tar.iterdir()

    assert isinstance(subdir, Path)

    hash(subdir)  # shouldn't crash

    ## make sure comparisons work
    assert subdir == tar / 'gdpr_export'
    assert subdir != tar
    ##

    assert subdir.name == 'gdpr_export'
    assert str(subdir) == str(target / 'gdpr_export')
    assert tar.is_dir()  # TODO not sure about this.. maybe it should return both is_file and is_dir?

    whatever = subdir / 'whatever'
    # assert whatever.name == 'whatever'
    assert not whatever.exists()

    messages = subdir / 'messages'
    assert messages.is_dir()

    assert messages.parts == (*target.parts, 'gdpr_export', 'messages')

    assert messages < subdir / 'profile'  # supports ordering

    index = messages / 'index.csv'
    assert index.is_file()
    assert index.exists()

    data = index.read_bytes()
    assert data == b'test message\n'

    text = index.read_text()
    assert text == 'test message\n'

    with index.open('rb') as fo:
        assert fo.read() == b'test message\n'

    with index.open() as fo:
        assert fo.read() == 'test message\n'
