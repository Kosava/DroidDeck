"""
waydroid_api.py — tanki sloj iznad waydroid CLI-ja.

GUI nikada ne poziva waydroid direktno, sve ide kroz ovaj modul.
Ako se neka komanda promeni u novoj verziji Waydroid-a, menja se samo ovde.

Na X11 sesiji (npr. LXQt) Waydroid nema Wayland ekran, pa ga pokrećemo
unutar Weston prozora sa sopstvenim socketom (NESTED_SOCKET).

Funkcije koje pokreću spoljne programe vraćaju bool (True = pokrenuto) i
nikad ne bacaju izuzetak: neuhvaćen izuzetak u Qt slotu ruši PyQt6 aplikaciju.
"""

import configparser
import os
import re
import shlex
import signal
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

# Waydroid sam pravi .desktop fajl za svaku instaliranu Android aplikaciju
APPS_DIR = Path.home() / ".local/share/applications"
APP_PREFIX = "waydroid."

# Sistemske aplikacije koje ne želimo u mreži po defaultu
SYSTEM_PACKAGES = {
    "com.android.settings",
    "com.android.documentsui",
    "com.android.contacts",
    "com.android.calculator2",
    "com.android.camera2",
    "com.android.deskclock",
    "com.android.gallery3d",
    "com.android.inputmethod.latin",
    "org.lineageos.jelly",
    "org.lineageos.recorder",
    "org.lineageos.eleven",
    "org.lineageos.etar",
    "com.google.android.gms",
    "com.google.android.gsf",
}

NESTED_SOCKET = "droiddeck"
NESTED_WIDTH = 1280
NESTED_HEIGHT = 720
SCREEN_SIZES = [(1280, 720), (1600, 900), (1920, 1080)]


def set_screen_size(width: int, height: int) -> None:
    """Veličina Weston prozora; važi od sledećeg paljenja Androida."""
    global NESTED_WIDTH, NESTED_HEIGHT
    NESTED_WIDTH, NESTED_HEIGHT = int(width), int(height)

STATUS_CMD = ["waydroid", "status"]
CONTAINER_SERVICE = "waydroid-container"
# Apsolutne putanje: polkit pravilo DroidDeck-a (50-droiddeck.rules) poredi
# tačnu komandu, pa ove komande ne traže lozinku kad je pravilo instalirano
SYSTEMCTL = "/usr/bin/systemctl"
WAYDROID = "/usr/bin/waydroid"
START_CONTAINER_CMD = ["pkexec", SYSTEMCTL, "start", CONTAINER_SERVICE]
ENABLE_CONTAINER_CMD = ["pkexec", "systemctl", "enable", "--now", CONTAINER_SERVICE]

WAYDROID_DIR = Path("/var/lib/waydroid")
WAYDROID_CFG = WAYDROID_DIR / "waydroid.cfg"


@dataclass
class Status:
    initialized: bool
    container: bool
    session: bool


@dataclass
class AppInfo:
    name: str
    package: str
    icon: str          # putanja do PNG-a (može biti prazna)
    system: bool


# ---------------------------------------------------------------- provere

def is_installed() -> bool:
    return shutil.which("waydroid") is not None


def initialized_on_disk() -> bool:
    """Waydroid je inicijalizovan ako postoji njegov config (isto proverava i sam Waydroid)."""
    return WAYDROID_CFG.exists()


def needs_nested() -> bool:
    """True ako radimo na X11 i treba nam Weston kao Wayland ekran."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return False
    return not os.environ.get("WAYLAND_DISPLAY")


def compositor_available() -> bool:
    return shutil.which("weston") is not None


def _systemctl_ok(*args: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", *args, "--quiet", CONTAINER_SERVICE],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def container_service_active() -> bool:
    """
    Da li systemd servis radi. (`Container:` iz `waydroid status` je stanje
    LXC kontejnera — posle gašenja sesije je STOPPED iako servis radi.)
    """
    return _systemctl_ok("is-active")


def container_service_enabled() -> bool:
    """Servis se sam pali pri startu sistema — DroidDeck onda ne traži lozinku."""
    return _systemctl_ok("is-enabled")


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def socket_path() -> str:
    return os.path.join(_runtime_dir(), NESTED_SOCKET)


def compositor_ready() -> bool:
    """Socket postoji I Weston zaista radi (zaostali fajl posle pada ne važi)."""
    if not needs_nested():
        return True
    return os.path.exists(socket_path()) and compositor_running()


def _remove_stale_socket() -> None:
    for path in (socket_path(), socket_path() + ".lock"):
        try:
            os.remove(path)
        except OSError:
            pass


def compositor_pids() -> list[int]:
    """
    PID-ovi NAŠEG Weston-a: proces čiji je program `weston` i koji ima tačno
    argument --socket=droiddeck. (pgrep/pkill -f bi pogodio i terminal u kom
    neko kuca grep sa tim tekstom.)
    """
    wanted = f"--socket={NESTED_SOCKET}"
    pids = []
    try:
        entries = list(os.scandir("/proc"))
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
            if os.stat(f"/proc/{entry.name}").st_uid != os.getuid():
                continue
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in argv if a]
        if wanted not in args:
            continue
        # sam weston, ili skripta pokrenuta kroz interpreter (argv[1])
        if any(os.path.basename(a) == "weston" for a in args[:2]):
            pids.append(int(entry.name))
    return pids


def compositor_running() -> bool:
    return bool(compositor_pids())


def parse_status(text: str) -> Status | None:
    """Parsira izlaz komande `waydroid status`. None = izlaz nije upotrebljiv."""
    if not text.strip():
        return None
    if "not initialized" in text.lower():
        return Status(initialized=False, container=False, session=False)

    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip().upper()

    if "session" not in fields and "container" not in fields:
        return None
    return Status(
        initialized=True,
        container=fields.get("container") == "RUNNING",
        session=fields.get("session") == "RUNNING",
    )


# ---------------------------------------------------------------- akcije

def wayland_env() -> dict:
    env = os.environ.copy()
    if needs_nested():
        env["WAYLAND_DISPLAY"] = NESTED_SOCKET
    return env


def _detached(cmd: list[str], env: dict | None = None) -> bool:
    """
    Pokreće proces nezavisno od DroidDeck-a (Android radi i kad zatvoriš prozor).
    False ako program ne može da se pokrene (nije instaliran i sl.).
    """
    try:
        subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def start_compositor() -> bool:
    if not needs_nested() or compositor_running():
        return True
    if not compositor_available():
        return False
    _remove_stale_socket()          # inače bi Weston odbio isti naziv socketa
    return _detached([
        "weston",
        f"--socket={NESTED_SOCKET}",
        f"--width={NESTED_WIDTH}",
        f"--height={NESTED_HEIGHT}",
    ])


def stop_compositor(force: bool = False) -> None:
    """SIGTERM (ili SIGKILL sa force=True). Ne čeka — za to služi compositor_running()."""
    if not needs_nested():
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    for pid in compositor_pids():
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def start_session() -> bool:
    return _detached(["waydroid", "session", "start"], env=wayland_env())


def stop_session() -> bool:
    return _detached(["waydroid", "session", "stop"])


def show_full_ui() -> bool:
    return _detached(["waydroid", "show-full-ui"], env=wayland_env())


# ---------------------------------------------------------------- aplikacije

def _read_desktop(path: Path) -> AppInfo | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str            # ključevi su osetljivi na velika slova
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (configparser.Error, KeyError, UnicodeDecodeError):
        return None

    if entry.get("NoDisplay", "").lower() == "true":
        return None

    package = path.stem[len(APP_PREFIX):]
    return AppInfo(
        name=entry.get("Name", package),
        package=package,
        icon=entry.get("Icon", ""),
        system=package in SYSTEM_PACKAGES,
    )


def list_apps() -> list[AppInfo]:
    """Sve Android aplikacije, sortirane po imenu."""
    if not APPS_DIR.is_dir():
        return []
    apps = []
    for path in APPS_DIR.glob(f"{APP_PREFIX}*.desktop"):
        app = _read_desktop(path)
        if app:
            apps.append(app)
    return sorted(apps, key=lambda a: a.name.lower())


def installed_packages() -> set[str]:
    """Paketi za koje Waydroid ima .desktop fajl (i skriveni)."""
    if not APPS_DIR.is_dir():
        return set()
    return {p.stem[len(APP_PREFIX):] for p in APPS_DIR.glob(f"{APP_PREFIX}*.desktop")}


def launch_cmd(package: str) -> list[str]:
    """GUI je pokreće kroz QProcess (sa wayland_env) da bi video grešku."""
    return ["waydroid", "app", "launch", package]


# ---------------------------------------------------------------- komande u Android-u

# Android nazivi paketa: slova, cifre, _ i tačke (bar dva dela). Isto proverava
# i polkit pravilo — ništa drugo ne može da prođe bez lozinke.
_PACKAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")

KEY_HOME = 3
KEY_BACK = 4
KEY_APP_SWITCH = 187
NAV_KEYS = (KEY_HOME, KEY_BACK, KEY_APP_SWITCH)

BOOT_PROP = "sys.boot_completed"


def valid_package(package: str) -> bool:
    return bool(_PACKAGE_RE.fullmatch(package))


def force_stop_cmd(package: str) -> list[str] | None:
    """Potpuno gasi aplikaciju (i onu "sakrivenu" zatvaranjem prozora)."""
    if not valid_package(package):
        return None
    return ["pkexec", WAYDROID, "shell", "--", "am", "force-stop", package]


def keyevent_cmd(key: int) -> list[str] | None:
    if key not in NAV_KEYS:
        return None
    return ["pkexec", WAYDROID, "shell", "--", "input", "keyevent", str(key)]


def parse_boot_completed(text: str) -> bool:
    """`waydroid prop get sys.boot_completed` → '1' kad je Android potpuno podignut."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return bool(lines) and lines[-1] == "1"


POLKIT_RULE = Path("/usr/share/polkit-1/rules.d/50-droiddeck.rules")


def polkit_rule_installed() -> bool:
    return POLKIT_RULE.exists() or Path("/etc/polkit-1/rules.d/50-droiddeck.rules").exists()


def cfg_stamp() -> float:
    """Vreme izmene waydroid.cfg — menja se pri svakom `waydroid init`."""
    try:
        return WAYDROID_CFG.stat().st_mtime
    except OSError:
        return 0.0


def package_stamps() -> dict[str, float]:
    """paket → vreme izmene .desktop fajla (za proveru instalacije/ažuriranja)."""
    stamps = {}
    if APPS_DIR.is_dir():
        for p in APPS_DIR.glob(f"{APP_PREFIX}*.desktop"):
            try:
                stamps[p.stem[len(APP_PREFIX):]] = p.stat().st_mtime
            except OSError:
                pass
    return stamps


# ---------------------------------------------------------------- Play Store / APK

PLAY_STORE_PKG = "com.android.vending"


def play_store_installed() -> bool:
    return (APPS_DIR / f"{APP_PREFIX}{PLAY_STORE_PKG}.desktop").exists()


def install_cmd(apk_path: str) -> list[str]:
    """Komanda za instalaciju; GUI je pokreće kroz QProcess da bi znao rezultat."""
    return ["waydroid", "app", "install", apk_path]


def is_apk(path: str) -> bool:
    return path.lower().endswith(".apk") and os.path.isfile(path)


# ---------------------------------------------------------------- deinstalacija, multi-window

MULTI_WINDOW_PROP = "persist.waydroid.multi_windows"


def uninstall_cmd(package: str) -> list[str]:
    return ["waydroid", "app", "remove", package]


def get_prop_cmd(prop: str) -> list[str]:
    return ["waydroid", "prop", "get", prop]


def set_prop_cmd(prop: str, value: str) -> list[str]:
    return ["waydroid", "prop", "set", prop, value]


def parse_bool_prop(text: str) -> bool | None:
    """Poslednja neprazna linija izlaza: 'true' / 'false' / ništa."""
    lines = [ln.strip().lower() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    if lines[-1] == "true":
        return True
    if lines[-1] == "false":
        return False
    return None


def desktop_file(package: str) -> Path:
    return APPS_DIR / f"{APP_PREFIX}{package}.desktop"


# ---------------------------------------------------------------- podešavanje

CERT_URL = "https://www.google.com/android/uncertified"
SCRIPT_REPO = "https://github.com/casualsnek/waydroid_script"
SCRIPT_DIR = Path.home() / "waydroid_script"     # gde korisnik klonira skriptu
SCRIPT_SETUP_COMMANDS = (
    "sudo pacman -S --needed lzip git python\n"
    f"git clone {SCRIPT_REPO} ~/waydroid_script\n"
    "cd ~/waydroid_script\n"
    "python3 -m venv venv\n"
    "venv/bin/pip install -r requirements.txt\n"
)

# PYTHONUNBUFFERED: Waydroid je Python program; kroz pipe bi ispis napretka
# stizao u paketima od nekoliko KB, pa bi traka napretka skakala
INIT_CMD = ["pkexec", "env", "PYTHONUNBUFFERED=1",
            "waydroid", "init", "-s", "GAPPS", "-f"]
INIT_CMD_SHOWN = "pkexec waydroid init -s GAPPS -f"

# Android ID iz Google Services baze (zvanični postupak iz Waydroid dokumentacije)
_ANDROID_ID_SQL = (
    "ANDROID_RUNTIME_ROOT=/apex/com.android.runtime "
    "ANDROID_DATA=/data "
    "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata "
    "ANDROID_I18N_ROOT=/apex/com.android.i18n "
    "sqlite3 /data/data/com.google.android.gsf/databases/gservices.db "
    "\"select value from main where name = 'android_id';\""
)
ANDROID_ID_CMD = ["pkexec", "waydroid", "shell", "--", "sh", "-c", _ANDROID_ID_SQL]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def kernel_has_binder() -> bool:
    """Binder je potreban Waydroid-u; linux-zen ga ima ugrađenog."""
    if "binder" in _read_text(Path("/proc/filesystems")):
        return True
    if Path("/dev/binderfs").exists() or Path("/dev/binder").exists():
        return True
    release = os.uname().release
    return Path(f"/lib/modules/{release}/kernel/drivers/android").exists()


def kernel_name() -> str:
    return os.uname().release


def has_gapps() -> bool | None:
    """True/False ako možemo da pročitamo config, None ako ne znamo."""
    cfg = _read_text(WAYDROID_CFG)
    if not cfg:
        return None
    return "GAPPS" in cfg.upper()


def arm_translation_installed() -> bool:
    for name in ("waydroid.cfg", "waydroid_base.prop"):
        if "arm64-v8a" in _read_text(WAYDROID_DIR / name):
            return True
    return False


def cpu_vendor() -> str:
    """'amd', 'intel' ili 'unknown'."""
    info = _read_text(Path("/proc/cpuinfo"))
    if "AuthenticAMD" in info:
        return "amd"
    if "GenuineIntel" in info:
        return "intel"
    return "unknown"


def recommended_translation() -> str:
    """libndk je brži na AMD procesorima, libhoudini je Intel-ov."""
    return "libhoudini" if cpu_vendor() == "intel" else "libndk"


def extras_command() -> list[str] | None:
    """Pronalazi waydroid_script: AUR komandu ili kloniran repo sa venv-om."""
    exe = shutil.which("waydroid-extras")
    if exe:
        return [exe]
    py = SCRIPT_DIR / "venv/bin/python3"
    main = SCRIPT_DIR / "main.py"
    if py.exists() and main.exists():
        return [str(py), str(main)]
    return None


def arm_install_cmd(lib: str) -> list[str] | None:
    base = extras_command()
    if base is None:
        return None
    if len(base) == 1:                         # AUR komanda
        return ["pkexec", base[0], "install", lib]
    # kloniran repo: pkexec menja radni folder, pa se vraćamo u repo.
    # -B: root ne piše __pycache__ u korisnikov repo
    folder = shlex.quote(str(SCRIPT_DIR))
    return ["pkexec", "sh", "-c",
            f"cd {folder} && exec venv/bin/python3 -B main.py install {shlex.quote(lib)}"]


def describe_cmd(cmd: list[str]) -> str:
    """Čitljiv prikaz komande (za potvrdu pre pokretanja kao root)."""
    if cmd[:3] == ["pkexec", "sh", "-c"]:
        return cmd[3]
    return " ".join(shlex.quote(c) for c in cmd[1:] if c != "pkexec")


def _ignorable_untracked(path: str) -> bool:
    """Netrackovani fajlovi koji ne menjaju kod: venv i Python keš."""
    return (path.startswith("venv/") or path == "venv"
            or "__pycache__/" in path or path.startswith("__pycache__")
            or path.endswith((".pyc", ".pyo")))


def script_repo_state() -> str:
    """
    Stanje kloniranog waydroid_script repoa, jer se pokreće kao root:
      'aur'      — instaliran kao paket (vlasnik root, bezbedno)
      'clean'    — nijedan praćeni fajl nije menjan i nema novih .py fajlova
      'modified' — neko je menjao ili dodavao kod posle kloniranja
      'unknown'  — nije git repo ili git nije dostupan
    Paketi u venv/ se ne proveravaju.
    """
    if shutil.which("waydroid-extras"):
        return "aur"
    if not (SCRIPT_DIR / ".git").is_dir() or not shutil.which("git"):
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "status", "--porcelain",
             "--untracked-files=all", "--ignored=no"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith("?? ") and _ignorable_untracked(line[3:].strip().strip('"')):
            continue
        return "modified"
    return "clean"


def parse_android_id(text: str) -> str | None:
    """Android ID je jedina linija sastavljena samo od cifara."""
    for line in text.splitlines():
        line = line.strip()
        if re.fullmatch(r"\d{10,20}", line):
            return line
    return None


# ---------------------------------------------------------------- napredak `waydroid init`

# "[Downloading] 312.45 MB/812.00 MB    4123.00 kbps(approx.)" — hvatamo samo
# "X MB/Y MB", a brzinu i preostalo vreme računamo sami (ne zavisimo od formata)
_DOWNLOAD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MB\s*/\s*(\d+(?:\.\d+)?)\s*MB", re.I)

IMAGE_NAMES = {"system": "Sistemska slika", "vendor": "Vendor slika"}


@dataclass
class InitProgress:
    stage: str = "wait"      # wait, prepare, download, verify, extract
    image: str = ""          # system / vendor
    index: int = 0           # koje preuzimanje (1 ili 2)
    done_mb: float = 0.0
    total_mb: float = 0.0
    speed_mbs: float | None = None
    eta_s: float | None = None

    @property
    def fraction(self) -> float | None:
        if self.stage != "download" or self.total_mb <= 0:
            return None
        return max(0.0, min(1.0, self.done_mb / self.total_mb))


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return "manje od minuta"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"~{minutes} min"
    return f"~{minutes // 60} h {minutes % 60} min"


class InitProgressTracker:
    """
    Prati izlaz `waydroid init` i vodi računa o fazi i napretku preuzimanja.
    feed(chunk) vraća linije koje NISU napredak (one idu u log); linije
    napretka se samo "upijaju" u self.state.
    """

    SPEED_WINDOW_S = 6.0

    def __init__(self, clock=time.monotonic):
        self.state = InitProgress()
        self._clock = clock
        self._buf = ""
        self._announced: str | None = None     # slika najavljena log linijom
        self._samples: deque[tuple[float, float]] = deque()

    # ---------------------------------------------------------- ulaz

    def feed(self, chunk: str) -> list[str]:
        if self.state.stage == "wait" and chunk.strip():
            self.state.stage = "prepare"        # lozinka je prihvaćena, Waydroid radi
        self._buf += chunk.replace("\r\n", "\n")
        parts = re.split(r"[\r\n]", self._buf)
        self._buf = parts.pop()                  # nedovršena linija ostaje za sledeći put
        log = []
        for line in parts:
            if not self._consume(line) and line.strip():
                log.append(line.rstrip())
        # nedovršena linija napretka (Waydroid je piše bez \n): prikaži odmah,
        # ali samo ako izgleda celo (posle MB/MB sledi još teksta)
        m = _DOWNLOAD_RE.search(self._buf)
        if m and self._buf[m.end():].strip():
            self._progress(float(m.group(1)), float(m.group(2)))
        return log

    def flush(self) -> list[str]:
        rest, self._buf = self._buf, ""
        if rest.strip() and not self._consume(rest):
            return [rest.rstrip()]
        return []

    def _consume(self, line: str) -> bool:
        """True ako je linija napredak (ne ide u log). Ažurira fazu iz log linija."""
        m = _DOWNLOAD_RE.search(line)
        if m:
            self._progress(float(m.group(1)), float(m.group(2)))
            return True
        low = line.lower()
        if "download" in low:
            if "vendor" in low:
                self._announced = "vendor"
            elif "system" in low:
                self._announced = "system"
        elif "validat" in low or "verif" in low or "checksum" in low:
            self._set_stage("verify")
        elif "extract" in low or "unpack" in low:
            self._set_stage("extract")
        return False

    # ---------------------------------------------------------- stanje

    def _set_stage(self, stage: str):
        self.state.stage = stage
        self.state.speed_mbs = self.state.eta_s = None
        self._samples.clear()

    def _progress(self, done: float, total: float):
        st = self.state
        new_file = (st.stage != "download" or abs(total - st.total_mb) > 0.01
                    or done + 0.01 < st.done_mb)
        if new_file:
            st.index += 1
            st.image = self._announced or ("system" if st.index == 1 else "vendor")
            self._announced = None
            st.stage = "download"
            st.speed_mbs = st.eta_s = None
            self._samples.clear()
        st.done_mb, st.total_mb = done, total

        now = self._clock()
        self._samples.append((now, done))
        while len(self._samples) > 2 and now - self._samples[0][0] > self.SPEED_WINDOW_S:
            self._samples.popleft()
        t0, d0 = self._samples[0]
        if now - t0 >= 1.0 and done > d0:
            st.speed_mbs = (done - d0) / (now - t0)
            st.eta_s = max(0.0, total - done) / st.speed_mbs

    # ---------------------------------------------------------- prikaz

    def summary(self) -> str:
        st = self.state
        if st.stage == "wait":
            return "Čekam lozinku…"
        if st.stage == "prepare":
            return "Tražim najnoviju Android sliku…"
        if st.stage == "verify":
            return "Proveravam preuzete slike…"
        if st.stage == "extract":
            return "Raspakujem slike (može potrajati par minuta)…"
        name = IMAGE_NAMES.get(st.image, "Slika")
        parts = [f"{name} ({st.index}/2)", f"{st.done_mb:.0f} / {st.total_mb:.0f} MB"]
        if st.speed_mbs:
            parts.append(f"{st.speed_mbs:.1f} MB/s")
        if st.eta_s is not None and st.speed_mbs:
            parts.append(f"još {_fmt_eta(st.eta_s)}")
        return " · ".join(parts)
