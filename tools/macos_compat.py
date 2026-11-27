"""
tools/macos_compat.py
=====================
Asegura que todas las librerías instaladas funcionen desde macOS 12 (Monterey).

Algunos paquetes (p. ej. numpy) publican binarios "solo para macOS 14+" y pip los
elige cuando el servidor de compilación tiene un macOS nuevo. Este script:
  1. Revisa cada binario Mach-O del entorno y lee su versión mínima de macOS.
  2. Para cada paquete que exija más que el objetivo, descarga el binario oficial
     compatible (pip download --platform macosx_12_0_<arch>) y lo reinstala.
  3. Vuelve a revisar y falla si queda algo incompatible.

Uso (en el Mac de compilación, con el entorno activado):
    python tools/macos_compat.py 12.0
"""

from __future__ import annotations

import importlib.metadata as md
import pathlib
import platform
import re
import subprocess
import sys
import sysconfig
import tempfile

MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf"}


def min_macos(path: pathlib.Path) -> tuple[int, int] | None:
    """Versión mínima de macOS de un binario (None si no es Mach-O)."""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) not in MAGIC:
                return None
    except OSError:
        return None
    out = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True).stdout
    worst = None
    for block in out.split("Load command"):
        if "cmd LC_BUILD_VERSION" in block:
            m = re.search(r"^\s*minos (\d+)\.(\d+)", block, re.M)
        elif "cmd LC_VERSION_MIN_MACOSX" in block:
            m = re.search(r"^\s*version (\d+)\.(\d+)", block, re.M)
        else:
            continue
        if m:
            v = (int(m[1]), int(m[2]))
            worst = v if worst is None or v > worst else worst
    return worst


def offenders(target: tuple[int, int]) -> dict[str, tuple[str, str, tuple[int, int]]]:
    """{distribución: (versión, archivo, minos)} de los paquetes que exigen más que `target`."""
    owner: dict[pathlib.Path, tuple[str, str]] = {}
    for dist in md.distributions():
        for f in dist.files or []:
            owner[pathlib.Path(dist.locate_file(f)).resolve()] = (dist.metadata["Name"], dist.version)
    site = pathlib.Path(sysconfig.get_paths()["purelib"])
    bad: dict[str, tuple[str, str, tuple[int, int]]] = {}
    for p in site.rglob("*"):
        if p.is_symlink() or not p.is_file() or p.suffix not in (".so", ".dylib", ""):
            continue
        v = min_macos(p)
        if v and v > target:
            name, ver = owner.get(p.resolve(), (None, None))
            if name and name not in bad:
                bad[name] = (ver, str(p.relative_to(site)), v)
    return bad


def fix(name: str, version: str, plat: str) -> bool:
    """Descarga e instala el binario compatible con `plat` (misma versión si existe)."""
    with tempfile.TemporaryDirectory() as tmp:
        for spec in (f"{name}=={version}", name):
            cmd = [sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
                   "--platform", plat, "-d", tmp, spec]
            if subprocess.run(cmd, capture_output=True, text=True).returncode == 0:
                wheels = list(pathlib.Path(tmp).glob("*.whl"))
                if wheels:
                    print(f"  ↺ {name}: reinstalando {wheels[0].name}")
                    return subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps",
                                           str(wheels[0])]).returncode == 0
    print(f"  ✖ {name}: no hay binario compatible con {plat}")
    return False


def main() -> int:
    major, minor = (sys.argv[1] if len(sys.argv) > 1 else "12.0").split(".")
    target = (int(major), int(minor))
    plat = f"macosx_{target[0]}_{target[1]}_{platform.machine()}"
    bad = offenders(target)
    print(f"Objetivo macOS {target[0]}.{target[1]} ({plat}) · paquetes incompatibles: {len(bad)}")
    for name, (ver, file, v) in bad.items():
        print(f"  {name} {ver}: {file} exige macOS {v[0]}.{v[1]}")
        fix(name, ver, plat)
    left = offenders(target)
    for name, (ver, file, v) in left.items():
        print(f"  SIGUE INCOMPATIBLE: {name} {ver} ({file} → macOS {v[0]}.{v[1]})")
    print("Compatibilidad OK" if not left else "Quedan paquetes incompatibles")
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())
