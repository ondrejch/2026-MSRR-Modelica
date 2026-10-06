"""Compatibility setup.py shim.

Project metadata is defined in pyproject.toml.
This file exists for tools that still invoke setup.py directly.
"""

from setuptools import setup


if __name__ == "__main__":
    setup()
