from __future__ import annotations

import io
import os
import sys
import tarfile
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from tarfile import TarFile, TarInfo
from typing import Self

from .utils import archive_glob, check_read_mode, walk_paths


@dataclass(slots=True)
class Node:
    info: TarInfo
    children: list[Node]

    @property
    def name(self) -> str:
        # Tar directory entries may retain a trailing '/', which would otherwise make their name empty.
        return self.info.name.rstrip('/').rsplit('/', maxsplit=1)[-1]


Nodes = dict[str, Node]


def _tarpath(tf: TarFile) -> Path:
    path = tf.name
    assert path is not None
    path = os.fspath(path)  # convert PathLike objects into str | bytes
    if isinstance(path, bytes):
        path = path.decode()
    return Path(path)


class TarPath(Path):
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
            return tar  # type: ignore[return-value]  # ty: ignore[invalid-return-type]  # hmm doesn't like Self for some reason??

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
            return path  # type: ignore[return-value]  # ty: ignore[invalid-return-type]
        if path.name.endswith('.tar.zst') and sys.version_info[:2] < (3, 14):
            raise RuntimeError(".tar.zst requires Python 3.14+")

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

    def is_file(self, *, follow_symlinks: bool = True) -> bool:  # noqa: ARG002
        node = self._node
        return node is not None and node.info.isfile()

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:  # noqa: ARG002
        node = self._node
        return node is not None and node.info.isdir()

    def exists(self, **kwargs) -> bool:  # noqa: ARG002
        return self._node is not None  # meh

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:  # noqa: ARG002
        info = self.node.info
        return os.stat_result(
            (
                info.mode,
                0,
                0,
                1,
                info.uid,
                info.gid,
                info.size,
                info.mtime,
                info.mtime,
                info.mtime,
            ),
        )

    def iterdir(self) -> Generator[TarPath, None, None]:
        node = self.node
        assert node.info.isdir()

        for entry in node.children:
            rpath = self._rpath / entry.name
            yield TarPath(tar=self.tar, _nodes=self._nodes, _rpath=rpath, _node=entry)

    def walk(
        self,
        top_down: bool = True,  # noqa: FBT001,FBT002
        on_error=None,
        follow_symlinks: bool = False,  # noqa: FBT001,FBT002
    ) -> Iterator[tuple[TarPath, list[str], list[str]]]:
        assert top_down, "specifying top_down isn't supported for TarPath yet"
        assert on_error is None, "on_error isn't supported for TarPath yet"

        node = self._node
        if node is None or not node.info.isdir():
            return

        child_dirs = {c.name: c for c in node.children if c.info.isdir()}
        dirnames = sorted(child_dirs)
        filenames = sorted(c.name for c in node.children if c.info.isfile())

        yield self, dirnames, filenames

        # Match pathlib.Path.walk: callers can mutate dirnames in-place to prune traversal.
        for dirname in dirnames:
            child = child_dirs.get(dirname)
            if child is None:
                continue
            yield from TarPath(tar=self.tar, _nodes=self._nodes, _rpath=self._rpath / dirname, _node=child).walk(
                top_down=top_down,
                on_error=on_error,
                follow_symlinks=follow_symlinks,
            )

    def glob(self, pattern: str | os.PathLike[str], **kwargs) -> Iterator[TarPath]:  # type: ignore[override, unused-ignore]  # ty: ignore[invalid-method-override]
        yield from archive_glob(self, pattern, recursive=False, **kwargs)

    def rglob(self, pattern: str | os.PathLike[str], **kwargs) -> Iterator[TarPath]:  # type: ignore[override, unused-ignore]  # ty: ignore[invalid-method-override]
        yield from archive_glob(self, pattern, recursive=True, **kwargs)

    def relative_to(self, other: TarPath, *extra: str | os.PathLike[str]) -> Path:  # type: ignore[override]  # ty: ignore[invalid-method-override]
        assert _tarpath(self.tar) == _tarpath(other.tar), (_tarpath(self.tar), _tarpath(other.tar))
        return self._rpath.relative_to(other._rpath.joinpath(*extra))

    def __repr__(self) -> str:
        return f'{self.tar=} {self._rpath=} {self._node=}'

    @property
    def parent(self):
        if len(self._rpath.parts) == 0:
            return Path(self).parent
        return TarPath(tar=self.tar, _nodes=self._nodes, _rpath=self._rpath.parent, _node=None)

    def joinpath(self, *pathsegments: str | os.PathLike[str]) -> TarPath:
        return TarPath(tar=self.tar, _nodes=self._nodes, _rpath=self._rpath.joinpath(*pathsegments), _node=None)

    def __truediv__(self, key: str | os.PathLike[str]) -> TarPath:
        return self.joinpath(key)

    def open(self, mode: str = 'r', **kwargs):  # type: ignore[override]  # ty: ignore[invalid-method-override]
        check_read_mode(mode=mode, path=self)
        extracted = self.tar.extractfile(self.node.info)
        assert extracted is not None
        if 'b' in mode:  # meh
            return extracted
        else:
            return io.TextIOWrapper(extracted, encoding=kwargs.get('encoding'))

    @staticmethod
    def _make_args(path: Path) -> tuple[TarFile, Nodes, Node]:
        tf = tarfile.open(path, 'r')  # noqa: SIM115

        sep = '/'  # note: doesn't really matter which separator is used here, this is just within this function

        members = tf.getmembers()
        infos: dict[str, TarInfo] = {}
        for m in members:
            norm_name = m.name
            while norm_name.startswith('./'):
                # Archives created against the current directory often prefix every member with "./".
                norm_name = norm_name[2:]
            if m.isdir():
                norm_name = norm_name.rstrip('/')
            if norm_name in {'', '.'}:
                # sometimes root is included? we don't need it for walk_paths
                continue

            infos[norm_name] = m

            # Tar archives need not contain explicit entries for parent directories.
            # Synthesize them so a member such as "nested/file" still produces a navigable tree.
            parts = norm_name.split(sep)
            for end in range(1, len(parts)):
                parent = sep.join(parts[:end])
                if parent in infos:
                    continue
                parent_info = TarInfo(name=parent)
                parent_info.type = tarfile.DIRTYPE
                infos[parent] = parent_info

        # walk_paths expects a depth-first-compatible order and explicit directory entries.
        paths = sorted(f'{name}{sep}' if info.isdir() else name for name, info in infos.items())

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


# TODO migrate this into tests/archive_path.py too, with generated archives containing leading "./" members.
def test_tar_dir_leading_dot() -> None:
    """
    Test for 'flat' tar file, when it has leading dots in paths
    (can happen if you did smth like tar -czvf ../archive.tar.gz .)
    """
    from . import CPath  # avoid circular import

    structure_data: Path = Path(__file__).parent / 'tests/structure_data'
    target = structure_data / 'with_leading_dot.tar.gz'

    # Fixture precondition for the checked-in leading-dot archive; not behavioral coverage.
    assert target.exists(), target

    cpath = CPath(target)

    # Missing-path behavior is covered by tests/archive_path.py::test_file_type_methods[tar.gz].
    # This assert also exercises the leading "./" normalization unique to this fixture.
    assert not (cpath / 'whatever').exists()
    # Not covered elsewhere; archive_path.py generates normalized names, while this fixture stores "./c/hello".
    assert (cpath / Path('c', 'hello')).exists()
    # Read behavior is covered by tests/archive_path.py::test_file_read_modes[tar.gz].
    # This assert also exercises the leading "./" normalization unique to this fixture.
    assert (cpath / 'b.txt').read_text() == 'contents\n'

    # Iterdir is covered by tests/archive_path.py::test_tree_navigation[tar.gz].
    # This fixture-specific check makes sure leading "./" entries show up as normal root children.
    assert sorted(cpath.iterdir()) == [cpath / 'a', cpath / 'b.txt', cpath / 'c']

    # Glob behavior is covered by tests/archive_path.py::test_parent_joinpath_glob[tar.gz].
    # These exact patterns against leading "./" normalized names are only covered here.
    assert list(cpath.glob('h*')) == []
    assert list(cpath.glob('b.txt')) == [cpath / 'b.txt']
    assert list((cpath / 'b.txt').glob('*')) == []
    assert list((cpath / 'c').glob('h*')) == [cpath / 'c' / 'hello']

    # Rglob behavior is covered by tests/archive_path.py::test_rglob_patterns[tar.gz-*].
    # These exact patterns against leading "./" normalized names are only covered here.
    assert list(cpath.rglob('b.txt')) == [cpath / 'b.txt']
    assert list(cpath.rglob('h*')) == [cpath / 'c/hello']
    assert list(cpath.rglob('c')) == [cpath / 'c']
    assert list((cpath / 'c').rglob('h*')) == [cpath / 'c' / 'hello']
