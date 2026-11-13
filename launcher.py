"""
launcher.py
===========
Lanzador de escritorio de FUTURES-SCOPE (es el programa que se convierte en
FuturesScope.exe con PyInstaller).

Doble clic -> ventana de arranque con la marca -> inicia el motor en segundo plano
-> abre la app en tu navegador. Al cerrar la ventana se detiene todo.

Modos:
    python launcher.py               ventana de arranque (o consola si no hay entorno gráfico)
    python launcher.py --console     solo consola
    python launcher.py --selftest    arranca, comprueba que responde y sale (lo usa GitHub Actions)
    (interno) --serve PUERTO         proceso hijo que ejecuta Streamlit
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # código y recursos
HOME = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent  # datos y .env
sys.path.insert(0, str(ROOT))

from engine.config import APP_NAME, APP_VERSION, BUILD_TAG  # noqa: E402

# Paleta (igual que la interfaz)
BG, PANEL, LINE, TEXT, MUTED, GREEN, RED = "#0E1117", "#12161F", "#1F2633", "#E6EDF3", "#7D8590", "#00FF88", "#FF3B30"
LOG_FILE = HOME / "data" / "launcher.log"


# =============================================================================
# Utilidades
# =============================================================================
def say(msg: str) -> None:
    """print seguro (en el .exe sin consola sys.stdout es None)."""
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except (OSError, ValueError):
            pass


def free_port(preferred: int = 8501) -> int:
    """Puerto libre (8501 si está disponible)."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def is_ready(port: int) -> bool:
    """¿Responde el servidor de Streamlit?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as resp:
            return resp.read().strip() == b"ok"
    except OSError:
        return False


def child_python() -> str:
    """Intérprete para el servidor (modo script): variable FUTURES_SCOPE_PYTHON, .venv local o el actual."""
    override = os.environ.get("FUTURES_SCOPE_PYTHON")
    if override:
        return override
    for candidate in (HOME / ".venv" / "Scripts" / "python.exe", HOME / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def spawn_server(port: int) -> subprocess.Popen:
    """Lanza el servidor en un proceso hijo con su salida en data/launcher.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a", encoding="utf-8", errors="replace")  # noqa: SIM115 - vive lo que el proceso
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · {APP_NAME} {APP_VERSION} · puerto {port} =====\n")
    log.flush()
    if FROZEN:
        cmd = [sys.executable, "--serve", str(port)]
    else:
        cmd = [child_python(), str(Path(__file__).resolve()), "--serve", str(port)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            cwd=str(ROOT), creationflags=flags)


def stop_server(proc: subprocess.Popen | None) -> None:
    """Detiene el servidor con elegancia (y a la fuerza si no responde)."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()


def serve(port: int) -> None:
    """Proceso hijo: ejecuta Streamlit dentro de este mismo intérprete / .exe."""
    os.chdir(ROOT)  # para que Streamlit encuentre .streamlit/config.toml
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(ROOT / "app.py"),
        "--server.port", str(port), "--server.address", "127.0.0.1", "--server.headless", "true",
        "--global.developmentMode", "false", "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none", "--theme.base", "dark",
    ]
    sys.exit(stcli.main())


def wait_ready(proc: subprocess.Popen, port: int, timeout: float = 180.0) -> bool:
    """Espera a que el servidor responda (False si el proceso muere o se agota el tiempo)."""
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        if is_ready(port):
            return True
        time.sleep(0.5)
    return False


# =============================================================================
# Ventana de arranque (tkinter, incluido en Python para Windows)
# =============================================================================
class Splash:
    """Ventana minimalista con la marca, el estado y dos botones."""

    def __init__(self, port: int) -> None:
        import tkinter as tk
        import tkinter.font as tkfont
        from tkinter import ttk

        self.tk = tk
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self.failed = False
        self.started = time.time()

        root = tk.Tk()
        self.root = root
        root.title(f"{APP_NAME} · {BUILD_TAG}")
        root.configure(bg=BG)
        root.resizable(False, False)
        w, h = 580, 360
        root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
        try:
            self._icon = tk.PhotoImage(file=str(ROOT / "assets" / "icon.png"))
            root.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 - el icono es opcional
            self._icon = None

        mono = ("Consolas", "Menlo", "DejaVu Sans Mono")
        family = next((f for f in mono if f in tkfont.families(root)), "Courier")

        frame = tk.Frame(root, bg=BG, padx=36, pady=28)
        frame.pack(fill="both", expand=True)

        head = tk.Frame(frame, bg=BG)
        head.pack(anchor="w")
        dot = tk.Canvas(head, width=14, height=14, bg=BG, highlightthickness=0)
        dot.create_oval(2, 2, 12, 12, fill=GREEN, outline="")
        dot.pack(side="left", padx=(0, 10))
        tk.Label(head, text=" ".join(APP_NAME), fg=TEXT, bg=BG, font=(family, 20, "bold")).pack(side="left")

        tk.Label(frame, text=BUILD_TAG.upper(), fg=MUTED, bg=BG, font=(family, 9)).pack(anchor="w", pady=(10, 0))
        tk.Frame(frame, bg=LINE, height=1).pack(fill="x", pady=18)

        self.status = tk.Label(frame, text="Iniciando el motor de análisis…", fg=TEXT, bg=BG, font=(family, 10),
                               anchor="w", justify="left", wraplength=500)
        self.status.pack(fill="x")
        self.detail = tk.Label(frame, text="La primera vez puede tardar hasta un minuto.", fg=MUTED, bg=BG,
                               font=(family, 8), anchor="w")
        self.detail.pack(fill="x", pady=(4, 12))

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("FS.Horizontal.TProgressbar", troughcolor=PANEL, background=GREEN, bordercolor=PANEL,
                        lightcolor=GREEN, darkcolor=GREEN, thickness=4)
        self.bar = ttk.Progressbar(frame, style="FS.Horizontal.TProgressbar", mode="indeterminate", length=508)
        self.bar.pack(fill="x")
        self.bar.start(12)

        buttons = tk.Frame(frame, bg=BG)
        buttons.pack(fill="x", pady=(22, 0))
        self.open_btn = tk.Button(buttons, text="ABRIR EN NAVEGADOR", command=self.open_browser, state="disabled",
                                  bg=GREEN, fg="#04130B", activebackground="#33FFA0", disabledforeground="#2E5C45",
                                  relief="flat", bd=0, padx=18, pady=9, font=(family, 9, "bold"), cursor="hand2")
        self.open_btn.pack(side="left")
        tk.Button(buttons, text="CERRAR", command=self.close, bg=PANEL, fg=TEXT, activebackground=LINE,
                  activeforeground=TEXT, relief="flat", bd=0, padx=18, pady=9, font=(family, 9),
                  cursor="hand2").pack(side="left", padx=10)

        tk.Label(frame, text=f"v{APP_VERSION} · Solo análisis: no ejecuta órdenes", fg="#4A5361", bg=BG,
                 font=(family, 8)).pack(side="bottom", anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)

    def run(self) -> None:
        """Arranca el servidor y la ventana."""
        self.proc = spawn_server(self.port)
        threading.Thread(target=self._wait, daemon=True).start()
        self.root.after(500, self._tick)
        self.root.mainloop()

    def _wait(self) -> None:
        if wait_ready(self.proc, self.port):
            self.ready = True
        else:
            self.failed = True

    def _tick(self) -> None:
        """Actualiza el estado sin bloquear la ventana."""
        if self.ready:
            self.bar.stop()
            self.bar.configure(mode="determinate", value=100)
            self.status.configure(text=f"Listo · {self.url}", fg=GREEN)
            self.detail.configure(text="Mantén esta ventana abierta mientras usas la app. Ciérrala para salir.")
            self.open_btn.configure(state="normal")
            self.open_browser()
            return
        if self.failed:
            self.bar.stop()
            self.status.configure(text="No se pudo iniciar el motor.", fg=RED)
            self.detail.configure(text=f"Revisa {LOG_FILE}")
            return
        self.detail.configure(text=f"Cargando librerías y datos… {time.time() - self.started:.0f} s")
        self.root.after(500, self._tick)

    def open_browser(self) -> None:
        webbrowser.open(self.url)

    def close(self) -> None:
        stop_server(self.proc)
        self.root.destroy()


def ensure_tcl_env() -> None:
    """
    En el .exe, apunta TCL_LIBRARY / TK_LIBRARY a los datos de Tcl/Tk empaquetados.
    (Si faltan, tkinter falla con "Can't find a usable init.tcl".)
    """
    if not FROZEN:
        return
    targets = {
        "TCL_LIBRARY": ("init.tcl", ("_tcl_data", "tcl/tcl8.6", "lib/tcl8.6", "tcl8.6")),
        "TK_LIBRARY": ("tk.tcl", ("_tk_data", "tcl/tk8.6", "lib/tk8.6", "tk8.6")),
    }
    for var, (marker, rels) in targets.items():
        current = os.environ.get(var)
        if current and (Path(current) / marker).exists():
            continue
        for rel in rels:
            if (ROOT / rel / marker).exists():
                os.environ[var] = str(ROOT / rel)
                break


def gui_available() -> bool:
    """¿Hay tkinter y pantalla?"""
    try:
        import tkinter  # noqa: F401
        import tkinter.font  # noqa: F401
    except ImportError:
        return False
    return os.name == "nt" or sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def log_line(text: str) -> None:
    """Añade una línea a data/launcher.log (sin fallar nunca)."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def native_message(text: str, error: bool = False) -> None:
    """Cuadro de mensaje nativo de Windows (bloquea hasta pulsar Aceptar). En otros sistemas, consola."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, f"{APP_NAME} · {BUILD_TAG}", 0x10 if error else 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    say(text)


def run_headless(port: int, reason: str = "") -> int:
    """
    Plan B si la ventana de arranque no puede abrirse: arranca el motor, abre el navegador
    y muestra un aviso nativo. Al pulsar Aceptar se cierra el programa.
    """
    if reason:
        log_line(f"Ventana de arranque no disponible ({reason}); usando modo sin ventana.")
    if os.name != "nt":
        return run_console(port)
    proc = spawn_server(port)
    try:
        if not wait_ready(proc, port):
            native_message(f"No se pudo iniciar el motor de análisis.\n\nRevisa el registro:\n{LOG_FILE}", error=True)
            return 1
        url = f"http://127.0.0.1:{port}"
        webbrowser.open(url)
        native_message(f"{APP_NAME} está abierto en tu navegador:\n{url}\n\n"
                       "Mantén este aviso abierto mientras usas la app.\nPulsa Aceptar para CERRAR el programa.")
        return 0
    finally:
        stop_server(proc)


# =============================================================================
# Modos consola y autotest
# =============================================================================
def run_console(port: int) -> int:
    say(f"\n  {APP_NAME} · {BUILD_TAG}\n  Iniciando en http://127.0.0.1:{port} … (Ctrl+C para salir)")
    proc = spawn_server(port)
    try:
        if not wait_ready(proc, port):
            say(f"  ✖ No se pudo iniciar. Revisa {LOG_FILE}")
            return 1
        say("  ✔ Listo. Abriendo el navegador…")
        webbrowser.open(f"http://127.0.0.1:{port}")
        while proc.poll() is None:
            time.sleep(1)
        return proc.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop_server(proc)


def engine_check() -> str:
    """
    Importa TODOS los módulos que usa la app y corre un análisis DEMO completo.
    Detecta módulos que falten dentro del .exe (el health check del servidor no los ve).
    """
    import importlib

    for name in ("engine.pipeline", "engine.backtester", "engine.context_analyzer", "strategies", "ui.components",
                 "plotly.graph_objects", "ccxt", "yfinance", "mplfinance", "tradingview_ta"):
        importlib.import_module(name)
    from engine.context_analyzer import ContextAnalyzer
    from engine.news_filter import NewsFilter
    from engine.pipeline import AnalysisPipeline, AnalysisRequest

    res = AnalysisPipeline(context=ContextAnalyzer(enabled=False), news=NewsFilter(enabled=False)).run(
        AnalysisRequest(symbol="DEMO/USDT", market="demo", timeframe="1h"))
    if not res.ok:
        raise RuntimeError(res.error)
    return f"motor OK ({res.signal.direction} {res.signal.score:.0f}/100)"


def tcl_check() -> tuple[bool, str]:
    """
    ¿Puede arrancar Tcl/Tk (la base de la ventana de arranque)?
    Obligatorio en el .exe de Windows; informativo en el resto.
    """
    ensure_tcl_env()
    try:
        import tkinter

        interp = tkinter.Tcl()  # Necesita init.tcl: es justo lo que falla si no se empaquetó
        library = interp.eval("info library")
        tk_dir = os.environ.get("TK_LIBRARY") or str(Path(library).parent / "tk8.6")
        if FROZEN and not (Path(tk_dir) / "tk.tcl").exists():
            return False, f"Tk FALLO: falta tk.tcl en {tk_dir}"
        return True, f"Tcl/Tk OK ({interp.eval('info patchlevel')})"
    except Exception as exc:  # noqa: BLE001
        required = FROZEN and os.name == "nt"
        return (not required), f"Tcl/Tk {'FALLO' if required else 'no disponible'}: {type(exc).__name__}: {exc}"


def selftest() -> int:
    """Comprueba Tcl/Tk y el motor, arranca el servidor, verifica /_stcore/health y la página (0 = OK)."""
    port = free_port(8765)
    t0 = time.time()
    tcl_ok, tcl_msg = tcl_check()
    say(tcl_msg)
    try:
        engine_msg = engine_check()
    except Exception as exc:  # noqa: BLE001
        engine_msg = f"motor FALLO: {type(exc).__name__}: {exc}"
    say(engine_msg)
    engine_msg = f"{tcl_msg} · {engine_msg}"
    proc = spawn_server(port)
    ok = wait_ready(proc, port, timeout=240) and "motor OK" in engine_msg and tcl_ok
    if ok:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as resp:
                ok = resp.status == 200
        except OSError:
            ok = False
    stop_server(proc)
    msg = f"SELFTEST {'OK' if ok else 'FALLO'} en {time.time() - t0:.1f}s (puerto {port}) · {engine_msg}"
    say(msg)
    try:
        (HOME / "data").mkdir(exist_ok=True)
        (HOME / "data" / "selftest.txt").write_text(msg + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--serve"]:
        serve(int(args[1]) if len(args) > 1 else 8501)
        return
    if "--selftest" in args:
        sys.exit(selftest())
    port = free_port()
    if "--console" in args:
        sys.exit(run_console(port))
    ensure_tcl_env()
    if not gui_available():
        sys.exit(run_headless(port, "sin entorno gráfico"))
    try:
        splash = Splash(port)
    except Exception as exc:  # noqa: BLE001 - p. ej. TclError: Can't find a usable init.tcl
        sys.exit(run_headless(port, f"{type(exc).__name__}: {exc}"))
    splash.run()


if __name__ == "__main__":
    main()
