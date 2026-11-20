"""
setup_fgcore.py
===============
Compila el núcleo C++ de FG VIEW (cpp/fgcore.cpp) como módulo de Python.

    pip install pybind11==3.1.0
    python setup_fgcore.py build_ext --inplace

Requisitos: compilador C++17 (Windows: "Build Tools for Visual Studio" con C++;
macOS: Xcode Command Line Tools; Linux: g++). Si no lo compilas, FG VIEW funciona
igual con la versión Python (más lenta).
"""

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

setup(
    name="fgcore",
    version="1.1.0",
    ext_modules=[Pybind11Extension("fgcore", ["cpp/fgcore.cpp"], cxx_std=17)],
    cmdclass={"build_ext": build_ext},
    zip_safe=False,
)
