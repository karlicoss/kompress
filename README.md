`kompress` lets you transparently read common compressed files and archives through a `pathlib.Path`-like interface.

It attempts to keep the API as close to `pathlib` as practical, but only supports read-oriented operations.

The common case for this is:

- You have a compressed file that contains some text
- You have a function (perhaps from another library that you don't control) that accepts a `pathlib.Path` object and calls `.open()`, `.read_text()`, or `.read_bytes()` on it

Without wrapping the function or adding logic to let it read from compressed files, this lets you do the following:

```python
from pathlib import Path
from kompress import CPath

def char_count(path: Path) -> int:
    # here, if a CPath is passed, it decompresses and returns a string
    return len(path.read_text())

inp = input("Enter a file to process: ")  # file came from user
char_count(CPath(inp))
```

For formats containing a directory tree, select a member with the usual `/` operator:

```python
export = CPath("export.tar.xz")
settings = export / "profile" / "settings.json"
print(settings.read_text())
```

Supported single-file compression formats:

- `.gz`
- `.bz2`
- `.xz`
- `.zst` and `.zstd` (on Python older than 3.14, install `kompress[zstd]`)
- `.lz4` (install `kompress[lz4]`)

Supported archive formats:

- `.zip`
- `.tar`
- `.tgz` and `.tar.gz`
- `.tar.bz2`
- `.tar.xz`
- `.tar.zst` (Python 3.14 or newer)

If it doesn't recognize the filetype, it will just call `pathlib.Path.open`, reading it like a regular file.

## Installing

`pip install kompress`

## Motivation and prior art

The design is driven by one constraint: often you can't change the *consumer* of the path.
Plenty of third-party code accepts a `pathlib.Path`, `isinstance`-checks it, and calls `.open()`/`.read_text()`/`.iterdir()` on it.
So kompress paths have to literally *pass as* `pathlib.Path` objects (they subclass it, with some `__class__` trickery for the zip adapter), rather than merely being "path-like".

That single requirement is what rules out the existing alternatives:

- stdlib [`zipfile.Path`](https://docs.python.org/3/library/zipfile.html#path-objects) (and its standalone counterpart [zipp](https://github.com/jaraco/zipp)) is the closest thing, and is what `kompress.ZipPath` builds on -- but it isn't an actual `pathlib.Path`, only covers zip, and `tarfile` has no equivalent at all.
- [fsspec](https://github.com/fsspec/filesystem_spec) + [universal-pathlib](https://github.com/fsspec/universal_pathlib) is the mainstream general-purpose answer (`UPath` even subclasses `pathlib.Path`). It's a considerably heavier dependency though, uses URL-style paths (`zip://...`), and won't transparently decompress a bare `.gz`/`.xz` file on `.open()`.
- [PyFilesystem2](https://github.com/PyFilesystem/pyfilesystem2) (with [fs.archive](https://github.com/althonos/fs.archive)) has its own filesystem abstraction, not compatible with `pathlib.Path`; it also seems unmaintained these days.
- [smart_open](https://github.com/piskvorky/smart_open) does transparent decompression, but only as an `open()` replacement -- there is no path object to pass around.
- [patool](https://github.com/wummel/patool)/[pyunpack](https://github.com/ponty/pyunpack) and stdlib [`shutil.unpack_archive`](https://docs.python.org/3/library/shutil.html#shutil.unpack_archive) extract archives to disk, rather than reading members in place.
- FUSE mounts ([ratarmount](https://github.com/mxmlnkn/ratarmount), archivemount) give you real paths and fast random access into large archives, but require a mount step and platform support.
- [pathlib-abc](https://github.com/barneygale/pathlib-abc) (which grew out of the [Make pathlib extensible](https://discuss.python.org/t/make-pathlib-extensible/3428) effort) is the future-facing way to implement custom path classes, and might eventually become the proper base for kompress. However its classes deliberately don't inherit from `pathlib.Path`, so on their own they wouldn't pass the `isinstance` checks that motivate this library in the first place.

Originally discussed in [this issue](https://github.com/karlicoss/kompress/issues/10) (transferred from HPI), which collected many of the links above.
