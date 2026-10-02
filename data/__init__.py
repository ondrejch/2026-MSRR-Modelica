"""Packaged MSRR data tree (plant decks, scenario decks, JSON schemas).

This initializer turns the repository's ``data/`` directory into the
importable ``msrr_data`` package that ships inside the smd-msrr wheel
(mapped by ``[tool.setuptools.package-dir]`` in ``pyproject.toml``).
Runtime code locates it through :mod:`helpers.data_resources`
(``importlib.resources``), never by importing this package directly.
"""

__all__: list[str] = []
