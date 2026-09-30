"""Sphinx configuration for the solarflow-ble API reference."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

project = "solarflow-ble"
author = "Daan Vervacke"
copyright = "2026, Daan Vervacke"
try:
    release = _version("solarflow-ble")
except PackageNotFoundError:  # pragma: no cover
    release = "0.0.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

html_theme = "furo"
