"""
Glavni prozor DroidDeck-a.

Faza 1: status + Pokreni/Ugasi Android
Faza 2: mreža aplikacija (ui/app_grid.py)
Faza 3: Play Store dugme, instalacija APK-a (dugme + drag & drop)
Faza 4: čarobnjak za podešavanje (ui/setup_wizard.py)
Faza 5: tray (ui/tray.py), meni ☰ (više prozora, tema, tray), deinstalacija,
        poslednje pokretane igre
1.0.3:  navigacija (Nazad / Početna / Aplikacije), Zaustavi aplikaciju,
        čekanje da se Android stvarno podigne, veličina Android ekrana

Životni ciklus Androida (self.phase):
  OFF      ugašen
  STARTING pali se: kontejner (pkexec) -> Weston (X11) -> socket -> sesija
  BOOTING  sesija radi; čekamo sys.boot_completed = 1 (najviše BOOT_TIMEOUT_MS)
  ON       spreman za akcije
  STOPPING gasi se: sesija -> Weston (čekamo da se zaista ugasi)

Svaka faza koja čeka spoljašnji događaj ima vremenski limit, i svaki
QProcess sluša i finished i errorOccurred — nijedno stanje ne može da
ostane zaglavljeno. Poruke o greškama se prikazuju tek POSLE promene stanja
(QTimer.singleShot), da modalni dijalog ne bi blokirao prelaz dok poll radi.

Akcije koje traže upaljen Android idu kroz "pending" listu: DroidDeck sam
upali Android i izvrši ih tek kad je faza ON. Ista akcija (npr. pet duplih
klikova na istu igru) čeka samo jednom.
"""

import os
from collections.abc import Callable

from PyQt6.QtCore import (
    QEvent, QProcess, QProcessEnvironment, QSettings, QStandardPaths, QTimer, Qt,
)
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSystemTrayIcon, QToolButton,
    QVBoxLayout, QWidget,
)

import waydroid_api as wd
from ui import theme
from ui.app_grid import AppGrid
from ui.icon import app_icon
from ui.setup_wizard import SetupWizard
from ui.tray import Tray

POLL_MS = 3000                 # prozor vidljiv ili prelazna faza
POLL_IDLE_MS = 12_000          # prozor u tray-u, ništa se ne dešava
STATUS_TIMEOUT_MS = 10_000     # `waydroid status` koji visi se ubija
SOCKET_WAIT_MS = 250
SOCKET_WAIT_TRIES = 40         # 40 x 250 ms = 10 s
START_TIMEOUT_MS = 90_000      # od klika do RUNNING sesije
STOP_TIMEOUT_MS = 30_000
BOOT_CHECK_MS = 1500           # koliko često pitamo da li je Android podignut
BOOT_TIMEOUT_MS = 90_000       # posle ovoga ON i bez potvrde
BOOT_FALLBACK_MS = 6000        # ako `waydroid prop` uopšte ne radi
WESTON_EXIT_TRIES = 15         # 15 x 200 ms = 3 s, pa SIGKILL
LAUNCH_TIMEOUT_MS = 20_000
INSTALL_TIMEOUT_MS = 180_000   # jedan APK
RECENT_MAX = 5

OFF, STARTING, BOOTING, ON, STOPPING = "off", "starting", "booting", "on", "stopping"

# ključevi podešavanja
K_DARK = "ui/dark"
K_TRAY = "ui/close_to_tray"
K_TRAY_HINT = "ui/tray_hint_shown"
K_MULTI = "android/multi_window"
K_SCREEN = "android/screen_size"
K_RECENT = "apps/recent"
K_POLKIT_HINT = "ui/polkit_hint_shown"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DroidDeck")
        self.setWindowIcon(app_icon())
        self.resize(900, 620)
        self.setAcceptDrops(True)

        self.settings = QSettings()
        self._load_screen_size()
        self.status = wd.Status(False, False, False)
        self.phase = OFF
        self._waydroid_ok = wd.is_installed()
        self._socket_tries = 0
        self._weston_tries = 0
        self._restart_after_stop = False
        self._really_quit = False
        self._own_display = False      # Weston je otvorio DroidDeck (ne neko drugi)

        # akcije koje čekaju upaljen Android: (ključ, akcija)
        self._pending: list[tuple[str, Callable[[], None]]] = []
        self._install_queue: list[str] = []
        self._installing: str | None = None
        self._install_cancelled = False
        self._installed_ok = 0
        self._install_before: dict[str, float] = {}

        self._wizard: SetupWizard | None = None
        self._first_status = True
        self._procs: set[QProcess] = set()     # kratke komande u letu
        self._boot_checking = False

        self._build_ui()
        self._build_tray()

        # vremenski limit za trenutnu fazu (STARTING / STOPPING)
        self._phase_timer = QTimer(self, singleShot=True, timeout=self._on_phase_timeout)
        # BOOTING: periodična provera + gornji limit
        self._boot_poll = QTimer(self, interval=BOOT_CHECK_MS, timeout=self._check_booted)
        self._boot_timer = QTimer(self, singleShot=True, timeout=self._on_boot_timeout)
        # STOPPING: čekanje da se Weston zaista ugasi
        self._weston_timer = QTimer(self, interval=200, timeout=self._wait_weston_exit)

        # periodično čitanje statusa (+ čuvar ako komanda visi).
        # stdout i stderr zajedno: Waydroid poruku "not initialized" piše kroz logging
        self._status_proc = QProcess(self)
        self._status_proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._status_proc.finished.connect(self._on_status_finished)
        self._status_proc.errorOccurred.connect(self._on_status_error)
        self._status_watchdog = QTimer(self, singleShot=True, interval=STATUS_TIMEOUT_MS,
                                       timeout=self._status_proc.kill)
        self._poll = QTimer(self, interval=POLL_MS, timeout=self.refresh_status)
        self._poll.start()

        # pokretanje kontejnera (bez lozinke uz polkit pravilo, inače pkexec pita)
        self._container_proc = QProcess(self)
        self._container_proc.finished.connect(self._on_container_started)
        self._container_proc.errorOccurred.connect(self._on_container_error)

        # instalacija APK-a (+ čuvar da instalacija koja visi ne zaključa UI)
        self._install_proc = QProcess(self)
        self._install_proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._install_proc.finished.connect(self._on_install_finished)
        self._install_proc.errorOccurred.connect(self._on_install_error)
        self._install_guard = QTimer(self, singleShot=True, interval=INSTALL_TIMEOUT_MS,
                                     timeout=self._install_proc.kill)

        self._check_requirements()
        self._update_ui()
        if self._waydroid_ok:
            self.refresh_status()
        else:
            # bez Waydroid-a status nikad ne stiže — čarobnjak pokazuje šta fali
            self._first_status = False
            QTimer.singleShot(0, self.open_setup)

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # --- gornja traka
        top = QFrame(objectName="bar")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(14, 10, 14, 10)

        self.dot = QLabel("●")
        self.state_label = QLabel("Proveravam…")
        self.state_label.setStyleSheet("font-weight: bold;")

        self.menu_btn = QToolButton(text="☰")
        self.menu_btn.setToolTip("Opcije")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setMenu(self._build_options_menu())

        self.setup_btn = QPushButton("⚙  Podešavanje")
        self.setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_btn.clicked.connect(self.open_setup)

        self.power_btn = QPushButton("Pokreni Android", objectName="primary")
        self.power_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_btn.clicked.connect(self.toggle_power)

        top_l.addWidget(self.dot)
        top_l.addWidget(self.state_label)
        top_l.addStretch()
        top_l.addWidget(self.menu_btn)
        top_l.addWidget(self.setup_btn)
        top_l.addWidget(self.power_btn)

        # --- sredina: mreža aplikacija
        self.grid = AppGrid()
        self.grid.launch_requested.connect(self.launch_app)
        self.grid.stop_requested.connect(self.force_stop_app)
        self.grid.uninstall_requested.connect(self.uninstall_app)
        self.grid.reloaded.connect(lambda: self._refresh_recent())
        self.grid.can_stop = lambda: self.phase == ON

        # --- donja traka
        bottom = QFrame(objectName="bar")
        bottom_l = QHBoxLayout(bottom)
        bottom_l.setContentsMargins(14, 10, 14, 10)

        self.store_btn = QPushButton("Play Store")
        self.store_btn.clicked.connect(self.open_play_store)
        self.apk_btn = QPushButton("Instaliraj APK")
        self.apk_btn.clicked.connect(self.choose_apks)

        hint = QLabel("ili prevuci .apk u prozor", objectName="muted")

        # navigacija kao na telefonu (šalje taster Android-u)
        self.nav_btns = []
        for symbol, key, tip in (("◀", wd.KEY_BACK, "Nazad  (ili desni klik / Esc u Android-u)"),
                                 ("●", wd.KEY_HOME, "Početni ekran"),
                                 ("▢", wd.KEY_APP_SWITCH,
                                  "Otvorene aplikacije — odavde ih prevuci nagore da ih ugasiš")):
            btn = QToolButton(text=symbol, objectName="nav")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.send_key(k))
            self.nav_btns.append(btn)

        self.full_ui_btn = QPushButton("Pun Android ekran")
        self.full_ui_btn.clicked.connect(self.show_full_ui)

        bottom_l.addWidget(self.store_btn)
        bottom_l.addWidget(self.apk_btn)
        bottom_l.addWidget(hint)
        bottom_l.addStretch()
        for btn in self.nav_btns:
            bottom_l.addWidget(btn)
        bottom_l.addSpacing(8)
        bottom_l.addWidget(self.full_ui_btn)

        layout.addWidget(top)
        layout.addWidget(self.grid, 1)
        layout.addWidget(bottom)
        self.setCentralWidget(root)

    def _build_options_menu(self) -> QMenu:
        menu = QMenu(self)

        self.multi_action = QAction("Svaka aplikacija u svom prozoru", menu, checkable=True)
        self.multi_action.setChecked(self.settings.value(K_MULTI, False, type=bool))
        self.multi_action.toggled.connect(self.set_multi_window)
        menu.addAction(self.multi_action)

        # veličina Weston prozora (samo na X11, na Wayland-u nema Weston-a)
        if wd.needs_nested():
            size_menu = menu.addMenu("Veličina Android ekrana")
            group = QActionGroup(size_menu)
            current = (wd.NESTED_WIDTH, wd.NESTED_HEIGHT)
            for w, h in wd.SCREEN_SIZES:
                act = QAction(f"{w} × {h}", size_menu, checkable=True)
                act.setChecked((w, h) == current)
                act.triggered.connect(lambda _=False, w=w, h=h: self.set_screen_size(w, h))
                group.addAction(act)
                size_menu.addAction(act)
        menu.addSeparator()

        self.dark_action = QAction("Tamna tema", menu, checkable=True)
        self.dark_action.setChecked(self.settings.value(K_DARK, True, type=bool))
        self.dark_action.toggled.connect(self.set_dark)
        menu.addAction(self.dark_action)

        self.tray_action = QAction("Zatvaranje šalje u tray", menu, checkable=True)
        self.tray_action.setChecked(self.settings.value(K_TRAY, True, type=bool))
        self.tray_action.toggled.connect(lambda on: self.settings.setValue(K_TRAY, on))
        self.tray_action.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        menu.addAction(self.tray_action)

        menu.addSeparator()
        menu.addAction("Izlaz", self.quit_app)
        return menu

    def _build_tray(self):
        self.tray: Tray | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = Tray(self)
        self.tray.toggle_window.connect(self.toggle_window)
        self.tray.toggle_power.connect(self.toggle_power)
        self.tray.open_store.connect(self.open_play_store)
        self.tray.launch.connect(self.launch_app)
        self.tray.quit_requested.connect(self.quit_app)
        self.tray.show()
        self._refresh_recent()

    def _set_state(self, text: str, color: str, tray_state: str):
        self.state_label.setText(text)
        self.dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        if self.tray:
            self.tray.set_state(tray_state, text)

    def _transitional(self) -> bool:
        return (self.phase in (STARTING, BOOTING, STOPPING)
                or self._restart_after_stop or bool(self._installing))

    def _update_ui(self):
        s = self.status
        transitional = self.phase in (STARTING, STOPPING) or self._restart_after_stop

        if not self._waydroid_ok:
            self._set_state("Waydroid nije instaliran", theme.RED, "off")
        elif self._installing:
            self._set_state(f"Instaliram {os.path.basename(self._installing)}…",
                            theme.YELLOW, "busy")
        elif self._restart_after_stop:
            self._set_state("Restartujem Android…", theme.YELLOW, "busy")
        elif self.phase == STARTING:
            self._set_state("Palim Android…", theme.YELLOW, "busy")
        elif self.phase == STOPPING:
            self._set_state("Gasim Android…", theme.YELLOW, "busy")
        elif self.phase == BOOTING:
            self._set_state("Android se podiže…", theme.YELLOW, "busy")
        elif self._first_status:
            self._set_state("Proveravam…", theme.YELLOW, "busy")
        elif not s.initialized:
            self._set_state("Waydroid nije inicijalizovan", theme.RED, "off")
        elif self.phase == ON:
            self._set_state("Android radi", theme.GREEN, "on")
        else:
            self._set_state("Android je ugašen", theme.RED, "off")

        running = self.phase in (BOOTING, ON)
        self.power_btn.setText("Ugasi Android" if running else "Pokreni Android")
        # tokom instalacije se Android i dalje može ugasiti (gašenje prekida instalaciju)
        self.power_btn.setEnabled(self._waydroid_ok and s.initialized and not transitional
                                  and (running or not self._installing))

        can_act = (self._waydroid_ok and s.initialized and self.phase != STOPPING
                   and not self._restart_after_stop)
        self.store_btn.setEnabled(can_act)
        self.apk_btn.setEnabled(can_act)
        self.full_ui_btn.setEnabled(self.phase == ON)
        for btn in self.nav_btns:
            btn.setEnabled(self.phase == ON)
        if self.tray:
            self.tray.set_power(running, self.power_btn.isEnabled())
            self.tray.store_action.setEnabled(can_act)
        self._adjust_poll()

    def _adjust_poll(self):
        """Brz poll dok se nešto dešava ili je prozor otvoren, spor u tray-u."""
        if not hasattr(self, "_poll"):
            return
        visible = self.isVisible() and not self.isMinimized()
        interval = POLL_MS if (visible or self._transitional()) else POLL_IDLE_MS
        if self._poll.interval() != interval:
            self._poll.setInterval(interval)

    def _warn_later(self, message: str):
        """Poruka posle trenutnog slota — stanje se menja odmah, dijalog ne blokira."""
        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "DroidDeck", message))

    # ------------------------------------------------------------ provere

    def _check_requirements(self):
        if not self._waydroid_ok:
            return                          # čarobnjak (korak 1) objašnjava šta fali
        if wd.needs_nested() and not wd.compositor_available():
            QMessageBox.warning(
                self, "DroidDeck",
                "Radiš na X11 sesiji, a Weston nije instaliran.\n\n"
                "Instaliraj ga: sudo pacman -S weston",
            )

    # ------------------------------------------------------------ pomoćne komande

    def _run_cmd(self, cmd: list[str], on_done: Callable[[int, str], None] | None = None,
                 timeout_ms: int = 60_000, env: dict | None = None):
        """
        Kratka komanda kroz QProcess; on_done(exit_code, izlaz) se poziva TAČNO
        jednom — i kad se proces završi, i kad ne može da krene, i kad istekne vreme
        (tada je exit_code različit od 0).
        """
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if env is not None:
            penv = QProcessEnvironment()
            for key, value in env.items():
                penv.insert(key, value)
            proc.setProcessEnvironment(penv)
        self._procs.add(proc)
        guard = QTimer(proc, singleShot=True, interval=timeout_ms, timeout=proc.kill)
        done_once = {"v": False}

        def finish(code: int, out: str):
            if done_once["v"]:
                return
            done_once["v"] = True
            guard.stop()
            self._procs.discard(proc)
            proc.deleteLater()
            if on_done:
                on_done(code, out)

        def finished(code, status):
            out = bytes(proc.readAll()).decode(errors="replace")
            if status == QProcess.ExitStatus.CrashExit:
                code = code or -2
                out = (out + "\n(komanda je prekinuta — isteklo vreme)").strip()
            finish(code, out)

        def error(err):
            if err == QProcess.ProcessError.FailedToStart:
                finish(-1, f"Komanda nije pokrenuta: {cmd[0]}")

        proc.finished.connect(finished)
        proc.errorOccurred.connect(error)
        guard.start()
        proc.start(cmd[0], cmd[1:])

    # ------------------------------------------------------------ status

    def refresh_status(self):
        self._waydroid_ok = wd.is_installed()
        if not self._waydroid_ok:
            self._update_ui()
            return
        if self._status_proc.state() == QProcess.ProcessState.NotRunning:
            self._status_watchdog.start()
            self._status_proc.start(wd.STATUS_CMD[0], wd.STATUS_CMD[1:])

    def _on_status_error(self, err):
        if err == QProcess.ProcessError.FailedToStart:
            self._status_watchdog.stop()

    def _on_status_finished(self):
        self._status_watchdog.stop()
        text = bytes(self._status_proc.readAll()).decode(errors="replace")
        status = wd.parse_status(text)
        if status is None and not wd.initialized_on_disk():
            status = wd.Status(initialized=False, container=False, session=False)
        if status is None:
            return                          # neupotrebljiv izlaz: zadrži staro stanje
        self.status = status

        if self.phase == STARTING and status.session:
            self._phase_timer.stop()
            self._enter_booting()
        elif (self.phase == STOPPING and not status.session
              and not self._weston_timer.isActive()):
            self._phase_timer.stop()
            self._begin_weston_exit()       # OFF tek kad se Weston stvarno ugasi
        elif self.phase == OFF and status.session and not self._weston_timer.isActive():
            self.phase = ON                 # upaljen spolja (ili pre DroidDeck-a)
        elif self.phase in (BOOTING, ON) and not status.session:
            self._stop_boot_wait()
            self._pending.clear()           # ugašen spolja ili pukao
            self._cancel_installs(deliberate=False)
            self.phase = STOPPING
            self._begin_weston_exit()

        # zatvoren NAŠ Weston prozor = korisnik želi da ugasi Android
        if (self.phase in (BOOTING, ON) and self._own_display
                and not wd.compositor_running()):
            self.statusBar().showMessage("Android prozor je zatvoren — gasim Android.", 5000)
            self._stop()

        # prvo pokretanje: otvori čarobnjak ako podešavanje nije završeno
        if self._first_status:
            self._first_status = False
            if not SetupWizard.is_done() or not status.initialized:
                QTimer.singleShot(0, self.open_setup)
            elif self.phase == ON:
                self._sync_multi_window()

        self._update_ui()

    # ------------------------------------------------------------ podizanje sistema

    def _enter_booting(self):
        self.phase = BOOTING
        self._boot_checking = False
        self._boot_timer.start(BOOT_TIMEOUT_MS)
        self._boot_poll.start()

    def _stop_boot_wait(self):
        self._boot_poll.stop()
        self._boot_timer.stop()

    def _check_booted(self):
        """sys.boot_completed = 1 → Android (i Waydroid-ov servis za aplikacije) je spreman."""
        if self.phase != BOOTING or self._boot_checking:
            return
        self._boot_checking = True

        def done(code, out):
            self._boot_checking = False
            if self.phase != BOOTING:
                return
            if code == -1:
                # `waydroid prop` ne može ni da krene: stari način (kratko čekanje)
                self._boot_poll.stop()
                self._boot_timer.start(BOOT_FALLBACK_MS)
            elif code == 0 and wd.parse_boot_completed(out):
                self._on_booted()
        self._run_cmd(wd.get_prop_cmd(wd.BOOT_PROP), done, timeout_ms=5000)

    def _on_boot_timeout(self):
        if self.phase != BOOTING:
            return
        self.statusBar().showMessage(
            "Android nije potvrdio da je podignut — nastavljam svejedno.", 8000)
        self._on_booted()

    def _on_booted(self):
        if self.phase != BOOTING:
            return
        self._stop_boot_wait()
        self.phase = ON
        self._update_ui()
        self._reconcile_multi_window()
        self._run_pending()

    # ------------------------------------------------------------ gašenje Weston-a

    def _begin_weston_exit(self):
        """Sesija je ugašena; ugasi i Weston i sačekaj ga pre nego što proglasiš OFF."""
        if not (self._own_display or wd.compositor_running()) or not wd.needs_nested():
            self._enter_off()
            return
        wd.stop_compositor()
        self._weston_tries = 0
        self._weston_timer.start()

    def _wait_weston_exit(self):
        if not wd.compositor_running():
            self._weston_timer.stop()
            self._enter_off()
            return
        self._weston_tries += 1
        if self._weston_tries == WESTON_EXIT_TRIES:
            wd.stop_compositor(force=True)
        elif self._weston_tries > WESTON_EXIT_TRIES + 10:
            self._weston_timer.stop()       # ni SIGKILL nije pomogao; ne blokiraj UI
            self._enter_off()

    def _enter_off(self):
        self.phase = OFF
        self._own_display = False
        self._update_ui()
        if self._restart_after_stop:
            QTimer.singleShot(500, self._start)    # _start gasi zastavicu

    def _on_phase_timeout(self):
        if self.phase == STARTING:
            # prvo ugasi (sesija može da zakasni i upali se), pa tek onda javi
            self._pending.clear()
            self._install_queue.clear()
            self._kill_container_proc()
            self.phase = STOPPING
            self._phase_timer.start(STOP_TIMEOUT_MS)
            wd.stop_session()
            self._update_ui()
            self._warn_later(
                "Android se nije upalio za 90 sekundi.\n\n"
                "Proveri ⚙ Podešavanje (korak 1), ili u terminalu pokreni\n"
                "waydroid log  da vidiš šta se desilo.")
        elif self.phase == STOPPING:
            self._restart_after_stop = False
            if self.status.session:
                self.phase = ON
                self._update_ui()
            else:
                self._begin_weston_exit()
            self._warn_later("Android ne odgovara na gašenje.\n\n"
                             "Pokušaj ponovo, ili u terminalu:\n"
                             "sudo systemctl restart waydroid-container")

    # ------------------------------------------------------------ pending akcije

    def _when_ready(self, key: str, action: Callable[[], None],
                    message: str | None = None) -> bool:
        """Izvrši odmah ako je Android spreman, inače ga upali pa izvrši."""
        if self.phase == ON:
            action()
            return True
        if self.phase == STOPPING or self._restart_after_stop:
            self.statusBar().showMessage("Android se gasi — sačekaj pa pokušaj ponovo.", 5000)
            return False
        if not self._waydroid_ok or not self.status.initialized:
            self.open_setup()
            return False
        if any(k == key for k, _ in self._pending):
            self.statusBar().showMessage("Već čeka — Android se pali…", 3000)
            return True
        self._pending.append((key, action))
        if message:
            self.statusBar().showMessage(message, 5000)
        if self.phase == OFF:
            self._start()
        return True

    def _run_pending(self):
        if self.phase != ON:
            return
        actions, self._pending = self._pending, []
        for _key, action in actions:
            action()
        self._next_install()

    # ------------------------------------------------------------ podešavanje

    def open_setup(self):
        if self._wizard is None:
            self._wizard = SetupWizard(lambda: self.status, self)
            self._wizard.finished_setup.connect(self.grid.reload)
        self.show_window()
        self._wizard.show()
        self._wizard.raise_()
        self._wizard.activateWindow()

    # ------------------------------------------------------------ aplikacije

    def launch_app(self, package: str, name: str):
        def go():
            self.statusBar().showMessage(f"Pokrećem {name}…", 4000)

            def done(code, out):
                if code != 0:
                    self._warn_later(
                        f"{name} nije pokrenut.\n\nWaydroid kaže:\n"
                        f"{out.strip()[-500:] or '(nema poruke)'}\n\n"
                        "Savet: desni klik → Zaustavi aplikaciju, pa probaj ponovo.")
            self._run_cmd(wd.launch_cmd(package), done,
                          timeout_ms=LAUNCH_TIMEOUT_MS, env=wd.wayland_env())
        if self._when_ready(f"launch:{package}", go, f"Palim Android za {name}…"):
            self._remember_recent(package)

    def force_stop_app(self, package: str, name: str):
        cmd = wd.force_stop_cmd(package)
        if cmd is None or self.phase != ON:
            return
        self._polkit_hint()

        def done(code, out):
            if code == 0:
                self.statusBar().showMessage(f"Zaustavljeno: {name}", 4000)
            elif code not in (126, 127):    # 126/127 = otkazana lozinka
                self._warn_later(f"Zaustavljanje nije uspelo: {name}\n\n{out[-400:]}")
        self._run_cmd(cmd, done, timeout_ms=30_000)

    def send_key(self, key: int):
        cmd = wd.keyevent_cmd(key)
        if cmd is None or self.phase != ON:
            return
        self._polkit_hint()

        def done(code, out):
            if code not in (0, 126, 127):
                self.statusBar().showMessage("Taster nije poslat Android-u.", 4000)
        self._run_cmd(cmd, done, timeout_ms=15_000)

    def _polkit_hint(self):
        """Jednom objasni zašto se traži lozinka ako polkit pravilo nije instalirano."""
        if wd.polkit_rule_installed() or self.settings.value(K_POLKIT_HINT, False, type=bool):
            return
        self.settings.setValue(K_POLKIT_HINT, True)
        QMessageBox.information(
            self, "DroidDeck",
            "Ova komanda se šalje Android-u kao administrator, pa sistem traži lozinku.\n\n"
            "Instaliraj DroidDeck kao paket (makepkg -si) — paket donosi polkit "
            "pravilo i lozinka više neće biti potrebna za navigaciju, zaustavljanje "
            "aplikacija i paljenje Androida.",
        )

    def show_full_ui(self):
        if not wd.show_full_ui():
            self.statusBar().showMessage("Nije moguće otvoriti pun Android ekran.", 5000)

    def open_play_store(self):
        if not wd.play_store_installed() and self.phase == ON:
            QMessageBox.information(
                self, "DroidDeck",
                "Play Store nije pronađen.\n\n"
                "Waydroid je verovatno inicijalizovan bez Google servisa. "
                "Otvori ⚙ Podešavanje, korak 2.",
            )
            return
        self.launch_app(wd.PLAY_STORE_PKG, "Play Store")

    def uninstall_app(self, package: str, name: str):
        answer = QMessageBox.question(
            self, "DroidDeck",
            f"Deinstalirati „{name}“?\n\nBrišu se i svi podaci te aplikacije "
            "(sačuvane igre, nalozi…).",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(code, out):
            if code != 0:
                QMessageBox.warning(self, "DroidDeck",
                                    f"Deinstalacija nije uspela: {name}\n\n{out[-500:]}")
                return
            self.statusBar().showMessage(f"Deinstalirano: {name}", 5000)
            self._forget_recent(package)
            # Waydroid briše .desktop fajl, watcher osvežava mrežu; ovo je rezerva
            QTimer.singleShot(1500, self.grid.reload)

        self._when_ready(f"uninstall:{package}",
                         lambda: self._run_cmd(wd.uninstall_cmd(package), done),
                         f"Palim Android za deinstalaciju {name}…")

    # ------------------------------------------------------------ poslednje pokretano

    def _recent(self) -> list[str]:
        value = self.settings.value(K_RECENT, [])
        if isinstance(value, str):          # QSettings vraća str za listu od 1
            value = [value]
        return list(value or [])

    def _remember_recent(self, package: str):
        if package == wd.PLAY_STORE_PKG:
            return
        recent = [p for p in self._recent() if p != package]
        recent.insert(0, package)
        self.settings.setValue(K_RECENT, recent[:RECENT_MAX])
        self._refresh_recent()

    def _forget_recent(self, package: str):
        self.settings.setValue(K_RECENT, [p for p in self._recent() if p != package])
        self._refresh_recent()

    def _refresh_recent(self):
        if not getattr(self, "tray", None):     # mreža se puni pre tray-a
            return
        items = []
        for package in self._recent():
            name = self.grid.name_for_package(package)
            if name:                        # preskoči deinstalirane
                items.append((package, name, self.grid.icon_for_package(package)))
        self.tray.set_recent(items)

    # ------------------------------------------------------------ opcije

    def set_dark(self, dark: bool):
        self.settings.setValue(K_DARK, dark)
        QApplication.instance().setStyleSheet(theme.qss_for(dark))

    def _load_screen_size(self):
        value = str(self.settings.value(K_SCREEN, "1280x720"))
        try:
            w, h = (int(x) for x in value.lower().split("x"))
        except ValueError:
            w, h = wd.SCREEN_SIZES[0]
        if (w, h) not in wd.SCREEN_SIZES:
            w, h = wd.SCREEN_SIZES[0]
        wd.set_screen_size(w, h)

    def set_screen_size(self, w: int, h: int):
        self.settings.setValue(K_SCREEN, f"{w}x{h}")
        wd.set_screen_size(w, h)
        if self.phase in (BOOTING, ON):
            answer = QMessageBox.question(
                self, "DroidDeck",
                f"Nova veličina ({w} × {h}) važi posle restarta Androida.\n\n"
                "Restartovati sada?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.restart_android()
        else:
            self.statusBar().showMessage(f"Android ekran: {w} × {h}", 4000)

    def set_multi_window(self, on: bool):
        """Željeno stanje se čuva u podešavanjima; Android ga dobija pri paljenju."""
        self.settings.setValue(K_MULTI, on)
        if self.phase == ON:
            self._apply_multi_window(on, ask_restart=True)
        else:
            self.statusBar().showMessage("Primeniće se kad se Android upali.", 5000)

    def _apply_multi_window(self, on: bool, ask_restart: bool):
        def done(code, out):
            if code != 0:
                QMessageBox.warning(self, "DroidDeck",
                                    f"Podešavanje prozora nije uspelo.\n\n{out[-400:]}")
                return
            answer = QMessageBox.question(
                self, "DroidDeck",
                "Promena prikaza važi posle restarta Androida.\n\nRestartovati sada?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.restart_android()
        self._run_cmd(wd.set_prop_cmd(wd.MULTI_WINDOW_PROP, str(on).lower()),
                      done if ask_restart else None)

    def _reconcile_multi_window(self):
        """Pri svakom paljenju: ako Android ne odgovara podešavanju, uskladi ga."""
        wanted = self.settings.value(K_MULTI, False, type=bool)

        def done(code, out):
            actual = wd.parse_bool_prop(out) if code == 0 else None
            if actual is None:
                actual = False              # prop nije postavljen = isključeno
            if actual != wanted and self.phase == ON:
                self._apply_multi_window(wanted, ask_restart=True)
        self._run_cmd(wd.get_prop_cmd(wd.MULTI_WINDOW_PROP), done)

    def _sync_multi_window(self):
        """Android je radio pre DroidDeck-a: meni preuzima njegovo stvarno stanje."""
        def done(code, out):
            value = wd.parse_bool_prop(out) if code == 0 else None
            if value is not None and value != self.multi_action.isChecked():
                self.multi_action.blockSignals(True)
                self.multi_action.setChecked(value)
                self.multi_action.blockSignals(False)
                self.settings.setValue(K_MULTI, value)
        self._run_cmd(wd.get_prop_cmd(wd.MULTI_WINDOW_PROP), done)

    # ------------------------------------------------------------ APK

    def choose_apks(self):
        folder = (QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation) or os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Izaberi APK", folder, "Android paketi (*.apk)",
        )
        self.install_apks(paths)

    def install_apks(self, paths: list[str]):
        apks = [p for p in paths if wd.is_apk(p)]
        if not apks:
            return
        if self.phase == STOPPING or self._restart_after_stop:
            self.statusBar().showMessage("Android se gasi — sačekaj pa pokušaj ponovo.", 5000)
            return
        if not self._waydroid_ok or not self.status.initialized:
            self.open_setup()
            return

        self._install_queue.extend(apks)
        if not self._installing:
            self._installed_ok = 0

        if self.phase == ON:
            self._next_install()
        else:
            self.statusBar().showMessage(
                f"Palim Android za instalaciju ({len(self._install_queue)} APK)…", 5000)
            if self.phase == OFF:
                self._start()
        self._update_ui()

    def _next_install(self):
        if self._installing or not self._install_queue or self.phase != ON:
            return
        self._installing = self._install_queue.pop(0)
        self._install_cancelled = False
        self._install_before = wd.package_stamps()
        prog, *args = wd.install_cmd(self._installing)
        self._install_guard.start()
        self._install_proc.start(prog, args)
        self._update_ui()

    def _cancel_installs(self, deliberate: bool):
        """Android se gasi: red se briše, tekuća instalacija se prekida."""
        self._install_queue.clear()
        if self._install_proc.state() != QProcess.ProcessState.NotRunning:
            self._install_cancelled = deliberate
            self._install_proc.kill()       # finished -> _finish_install

    def _on_install_error(self, err):
        if err == QProcess.ProcessError.FailedToStart and self._installing:
            self._finish_install(-1, "Komanda za instalaciju nije pokrenuta.")

    def _on_install_finished(self, exit_code, exit_status):
        output = bytes(self._install_proc.readAll()).decode(errors="replace").strip()
        if exit_status == QProcess.ExitStatus.CrashExit:
            exit_code = exit_code or -1
            output = (output + "\n\nInstalacija je prekinuta "
                      "(isteklo vreme ili je Android ugašen).").strip()
        self._finish_install(exit_code, output)

    def _finish_install(self, exit_code: int, output: str):
        self._install_guard.stop()
        if not self._installing:
            return
        name = os.path.basename(self._installing)
        self._installing = None

        if self._install_cancelled:
            self._install_cancelled = False
            self.statusBar().showMessage(f"Instalacija prekinuta: {name}", 5000)
        elif exit_code == 0:
            self._installed_ok += 1
            self.statusBar().showMessage(f"Instalirano: {name}", 5000)
            if self.tray and not self.isVisible():
                self.tray.showMessage("DroidDeck", f"Instalirano: {name}",
                                      QSystemTrayIcon.MessageIcon.Information, 4000)
            # Waydroid ume da vrati 0 i kad Android odbije APK: proveri ikonice kasnije
            before = self._install_before
            QTimer.singleShot(8000, lambda: self._verify_install(name, before))
        else:
            self._warn_later(
                f"Instalacija nije uspela: {name}\n\n{output[-600:] or 'Nema detalja.'}")

        if self._install_queue:
            # ne pokreći isti QProcess iz njegovog signala — Qt to tiho odbije
            QTimer.singleShot(0, self._next_install)
        else:
            if self._installed_ok > 1:
                self.statusBar().showMessage(f"Instalirano {self._installed_ok} APK-a", 5000)
            self._installed_ok = 0
            # ikonice obično stignu same (QFileSystemWatcher), ovo je rezerva
            QTimer.singleShot(3000, self.grid.reload)
        self._update_ui()

    def _verify_install(self, name: str, before: dict[str, float]):
        """
        Nova aplikacija = novi .desktop; ažuriranje = izmenjen .desktop.
        Ako se nijedno nije desilo, APK verovatno nije instaliran.
        """
        if self.phase != ON or self._installing:
            return
        after = wd.package_stamps()
        changed = any(pkg not in before or before[pkg] != stamp
                      for pkg, stamp in after.items())
        if not changed:
            self.statusBar().showMessage(
                f"{name}: ikonica se nije pojavila niti promenila — "
                "instalacija verovatno nije uspela.", 10_000)

    # ------------------------------------------------------------ drag & drop

    @staticmethod
    def _dropped_apks(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith(".apk")]

    def dragEnterEvent(self, event):
        apks = self._dropped_apks(event)
        if apks:
            event.acceptProposedAction()
            self.statusBar().showMessage(f"Pusti da instaliraš ({len(apks)} APK)", 10_000)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.statusBar().clearMessage()

    def dropEvent(self, event):
        self.statusBar().clearMessage()
        apks = self._dropped_apks(event)
        if apks:
            event.acceptProposedAction()
            self.install_apks(apks)

    # ------------------------------------------------------------ prozor / tray

    def _sync_window_state(self):
        """Natpis u tray meniju i brzina poll-a prate stvarno stanje prozora."""
        if getattr(self, "tray", None):
            self.tray.set_window_visible(self.isVisible() and not self.isMinimized())
        self._adjust_poll()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_window_state()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_window_state()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_state()

    def show_window(self):
        if self.isMinimized():
            self.showNormal()               # vraća i maksimizovan prozor kakav je bio
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def toggle_window(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_window()

    def _close_to_tray(self) -> bool:
        return (self.tray is not None and not self._really_quit
                and self.settings.value(K_TRAY, True, type=bool))

    def quit_app(self):
        self._really_quit = True
        if self.close():
            QApplication.instance().quit()
        else:
            self._really_quit = False

    def closeEvent(self, event):
        if self._close_to_tray():
            event.ignore()
            self.hide()
            if not self.settings.value(K_TRAY_HINT, False, type=bool):
                self.settings.setValue(K_TRAY_HINT, True)
                self.tray.showMessage(
                    "DroidDeck", "I dalje radim u tray-u. Desni klik na ikonicu za meni.",
                    QSystemTrayIcon.MessageIcon.Information, 4000)
            return

        # izlaz prekida instalaciju u toku — pitaj
        if self._installing:
            left = len(self._install_queue)
            extra = f" (i još {left} u redu)" if left else ""
            answer = QMessageBox.question(
                self, "DroidDeck",
                f"Instalacija {os.path.basename(self._installing)}{extra} je u toku.\n\n"
                "Prekinuti je i izaći?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._install_queue.clear()
            self._install_guard.stop()
            self._install_cancelled = True
            self._install_proc.kill()
            self._install_proc.waitForFinished(2000)

        # čarobnjak je zaseban prozor; zatvori ga zajedno sa glavnim
        if self._wizard is not None and self._wizard.isVisible():
            if not self._wizard.close():
                event.ignore()
                return
        self._poll.stop()
        if self.tray:
            self.tray.hide()
        super().closeEvent(event)

    # ------------------------------------------------------------ start / stop

    def toggle_power(self):
        if self.phase in (BOOTING, ON):
            self._stop()
        elif self.phase == OFF:
            self._start()

    def restart_android(self):
        if self.phase == OFF:
            self._start()
            return
        if self.phase not in (BOOTING, ON):
            return
        self._restart_after_stop = True
        self._stop(keep_pending=True)

    def _start(self):
        self._restart_after_stop = False
        if self.phase != OFF:
            return
        if not self._waydroid_ok or not self.status.initialized:
            self._pending.clear()
            self._install_queue.clear()
            self._update_ui()
            self._warn_later("Waydroid nije spreman.\n\nOtvori ⚙ Podešavanje.")
            return

        self.phase = STARTING
        self._phase_timer.start(START_TIMEOUT_MS)
        self._update_ui()

        if wd.container_service_active():
            self._start_display()
        else:
            self._kill_container_proc()     # zaostali pkexec od prošlog pokušaja
            prog, *args = wd.START_CONTAINER_CMD
            self._container_proc.start(prog, args)

    def _kill_container_proc(self):
        if self._container_proc.state() != QProcess.ProcessState.NotRunning:
            self._container_proc.kill()
            self._container_proc.waitForFinished(1000)

    def _abort_start(self, message: str):
        self._phase_timer.stop()
        self._kill_container_proc()
        self.phase = OFF
        self._own_display = False
        self._pending.clear()
        self._install_queue.clear()
        self._update_ui()
        self._warn_later(message)

    def _on_container_error(self, err):
        if err == QProcess.ProcessError.FailedToStart and self.phase == STARTING:
            self._abort_start("Kontejner nije pokrenut: pkexec ili systemctl nije dostupan.")

    def _on_container_started(self, exit_code, _status):
        if self.phase != STARTING:
            return
        if exit_code != 0:
            self._abort_start("Kontejner nije pokrenut (otkazana lozinka?).\n\n"
                              "Savet: u ⚙ Podešavanju uključi Waydroid servis, "
                              "pa lozinka više neće biti potrebna.")
            return
        self._start_display()

    def _start_display(self):
        """Na X11 prvo podigni Weston pa sačekaj njegov socket."""
        if wd.needs_nested() and not wd.compositor_available():
            self._abort_start("Weston nije instaliran, a potreban je na X11 sesiji.\n\n"
                              "Instaliraj ga:  sudo pacman -S weston")
            return
        if not wd.start_compositor():
            self._abort_start("Weston nije mogao da se pokrene.")
            return
        self._own_display = wd.needs_nested()
        self._socket_tries = 0
        self._wait_for_socket()

    def _wait_for_socket(self):
        if self.phase != STARTING:
            return
        if wd.compositor_ready():
            if not wd.start_session():
                wd.stop_compositor()
                self._abort_start("Komanda  waydroid session start  nije pokrenuta.")
            return
        self._socket_tries += 1
        if self._socket_tries > SOCKET_WAIT_TRIES:
            wd.stop_compositor()
            self._abort_start("Weston se nije pokrenuo na vreme.")
            return
        QTimer.singleShot(SOCKET_WAIT_MS, self._wait_for_socket)

    def _stop(self, keep_pending: bool = False):
        if self.phase not in (BOOTING, ON):
            return
        if not keep_pending:
            self._pending.clear()
            self._cancel_installs(deliberate=True)
        self._stop_boot_wait()
        self.phase = STOPPING
        self._phase_timer.start(STOP_TIMEOUT_MS)
        self._update_ui()
        wd.stop_session()
