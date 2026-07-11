`kompress` lets you transparently read common compressed files and archives through a `pathlib.Paht`-like interface.

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

Originally discussed in this [HPI issue](https://github.com/karlicoss/HPI/issues/20)

## Installing

`pip install kompress`
