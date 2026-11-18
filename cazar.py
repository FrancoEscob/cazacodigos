# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "opencv-python-headless",
#   "numpy",
#   "rapidocr-onnxruntime==1.4.4",
#   "streamlink",
#   "imageio-ffmpeg",
#   "pyperclip",
#   "pyautogui; sys_platform == 'win32'",
#   "pygetwindow; sys_platform == 'win32'",
# ]
# ///
"""
Cazacódigos: mira un stream de Twitch y detecta el instante en que aparece
un código en pantalla (Xbox 5x5 o PlayStation 4-4-4). Lo copia al portapapeles
y, si se lo pedís, lo pega solo o abre la página de canje.

Uso rápido:
    uv run cazar.py                      # mira twitch.tv/rubius, copia y avisa
    uv run cazar.py --abrir              # además abre el canje de Xbox con el código cargado
    uv run cazar.py --teclear --enter    # además lo pega en la ventana que tengas enfocada
    uv run cazar.py --archivo video.ts   # probar con una grabación o una imagen
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ANCHO, ALTO = 1920, 1080
AQUI = Path(__file__).resolve().parent
ES_WINDOWS = platform.system() == "Windows"

# Palabra que el streamer pone al lado del código. El lector a veces confunde
# O con 0 o I con 1, así que se acepta también "CODIG0", "C0DIGO", etc.
ETIQUETA = re.compile(r"C[OÓ0]D[I1L]G[OÓ0]|C[O0]DE|CLAVE")
# Los códigos de Xbox/Microsoft nunca llevan estos caracteres.
EXCLUIDOS_XBOX = set("AEIOULS015")
# Link oficial de Microsoft que abre el canje con el código ya cargado.
URL_XBOX = "https://go.microsoft.com/fwlink/?LinkId=532540&mstoken={codigo}"
# Zona donde apareció el cartel en las capturas (fracciones del cuadro: x0,y0,x1,y1).
ZONA_INICIAL = (0.66, 0.57, 1.0, 0.78)


# ───────────────────────── lectura de texto ─────────────────────────

def crear_ocr(hilos: int = -1):
    import logging

    from rapidocr_onnxruntime import RapidOCR

    logging.getLogger().setLevel(logging.ERROR)
    # limit 'max' muy alto = nunca achicar la imagen antes de leer.
    return RapidOCR(det_limit_side_len=4000, det_limit_type="max", intra_op_num_threads=hilos)


@dataclass
class Lectura:
    codigo: str  # con guiones
    tipo: str  # xbox | play | otro
    conf: float
    dudoso: bool = False
    caja: tuple | None = None  # x0,y0,x1,y1 en píxeles de la imagen leída
    etiqueta: tuple | None = None


def _normalizar(txt: str) -> str:
    t = txt.upper()
    for ch in "—–_~·•=":
        t = t.replace(ch, "-")
    return t


RE_XBOX = re.compile(r"(?<![A-Z0-9-])[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}(?![A-Z0-9-])")
RE_PLAY = re.compile(r"(?<![A-Z0-9-])[A-Z0-9]{4}(?:-[A-Z0-9]{4}){2}(?![A-Z0-9-])")


def _clasificar(texto: str, relajado: bool) -> tuple[str, str] | None:
    """Devuelve (codigo_con_guiones, tipo) o None."""
    texto = re.sub(r"\s+", "", texto)
    m = RE_XBOX.search(texto)
    if m:
        return m.group(0), "xbox"
    m = RE_PLAY.search(texto)
    if m:
        return m.group(0), "play"
    if relajado:
        # Al lado de la palabra CODIGO aceptamos que falte algún guion,
        # pero los que haya tienen que caer en su lugar (evita leer un
        # código a medio aparecer como si fuera otro más corto).
        limpio = re.sub(r"[^A-Z0-9-]", "", texto).strip("-")
        trozos = [p for p in limpio.split("-") if p]
        solo = "".join(trozos)
        for largo, grupo, tipo in ((25, 5, "xbox"), (12, 4, "play")):
            if len(solo) == largo and all(len(p) % grupo == 0 for p in trozos):
                return "-".join(solo[i:i + grupo] for i in range(0, largo, grupo)), tipo
    return None


def extraer(resultado) -> tuple[list[Lectura], list[tuple]]:
    """De la salida del OCR saca los códigos y las cajas de la palabra clave."""
    items = []
    for caja, txt, conf in resultado or []:
        xs = [p[0] for p in caja]
        ys = [p[1] for p in caja]
        items.append((min(xs), min(ys), max(xs), max(ys), _normalizar(txt), float(conf)))

    etiquetas = [(x0, y0, x1, y1) for x0, y0, x1, y1, t, _ in items if ETIQUETA.search(t)]

    lecturas: dict[str, Lectura] = {}
    for x0, y0, x1, y1, t, conf in items:
        m = ETIQUETA.search(t)
        etiqueta = None
        if m:
            resto, relajado, etiqueta = t[m.end():], True, (x0, y0, x1, y1)
        else:
            resto, relajado = t, False
            cy = (y0 + y1) / 2
            for ex0, ey0, ex1, ey1 in etiquetas:
                alto = ey1 - ey0
                if ey0 - 0.4 * alto <= cy <= ey1 + 0.4 * alto and x0 >= ex0:
                    relajado, etiqueta = True, (ex0, ey0, ex1, ey1)
                    break
        r = _clasificar(resto, relajado)
        if not r:
            continue
        codigo, tipo = r
        dudoso = tipo == "xbox" and any(c in EXCLUIDOS_XBOX for c in codigo)
        if codigo not in lecturas or conf > lecturas[codigo].conf:
            lecturas[codigo] = Lectura(codigo, tipo, conf, dudoso, (x0, y0, x1, y1), etiqueta)
    return list(lecturas.values()), etiquetas


def leer_zona(ocr, cuadro: np.ndarray, zona: tuple, escala: float):
    """Lee la zona vigilada. Devuelve (lecturas, etiquetas, recorte).

    Primero la lee tal cual (rápido, alcanza para la letra grande de los códigos
    de PlayStation). Si ve el cartel pero no saca un código confiable, la agranda
    y la vuelve a leer (hace falta para la letra chica de los de Xbox).
    """
    h, w = cuadro.shape[:2]
    x0, y0, x1, y1 = (int(zona[0] * w), int(zona[1] * h), int(zona[2] * w), int(zona[3] * h))
    recorte = cuadro[y0:y1, x0:x1]
    res, _ = ocr(recorte, use_cls=False)
    lecturas, etiquetas = extraer(res)
    hay_texto_largo = any(len(t) >= 12 for _, t, _ in res or [])
    confiable = lecturas and all(l.conf >= 0.9 and not l.dudoso for l in lecturas)
    if escala > 1 and not confiable and (etiquetas or lecturas or hay_texto_largo):
        grande = cv2.resize(recorte, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
        res, _ = ocr(grande, use_cls=False)
        lecturas2, etiquetas2 = extraer(res)
        if lecturas2:
            lecturas = lecturas2
        etiquetas = etiquetas or etiquetas2
    return lecturas, etiquetas, recorte


def zona_desde_etiqueta(caja: tuple, w: int, h: int) -> tuple:
    """Dada la caja de 'CODIGO' en el cuadro entero, arma la zona a vigilar."""
    x0, y0, x1, y1 = caja
    ancho, alto = x1 - x0, y1 - y0
    zx0 = max(0.0, (x0 - 1.5 * ancho) / w)
    zx1 = min(1.0, (x1 + 6.0 * ancho) / w)
    zy0 = max(0.0, (y0 - 3.5 * alto) / h)
    zy1 = min(1.0, (y1 + 3.5 * alto) / h)
    return (zx0, zy0, zx1, zy1)


# ───────────────────────── decidir cuándo disparar ─────────────────────────

def _distancia(a: str, b: str) -> int:
    if len(a) != len(b):
        return 99
    return sum(1 for x, y in zip(a, b) if x != y)


@dataclass
class Rastreador:
    confirmar: int = 2
    espera_dudoso: float = 1.2
    racha: dict = field(default_factory=dict)  # codigo -> (veces seguidas, primera vez)
    disparados: list = field(default_factory=list)

    def alimentar(self, lecturas: list[Lectura], ahora: float) -> list[tuple[Lectura, bool]]:
        """Devuelve [(lectura, es_variante)] de los códigos que hay que disparar ya."""
        vistos = {l.codigo for l in lecturas}
        for c in list(self.racha):
            if c not in vistos:
                del self.racha[c]
        salida = []
        for l in lecturas:
            if l.codigo in self.disparados:
                continue
            veces, primera = self.racha.get(l.codigo, (0, ahora))
            veces += 1
            self.racha[l.codigo] = (veces, primera)
            if veces < self.confirmar:
                continue
            if l.dudoso and ahora - primera < self.espera_dudoso:
                continue  # le damos un rato a que salga una lectura limpia
            variante = any(_distancia(l.codigo, d) <= 3 for d in self.disparados)
            self.disparados.append(l.codigo)
            salida.append((l, variante))
        return salida


# ───────────────────────── acciones ─────────────────────────

def copiar(texto: str):
    try:
        if not ES_WINDOWS and os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            # DEVNULL: wl-copy deja un proceso vivo y no debe quedarse con nuestra salida.
            subprocess.run(["wl-copy", texto], timeout=2, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        import pyperclip

        pyperclip.copy(texto)
    except Exception as e:  # el portapapeles nunca debe frenar lo demás
        print(f"  (no pude copiar: {e})")


def teclear(con_enter: bool):
    """Pega el portapapeles en la ventana enfocada."""
    try:
        if ES_WINDOWS:
            import pyautogui

            pyautogui.PAUSE = 0
            pyautogui.hotkey("ctrl", "v")
            if con_enter:
                time.sleep(0.05)
                pyautogui.press("enter")
        elif shutil.which("wtype"):
            subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], timeout=2)
            if con_enter:
                time.sleep(0.05)
                subprocess.run(["wtype", "-k", "Return"], timeout=2)
        elif shutil.which("xdotool"):
            subprocess.run(["xdotool", "key", "ctrl+v"] + (["Return"] if con_enter else []), timeout=2)
        else:
            print("  (no encontré wtype/xdotool para pegar)")
    except Exception as e:
        print(f"  (no pude pegar: {e})")


def titulos_de_ventanas() -> list[str]:
    """Títulos de las ventanas abiertas (solo Windows)."""
    if not ES_WINDOWS:
        return []
    import pygetwindow as gw

    return [t for t in gw.getAllTitles() if t.strip()]


def pegar_en_ventanas(patrones: list[str], con_enter: bool) -> int:
    """Windows: trae al frente cada ventana cuyo título contenga alguno de los
    textos y le pega el portapapeles. Sirve para tener un navegador por cuenta,
    cada uno parado en la casilla del código. Devuelve en cuántas pegó."""
    if not ES_WINDOWS:
        print("  (--ventanas por ahora solo funciona en Windows; usá --teclear)")
        return 0
    import pyautogui
    import pygetwindow as gw

    pyautogui.PAUSE = 0
    hechas = 0
    for v in gw.getAllWindows():
        titulo = (v.title or "").lower()
        if not titulo or not any(p in titulo for p in patrones):
            continue
        try:
            if v.isMinimized:
                v.restore()
            try:
                v.activate()
            except Exception:
                pyautogui.press("alt")  # Windows a veces niega el foco; con Alt lo suelta
                v.activate()
            time.sleep(0.08)
            pyautogui.hotkey("ctrl", "v")
            if con_enter:
                time.sleep(0.04)
                pyautogui.press("enter")
            hechas += 1
        except Exception as e:
            print(f"  (no pude pegar en '{v.title[:40]}': {e})")
    return hechas


def avisar(l: Lectura):
    def _sonar():
        try:
            if ES_WINDOWS:
                import winsound

                for _ in range(3):
                    winsound.Beep(1200, 150)
            else:
                if shutil.which("notify-send"):
                    subprocess.Popen(["notify-send", "-u", "critical", f"CÓDIGO {l.tipo.upper()}", l.codigo])
                oga = "/usr/share/sounds/freedesktop/stereo/complete.oga"
                if shutil.which("paplay") and os.path.exists(oga):
                    subprocess.Popen(["paplay", oga])
                else:
                    print("\a", end="", flush=True)
        except Exception:
            pass

    threading.Thread(target=_sonar, daemon=True).start()


class Acciones:
    def __init__(self, args):
        self.args = args
        self.carpeta = AQUI / "capturas"
        self.registro = AQUI / "codigos.log"

    def disparar(self, l: Lectura, variante: bool, t_cuadro: float, recorte, origen: str):
        a = self.args
        if a.solo and l.tipo != a.solo:
            print(f"  (ignoro {l.codigo}: es {l.tipo}, pediste solo {a.solo})")
            return
        t0 = time.perf_counter()
        hechas = []
        if not a.sin_acciones:
            # PlayStation: la casilla espera los 12 caracteres sin guiones. Xbox: con guiones.
            con_guiones = a.guiones == "si" or (a.guiones == "auto" and l.tipo != "play")
            copiar(l.codigo if con_guiones else l.codigo.replace("-", ""))
            hechas.append("copiado")
            # Una variante es casi el mismo código leído distinto: se copia y avisa,
            # pero no se vuelve a pegar ni a abrir nada.
            if not variante:
                if a.ventanas:
                    n = pegar_en_ventanas(a.ventanas, a.enter)
                    hechas.append(f"pegado en {n} ventana(s)" + ("+enter" if a.enter else ""))
                elif a.teclear:
                    teclear(a.enter)
                    hechas.append("pegado" + ("+enter" if a.enter else ""))
                if a.abrir and l.tipo == "xbox":
                    webbrowser.open(URL_XBOX.format(codigo=l.codigo))
                    hechas.append("canje Xbox abierto")
                if a.ejecutar:
                    cmd = a.ejecutar.format(codigo=l.codigo, tipo=l.tipo, url=URL_XBOX.format(codigo=l.codigo))
                    subprocess.Popen(cmd, shell=True)
                    hechas.append("comando lanzado")
            avisar(l)
        fin = time.perf_counter()
        marca = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        notas = []
        if variante:
            notas.append("VARIANTE de uno anterior")
        if l.dudoso:
            notas.append("DUDOSO: tiene caracteres que Xbox no usa")
        print()
        print("█" * 64)
        print(f"  {l.tipo.upper():5s}  {l.codigo}")
        print(f"  {marca}  confianza {l.conf:.0%}  {origen}  {' · '.join(notas)}")
        print(f"  {(fin - t_cuadro) * 1000:.0f} ms desde que llegó el cuadro  ({', '.join(hechas) or 'sin acciones'})")
        print("█" * 64, flush=True)
        with open(self.registro, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='milliseconds')}\t{l.tipo}\t{l.codigo}\t{l.conf:.2f}\t{origen}\t{';'.join(notas)}\n")
        if recorte is not None and not a.sin_capturas:
            self.carpeta.mkdir(exist_ok=True)
            nombre = dt.datetime.now().strftime("%H%M%S") + f"_{l.tipo}_{l.codigo}.png"
            cv2.imwrite(str(self.carpeta / nombre), recorte)


# ───────────────────────── video ─────────────────────────

def ruta_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _leer_exacto(tubo, n: int) -> bytes | None:
    partes, falta = [], n
    while falta:
        b = tubo.read(falta)
        if not b:
            return None
        partes.append(b)
        falta -= len(b)
    return b"".join(partes)


def cuadros_de_archivo(ruta: str, fps: float, desde: float, duracion: float | None):
    """Genera (segundo_del_video, cuadro) en orden, sin saltear."""
    cmd = [ruta_ffmpeg(), "-loglevel", "error"]
    if desde:
        cmd += ["-ss", str(desde)]
    cmd += ["-i", ruta]
    if duracion:
        cmd += ["-t", str(duracion)]
    cmd += ["-an", "-vf", f"fps={fps},scale={ANCHO}:{ALTO}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    n = 0
    try:
        while True:
            crudo = _leer_exacto(p.stdout, ANCHO * ALTO * 3)
            if crudo is None:
                break
            yield desde + n / fps, np.frombuffer(crudo, np.uint8).reshape(ALTO, ANCHO, 3)
            n += 1
    finally:
        p.kill()


class EnVivo:
    """Baja el stream con streamlink, lo decodifica con ffmpeg y guarda siempre el último cuadro."""

    def __init__(self, canal: str, calidad: str, fps: float):
        self.canal, self.calidad, self.fps = canal, calidad, fps
        self.ultimo = None  # (numero, instante, cuadro)
        self.cond = threading.Condition()
        self.n = 0
        self.vivo = True
        threading.Thread(target=self._bucle, daemon=True).start()

    def _abrir(self):
        from streamlink import Streamlink

        s = Streamlink()
        s.set_option("stream-timeout", 30)
        url = f"https://twitch.tv/{self.canal}"
        try:
            streams = s.streams(url, options={"low-latency": True, "disable-ads": True})
        except TypeError:
            streams = s.streams(url)
        if not streams:
            raise RuntimeError(f"{self.canal} no está en vivo")
        calidad = self.calidad if self.calidad in streams else "best"
        return streams[calidad].open(), calidad

    def _bucle(self):
        while self.vivo:
            try:
                fd, calidad = self._abrir()
            except Exception as e:
                print(f"[stream] {e}. Reintento en 5 s…", flush=True)
                time.sleep(5)
                continue
            print(f"[stream] conectado a {self.canal} ({calidad})", flush=True)
            ff = subprocess.Popen(
                [ruta_ffmpeg(), "-loglevel", "fatal", "-fflags", "nobuffer", "-flags", "low_delay",
                 "-probesize", "400k", "-analyzeduration", "400k", "-i", "pipe:0", "-an",
                 "-vf", f"fps={self.fps},scale={ANCHO}:{ALTO}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)

            def alimentar():
                try:
                    while self.vivo:
                        datos = fd.read(64 * 1024)
                        if not datos:
                            break
                        ff.stdin.write(datos)
                except Exception:
                    pass
                finally:
                    try:
                        ff.stdin.close()
                    except Exception:
                        pass

            threading.Thread(target=alimentar, daemon=True).start()
            while self.vivo:
                crudo = _leer_exacto(ff.stdout, ANCHO * ALTO * 3)
                if crudo is None:
                    break
                cuadro = np.frombuffer(crudo, np.uint8).reshape(ALTO, ANCHO, 3)
                with self.cond:
                    self.n += 1
                    self.ultimo = (self.n, time.perf_counter(), cuadro)
                    self.cond.notify_all()
            ff.kill()
            try:
                fd.close()
            except Exception:
                pass
            if self.vivo:
                print("[stream] se cortó, reconecto…", flush=True)
                time.sleep(1)

    def esperar_nuevo(self, anterior: int, timeout: float = 5.0):
        with self.cond:
            self.cond.wait_for(lambda: self.ultimo is not None and self.ultimo[0] > anterior, timeout)
            return self.ultimo if self.ultimo and self.ultimo[0] > anterior else None


# ───────────────────────── modos ─────────────────────────

class Zona:
    """Zona vigilada a toda velocidad. Arranca en la de las capturas y se corrige sola."""

    def __init__(self, inicial: tuple):
        self.valor = inicial
        self.aprendida = False
        self.candado = threading.Lock()

    def actualizar(self, etiqueta: tuple, w: int, h: int) -> bool:
        """Recibe la caja de 'CODIGO' vista en el cuadro entero. Mueve la zona solo si hace falta."""
        ex0, ey0, ex1, ey1 = etiqueta
        ancho = ex1 - ex0
        with self.candado:
            zx0, zy0, zx1, zy1 = self.valor
            cubre = (ex0 >= zx0 * w and ey0 >= zy0 * h and ey1 <= zy1 * h
                     and (ex1 + 4.5 * ancho <= zx1 * w or zx1 >= 0.99))
            if cubre:
                return False
            self.valor = zona_desde_etiqueta(etiqueta, w, h)
            self.aprendida = True
            return True

    def leer(self):
        with self.candado:
            return self.valor


def barrido_completo(ocr, cuadro, zona: Zona) -> bool:
    """Lee el cuadro entero buscando la palabra CODIGO en cualquier lado."""
    res, _ = ocr(cuadro, use_cls=False)
    _, etiquetas = extraer(res)
    h, w = cuadro.shape[:2]
    for caja in etiquetas:
        if zona.actualizar(caja, w, h):
            z = zona.leer()
            print(f"[zona] vi 'CODIGO' en otro lugar, ahora vigilo {tuple(round(v, 3) for v in z)}", flush=True)
            return True
    return False


def modo_vivo(args):
    print("Cargando lector de texto…", flush=True)
    # El barrido del cuadro entero va con pocos hilos para no frenar la lectura rápida.
    nucleos = os.cpu_count() or 4
    ocr_rapido, ocr_lento = crear_ocr(max(2, nucleos // 2)), crear_ocr(max(1, nucleos // 6))
    zona = Zona(args.zona)
    rastreador = Rastreador(confirmar=args.confirmar)
    acciones = Acciones(args)
    fuente = EnVivo(args.canal, args.calidad, args.fps)

    def barrer():
        ultimo = 0
        while True:
            f = fuente.esperar_nuevo(ultimo)
            if f:
                ultimo = f[0]
                try:
                    barrido_completo(ocr_lento, f[2], zona)
                except Exception as e:
                    print(f"[barrido] {e}")
            time.sleep(args.pausa_barrido)

    threading.Thread(target=barrer, daemon=True).start()

    extras = [x for x, on in (("pegar", args.teclear and not args.ventanas),
                              (f"pegar en ventanas {args.ventanas}", bool(args.ventanas)),
                              ("enter", args.enter), ("abrir canje Xbox", args.abrir)) if on]
    print(f"Vigilando twitch.tv/{args.canal}. Al detectar: copiar" + "".join(f" + {e}" for e in extras))
    print("Ctrl+C para salir.\n", flush=True)

    ultimo, pasadas, ms_total, t_info = 0, 0, 0.0, time.perf_counter()
    while True:
        f = fuente.esperar_nuevo(ultimo)
        if not f:
            continue
        ultimo, t_cuadro, cuadro = f
        t0 = time.perf_counter()
        lecturas, _, recorte = leer_zona(ocr_rapido, cuadro, zona.leer(), args.escala)
        ms_total += (time.perf_counter() - t0) * 1000
        pasadas += 1
        for l, variante in rastreador.alimentar(lecturas, time.perf_counter()):
            acciones.disparar(l, variante, t_cuadro, recorte, "vivo")
        if time.perf_counter() - t_info > 15:
            hora = dt.datetime.now().strftime("%H:%M:%S")
            print(f"[{hora}] vigilando · {pasadas / (time.perf_counter() - t_info):.1f} lecturas/s · {ms_total / pasadas:.0f} ms cada una", flush=True)
            pasadas, ms_total, t_info = 0, 0.0, time.perf_counter()


def modo_archivo(args):
    ocr = crear_ocr()
    zona = Zona(args.zona)
    rastreador = Rastreador(confirmar=args.confirmar)
    args.sin_acciones = True
    acciones = Acciones(args)
    ruta = args.archivo
    if Path(ruta).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        img = cv2.imread(ruta)
        if abs(img.shape[1] / img.shape[0] - ANCHO / ALTO) < 0.05:
            img = cv2.resize(img, (ANCHO, ALTO), interpolation=cv2.INTER_CUBIC)
        else:  # es un recorte, no un cuadro entero: se lee completo y sin deformar
            zona = Zona((0.0, 0.0, 1.0, 1.0))
        cuadros = ((i / args.fps, img) for i in range(args.confirmar + 1))
    else:
        cuadros = cuadros_de_archivo(ruta, args.fps, args.desde, args.duracion)
    cada = max(1, round(args.fps * args.pausa_barrido))
    n, ms_total, etiqueta_desde = 0, 0.0, None
    for seg, cuadro in cuadros:
        if n % cada == 0:
            barrido_completo(ocr, cuadro, zona)
        t0 = time.perf_counter()
        lecturas, etiquetas, recorte = leer_zona(ocr, cuadro, zona.leer(), args.escala)
        ms_total += (time.perf_counter() - t0) * 1000
        n += 1
        if etiquetas and etiqueta_desde is None:
            etiqueta_desde = seg
            print(f"[{seg:8.2f}s] aparece la palabra CODIGO en la zona", flush=True)
        if not etiquetas and etiqueta_desde is not None:
            print(f"[{seg:8.2f}s] desaparece (estuvo {seg - etiqueta_desde:.1f} s)", flush=True)
            etiqueta_desde = None
        if args.detalle and (lecturas or etiquetas):
            print(f"[{seg:8.2f}s] {[(l.codigo, round(l.conf, 2)) for l in lecturas]}")
        for l, variante in rastreador.alimentar(lecturas, seg):
            acciones.disparar(l, variante, time.perf_counter(), recorte, f"video {seg:.2f}s")
    print(f"\n{n} cuadros leídos, {ms_total / max(n, 1):.0f} ms por lectura de zona. Códigos: {rastreador.disparados or 'ninguno'}")


def main():
    p = argparse.ArgumentParser(description="Detecta códigos que aparecen en un stream de Twitch.")
    p.add_argument("--canal", default="rubius", help="canal de Twitch (por defecto: rubius)")
    p.add_argument("--calidad", default="best", help="calidad del stream (best, 1080p60, 720p60…)")
    p.add_argument("--fps", type=float, default=15, help="cuadros por segundo a mirar")
    p.add_argument("--confirmar", type=int, default=2, help="lecturas iguales seguidas antes de disparar (1 = más rápido, más riesgo)")
    p.add_argument("--solo", choices=["xbox", "play"], help="ignorar los códigos del otro tipo")
    p.add_argument("--teclear", action="store_true", help="pegar el código en la ventana enfocada")
    p.add_argument("--enter", action="store_true", help="con --teclear, apretar Enter después de pegar")
    p.add_argument("--ventanas", type=lambda t: [x.strip().lower() for x in t.split(",") if x.strip()],
                   help="Windows: pegar en TODAS las ventanas cuyo título contenga alguno de estos textos (separados por coma)")
    p.add_argument("--listar-ventanas", action="store_true", help="Windows: mostrar los títulos de las ventanas abiertas y salir")
    p.add_argument("--probar", metavar="CODIGO", help="sin mirar el stream: a los 4 s dispara las acciones con este código de mentira")
    p.add_argument("--abrir", action="store_true", help="abrir el canje de Xbox con el código ya cargado")
    p.add_argument("--guiones", choices=["auto", "si", "no"], default="auto",
                   help="copiar/pegar con guiones (auto: Xbox con, PlayStation sin)")
    p.add_argument("--ejecutar", help="comando a lanzar; admite {codigo}, {tipo} y {url}")
    p.add_argument("--zona", type=lambda s: tuple(float(v) for v in s.split(",")), default=ZONA_INICIAL,
                   help="zona a vigilar: x0,y0,x1,y1 en fracciones del cuadro")
    p.add_argument("--escala", type=float, default=2.0, help="cuánto agrandar la zona antes de leer")
    p.add_argument("--pausa-barrido", type=float, default=1.0, help="segundos entre barridos del cuadro entero")
    p.add_argument("--sin-capturas", action="store_true", help="no guardar la imagen de cada código")
    p.add_argument("--sin-acciones", action="store_true", help="solo mostrar, no copiar ni pegar ni avisar")
    p.add_argument("--archivo", help="probar con un video o imagen en vez del vivo")
    p.add_argument("--desde", type=float, default=0, help="con --archivo: segundo donde empezar")
    p.add_argument("--duracion", type=float, help="con --archivo: cuántos segundos mirar")
    p.add_argument("--detalle", action="store_true", help="con --archivo: mostrar cada lectura")
    args = p.parse_args()
    for salida in (sys.stdout, sys.stderr):  # la consola de Windows no siempre es UTF-8
        try:
            salida.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if args.listar_ventanas:
        for t in titulos_de_ventanas() or ["(solo disponible en Windows)"]:
            print(t)
        return
    if args.probar:
        r = _clasificar(_normalizar(args.probar), relajado=True)
        if not r:
            sys.exit("Ese código no tiene forma de Xbox (5x5) ni de PlayStation (4-4-4).")
        for i in (4, 3, 2, 1):
            print(f"Disparo en {i}…  (dejá enfocada la ventana donde querés que pegue)", flush=True)
            time.sleep(1)
        Acciones(args).disparar(Lectura(r[0], r[1], 1.0), False, time.perf_counter(), None, "prueba")
        time.sleep(1)
        return
    try:
        modo_archivo(args) if args.archivo else modo_vivo(args)
    except KeyboardInterrupt:
        print("\nListo.")


if __name__ == "__main__":
    main()
