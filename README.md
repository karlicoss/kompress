`kompress` lets you transparently decompress common archive formats while using `pathlib.Path` objects.

It attempts to keep the API as close to pathlib, but things may not be perfect yet

The common case for this is:

- You have a compressed file that when decompressed contains some text
- You have a function (perhaps from another library, that you don't control), that knows how to take a `pathlib.Path` object and call `.read()` or `.read_text()` on it

Without wrapping the function or adding logic to let it read from compressed files, this lets you do the following:

```python
from pathlib import Path
from kompress import CPath, is_compressed

def char_count(path: Path) -> int:
    # here, if a CPath is passed, it decompresses and returns a string
    return len(path.read_text())

inp = input("Enter a file to process: ")  # file came from user
char_count(CPath(inp))
```

This currently supports these archive formats:

- `.xz`
- `.zip`
- `.zst`
- `.tar.gz`
- `.gz`
- `.zstd` (since `python3.14`, on older version use `pip install 'kompress[zstd]'`)
- `.lz4` (`pip install 'kompress[lz4]'`)

If it doesn't recognize the filetype, it will just call `pathlib.Path.open`, reading it like a regular file.

Originally discussed in this [HPI issue](https://github.com/karlicoss/HPI/issues/20)

## Installing

`pip install kompress`

