"""One Tk interpreter for the whole test run.

Six test modules each made their own module-scoped root and destroyed it on the
way out. On Windows a fresh tk.Tk() after a destroy fails with "invalid command
name tcl_findLibrary", so the suite only passed because of the ORDER the files
happen to sort in - adding tests/test_activity.py broke it twice, once by
sorting first and once by shifting what ran when.

A single session root fixes it at the cause: nothing is destroyed mid-run, so
there is never a second interpreter to create. Modules keep their own `root`
fixture name by depending on this one, so no test body changed.
"""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def tk_root():
    r = tk.Tk()
    r.withdraw()
    yield r
    # destroyed once, at the very end of the run, when nothing follows it
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def clean_root(tk_root):
    """The shared root with every child from the previous test removed, so a
    module that counts children still sees only its own."""
    for child in tk_root.winfo_children():
        child.destroy()
    return tk_root
