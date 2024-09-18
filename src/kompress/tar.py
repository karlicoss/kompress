from __future__ import annotations

import fnmatch
import io
import os
import pathlib
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from tarfile import TarFile, TarInfo
from typing import Dict, Generator, Iterator

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


Nodes = Dict[str, Node]


def _tarpath(tf: TarFile) -> Path:
    path = tf.name
    assert path is not None
    path = os.fspath(path)  # convert PathLike objects into str | bytes
    if isinstance(path, bytes):
        path = path.decode()
    return Path(path)


class TarPath(Path):

    if sys.version_info[:2] < (3, 12):
        # older version of python need _flavour defined
        _flavour = pathlib._windows_flavour if os.name == 'nt' else pathlib._posix_flavour  # type: ignore[attr-defined]

    def __new__(
        cls,
        tar: str | Path | TarPath | TarFile,
        *,
        _nodes: Nodes | None = None,
        _rpath: Path | None = None,
        _node: Node | None = None,
    ) -> Self:
        if isinstance(tar, TarPath):
            # make sure TarPath(TarPath(...)) works
            assert _node is None, _node  # just in case
            return tar  # type: ignore[return-value]  # hmm doesn't like Self for some reason??

        if isinstance(tar, TarFile):
            # primary constructor, taking in Tarfile + relative Path
            assert _rpath is not None
            path = _tarpath(tar) / _rpath
            res = super().__new__(cls, path)
            return res  # delegate to __init__

        # otherwise it's str | Path -- need to build a new TarFile + Node for it XX
        assert _node is None, _node  # just in case
        path = Path(tar)

        if not path.exists():
            # if it doesn't exist, tarpath can't open it...
            # so it's the best we can do is just return a regular path
            return path  # type: ignore[return-value]

        tar, nodes, root = TarPath._make_args(path)
        return cls(tar=tar, _nodes=nodes, _node=root, _rpath=Path())

    def __init__(
        self,
        tar: str | Path | TarPath | TarFile,
        *,
        _nodes: Nodes | None = None,
        _rpath: Path | None = None,
        _node: Node | None = None,
    ) -> None:
        if hasattr(self, 'tar'):
            # already initialized via __new__
            return
        assert isinstance(tar, TarFile), tar  # make mypy happy. the other options are for __new__
        assert _nodes is not None
        assert _rpath is not None

        if sys.version_info[:2] >= (3, 12):
            # in older version of python Path didn't have __init__, so this just calls object.__init__
            path = _tarpath(tar) / _rpath
            super().__init__(path)

        self.tar = tar
        self._nodes = _nodes
        self._rpath = _rpath
        # note: / always used in index (even on windows) since that's what tar archives use
        self._node = _node if _node is not None else _nodes.get('/'.join(_rpath.parts))

    @property
    def node(self) -> Node:
        n = self._node
        assert n is not None, f"path {self} doesn't exist"
        return n

    def is_file(self) -> bool:
        return self.node.info.isfile()

    def is_dir(self) -> bool:
        return self.node.info.isdir()

    def exists(self, **kwargs) -> bool:  # noqa: ARG002
        return self._node is not None  # meh

    def iterdir(self) -> Generator[TarPath, None, None]:
        node = self.node
        assert node.info.isdir()

        for entry in node.children:
            rpath = self._rpath / entry.name
            yield TarPath(tar=self.tar, _nodes=self._nodes, _rpath=rpath, _node=entry)

    def glob(self, pattern: str, **kwargs) -> Iterator[TarPath]:  # type: ignore[override]  # noqa: ARG002
        parts = self._rpath.parts
        prefix = '' if len(parts) == 0 else ('/'.join(parts) + '/')
        full_pattern = prefix + pattern
        for p, node in self._nodes.items():
            if not fnmatch.fnmatch(p, full_pattern):
                continue
            rpath = Path(*p.split('/'))
            yield TarPath(tar=self.tar, _nodes=self._nodes, _rpath=rpath, _node=node)

    def rglob(self, pattern: str, **kwargs) -> Iterator[TarPath]:  # type: ignore[override]  # noqa: ARG002
        # TODO ugh.. not necessarily consistent with pathlib behaviour... need to double check later
        return self.glob('*' + pattern)

    def __repr__(self) -> str:
        return f'{self.tar=} {self._rpath=} {self._node=}'

    def __truediv__(self, other) -> TarPath:
        # TODO normalise it?
        new_rpath = self._rpath / other
        return TarPath(tar=self.tar, _nodes=self._nodes, _rpath=new_rpath, _node=None)

    def open(self, mode: str = 'r', **kwargs):  # type: ignore[override]
        extracted = self.tar.extractfile(self.node.info)
        if 'b' in mode:  # meh
            return extracted
        else:
            return io.TextIOWrapper(extracted, encoding=kwargs.get('encoding'))  # type: ignore[arg-type]

    @staticmethod
    def _make_args(path: Path) -> tuple[TarFile, Nodes, Node]:
        tf = tarfile.open(path, 'r')

        sep = '/'  # note: doesn't really matter which separator is used here, this is just within this function

        members = tf.getmembers()
        paths = []
        infos = {}
        for m in members:
            is_dir = m.isdir()

            norm_name = m.name
            if norm_name == '.':
                # sometimes root is included? we don't need it for walk_paths
                continue
            if norm_name[:2] == './':
                # sometimes archive is created against current dir (.), this ends up with awkward dots in the index...
                norm_name = norm_name[2:]

            p = norm_name + (sep if is_dir else '')
            paths.append(p)
            infos[norm_name] = m

        nodes: dict[str, Node] = {}

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

        for r, dirs, files in walk_paths(paths, separator=sep):
            p = f'{r}{sep}' if r != '.' else ''
            pnode = get_node(r)
            for x in dirs + files:
                cnode = get_node(f'{p}{x}')
                pnode.children.append(cnode)

        root_node = nodes['.']  # meh
        return (tf, nodes, root_node)


# TODO unify tests with zippath?
def test_tar_dir(tmp_path: Path) -> None:
    from . import CPath  # avoid circular import

    nonexistent = CPath(tmp_path / 'donotexist.tar.gz')
    assert not nonexistent.exists()

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

    assert isinstance(subdir, TarPath)
    assert isinstance(subdir, Path)

    hash(subdir)  # shouldn't crash

    ## make sure comparisons work
    assert subdir == tar / 'gdpr_export'
    assert subdir != tar
    ##

    parent = subdir.parent
    assert isinstance(parent, TarPath)
    assert parent == tar

    assert subdir.name == 'gdpr_export'
    assert str(subdir) == str(target / 'gdpr_export')
    assert tar.is_dir()  # TODO not sure about this.. maybe it should return both is_file and is_dir?

    whatever = subdir / 'whatever'
    assert whatever.name == 'whatever'
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


def test_tar_dir_leading_dot() -> None:
    """
    Test for 'flat' tar file, when it has leading dots in paths
    (can happen if you did smth like tar -czvf ../archive.tar.gz .)
    """
    from . import CPath  # avoid circular import

    structure_data: Path = Path(__file__).parent / 'tests/structure_data'
    target = structure_data / 'with_leading_dot.tar.gz'

    assert target.exists(), target

    cpath = CPath(target)

    assert not (cpath / 'whatever').exists()
    assert (cpath / Path('c', 'hello')).exists()
    assert (cpath / 'b.txt').read_text() == 'contents\n'

    assert sorted(cpath.iterdir()) == [cpath / 'a', cpath / 'b.txt', cpath / 'c']

    assert list(cpath.glob('h*')) == []
    assert list(cpath.glob('b.txt')) == [cpath / 'b.txt']
    assert list((cpath / 'b.txt').glob('*')) == []
    assert list((cpath / 'c').glob('h*')) == [cpath / 'c' / 'hello']

    assert list(cpath.rglob('b.txt')) == [cpath / 'b.txt']
    assert list(cpath.rglob('h*')) == [cpath / 'c/hello']
    assert list(cpath.rglob('c')) == [cpath / 'c']
    assert list((cpath / 'c').rglob('h*')) == [cpath / 'c' / 'hello']
