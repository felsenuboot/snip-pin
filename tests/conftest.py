"""The scripts have hyphens in their names, so they are loaded by path."""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    """Import ROOT/<name>.py as a module; the GTK scripts import gi but open no window."""
    saved = sys.argv
    sys.argv = [name]                       # pin-view.py hands over only with an argument
    try:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(ROOT, name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.argv = saved
    return mod


@pytest.fixture(scope="session")
def elements():
    return load("snip-elements")


@pytest.fixture(scope="session")
def view():
    return load("pin-view")


@pytest.fixture(scope="session")
def history():
    return load("pin-history")
