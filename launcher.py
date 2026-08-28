"""PyInstaller entry point. hemsa/__main__.py uses relative imports, which fail
when PyInstaller runs it as a top-level script (no parent package) - importing
through the package gives them their context. Dev runs keep using -m hemsa."""

import sys

from hemsa.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
