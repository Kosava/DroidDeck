"""
Čarobnjak za podešavanje — Faza 4.

Umesto klasičnog "Dalje / Nazad" čarobnjaka: jedna lista od 4 koraka,
svaki sa statusom (✓ / ✗ / •) i dugmetom za akciju. Odmah se vidi šta
je gotovo, a šta ne, i može se ponovo otvoriti kad god zatreba.

  1. Sistem          — Waydroid, binder u kernelu, Weston (na X11), servis
  2. Android slika   — waydroid init sa GAPPS (Play Store)
  3. Google nalog    — Android ID + registracija kod Google-a
  4. Igre (ARM)      — libndk / libhoudini preko waydroid_script
"""

from PyQt6.QtCore import QProcess, QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout,
)

import waydroid_api as wd
from ui import theme

SETTINGS_KEY = "setup/done"
K_ANDROID_ID = "setup/android_id"
K_ANDROID_ID_STAMP = "setup/android_id_cfg_mtime"   # ID važi samo za ovu instalaciju

OK, BAD, TODO = "ok", "bad", "todo"
MARKS = {OK: ("✓", theme.GREEN), BAD: ("✗", theme.RED), TODO: ("•", theme.YELLOW)}


class StepRow(QFrame):
    """Jedan red: oznaka · naslov + opis · dugme."""

    def __init__(self, number: int, title: str, parent=None):
        super().__init__(parent, objectName="bar")
        # red nikad ne sme biti niži od svog teksta
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        self.mark = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.mark.setFixedWidth(24)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(f"{number}. {title}")
        self.title.setStyleSheet("font-weight: bold;")
        self.desc = QLabel(objectName="muted", wordWrap=True)
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(self.title)
        text.addWidget(self.desc)

        # traka napretka (samo dok korak radi nešto dugo, npr. preuzimanje)
        self.progress = QProgressBar(textVisible=True)
        self.progress.setFixedHeight(18)
        self.progress_text = QLabel(objectName="muted", wordWrap=True)
        self.progress.hide()
        self.progress_text.hide()
        text.addSpacing(4)
        text.addWidget(self.progress)
        text.addWidget(self.progress_text)

        self.button = QPushButton()
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.hide()

        lay.addWidget(self.mark)
        lay.addLayout(text, 1)
        lay.addWidget(self.button)

    def set(self, state: str, desc: str, button: str | None = None, primary=False):
        sym, color = MARKS[state]
        self.mark.setText(sym)
        self.mark.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold;")
        self.desc.setText(desc)
        if button:
            self.button.setText(button)
            self.button.setObjectName("primary" if primary else "")
            self.button.style().unpolish(self.button)
            self.button.style().polish(self.button)
            self.button.show()
        else:
            self.button.hide()

    def set_progress(self, fraction: float | None, text: str):
        """fraction 0..1 = poznat napredak; None = "radim" animacija."""
        if fraction is None:
            if self.progress.maximum() != 0:
                self.progress.setRange(0, 0)
        else:
            if self.progress.maximum() != 1000:
                self.progress.setRange(0, 1000)
                self.progress.setFormat("%p%")
            self.progress.setValue(int(fraction * 1000))
        self.progress_text.setText(text)
        self.progress.show()
        self.progress_text.show()

    def hide_progress(self):
        self.progress.hide()
        self.progress_text.hide()


class SetupWizard(QDialog):
    finished_setup = pyqtSignal()

    def __init__(self, get_status, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DroidDeck — podešavanje")
        self.resize(680, 720)
        self._get_status = get_status          # funkcija koja vraća wd.Status
        self._android_id: str | None = QSettings().value(K_ANDROID_ID, None) or None
        self._check_android_id_stamp()
        self._service_enabled = False

        # jedan QProcess za sve duge komande (init, ID, ARM, servis)
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyRead.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        self._job: str | None = None
        self._job_output = ""
        self._tracker: wd.InitProgressTracker | None = None

        self._build_ui()

        # provere su jeftine (čitanje fajlova), ali rade samo dok je prozor otvoren
        self._timer = QTimer(self, interval=2000, timeout=self.refresh)
        self.refresh()

    def showEvent(self, event):
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        intro = QLabel(
            "Prođi kroz korake redom. Prozor može da ostane otvoren dok "
            "u glavnom prozoru pališ Android.",
            objectName="muted", wordWrap=True,
        )
        lay.addWidget(intro)

        self.step_system = StepRow(1, "Sistem")
        self.step_image = StepRow(2, "Android sa Play Store-om")
        self.step_google = StepRow(3, "Google sertifikacija")
        self.step_arm = StepRow(4, "Podrška za igre (ARM)")

        self.step_system.button.clicked.connect(self._system_action)
        self.step_image.button.clicked.connect(self._run_init)
        self.step_google.button.clicked.connect(self._google_action)
        self.step_arm.button.clicked.connect(self._arm_action)

        for row in (self.step_system, self.step_image, self.step_google, self.step_arm):
            lay.addWidget(row)

        self.log = QPlainTextEdit(readOnly=True, objectName="log")
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(150)
        self.log.hide()
        lay.addWidget(self.log)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        self.job_label = QLabel(objectName="muted")
        self.done_btn = QPushButton("Gotovo", objectName="primary")
        self.done_btn.clicked.connect(self._finish)
        bottom.addWidget(self.job_label, 1)
        bottom.addWidget(self.done_btn)
        lay.addLayout(bottom)

    # ------------------------------------------------------------ provere

    def _check_android_id_stamp(self):
        """Android reinstaliran (i iz terminala) → sačuvani ID više ne važi."""
        if not self._android_id:
            return
        saved = QSettings().value(K_ANDROID_ID_STAMP, 0.0, type=float)
        if abs(saved - wd.cfg_stamp()) > 0.001:
            self._android_id = None
            QSettings().remove(K_ANDROID_ID)
            QSettings().remove(K_ANDROID_ID_STAMP)

    def refresh(self):
        status = self._get_status()
        busy = self._job is not None
        if not busy:
            self._check_android_id_stamp()
        for row in (self.step_system, self.step_image, self.step_google, self.step_arm):
            row.button.setEnabled(not busy)

        # 1. sistem
        problems = []
        if not wd.is_installed():
            problems.append("Waydroid nije instaliran:  yay -S waydroid")
        if not wd.kernel_has_binder():
            problems.append(f"Kernel ({wd.kernel_name()}) nema binder. "
                            "Instaliraj linux-zen i restartuj sa njim.")
        if wd.needs_nested() and not wd.compositor_available():
            problems.append("X11 sesija bez Weston-a:  sudo pacman -S weston")
        system_ok = not problems
        self._service_enabled = system_ok and wd.container_service_enabled()
        if not system_ok:
            self.step_system.set(BAD, "\n".join(problems), "Proveri ponovo")
        elif status.initialized and not self._service_enabled:
            self.step_system.set(
                TODO,
                "Sistem je spreman. Uključi Waydroid servis da se lozinka ne "
                "traži pri svakom paljenju Androida (opciono, preporučeno).",
                "Uključi servis", primary=True)
        else:
            extra = " (Weston za X11 ✓)" if wd.needs_nested() else ""
            svc = " Servis se pali sa sistemom." if self._service_enabled else ""
            self.step_system.set(OK, f"Waydroid i kernel su spremni{extra}.{svc}")

        # 2. Android slika
        gapps = wd.has_gapps()
        if not status.initialized:
            self.step_image.set(
                TODO if system_ok else BAD,
                "Preuzima Android sliku sa Google servisima (~1 GB, potrajaće).",
                "Instaliraj Android" if system_ok and self._job != "init" else None,
                primary=True)
        elif gapps is False:
            self.step_image.set(
                BAD, "Android je instaliran bez Play Store-a. "
                "Ponovna instalacija sa GAPPS-om briše postojeće aplikacije.",
                "Reinstaliraj sa GAPPS")
        else:
            self.step_image.set(OK, "Android sa Play Store-om je instaliran.")

        image_ok = status.initialized and gapps is not False

        # 3. Google sertifikacija
        if not image_ok:
            self.step_google.set(TODO, "Prvo završi korak 2.")
        elif self._android_id:
            self.step_google.set(
                OK,
                f"Android ID: {self._android_id}\n"
                "Nalepi ga na Google stranici, sačekaj 10–20 min, pa ugasi i "
                "upali Android. Ako Play Store i dalje buni, u Android "
                "podešavanjima obriši podatke Play Store-a.",
                "Otvori Google stranicu", primary=True)
        elif not status.session:
            self.step_google.set(
                TODO, "Upali Android u glavnom prozoru i jednom otvori Play Store, "
                "pa se vrati ovde.")
        else:
            self.step_google.set(
                TODO, "Bez ovoga Play Store javlja da uređaj nije sertifikovan "
                "i mnoge aplikacije se ne mogu instalirati.",
                "Pročitaj Android ID", primary=True)

        # 4. ARM prevođenje
        lib = wd.recommended_translation()
        cpu = {"amd": "AMD", "intel": "Intel"}.get(wd.cpu_vendor(), "tvoj")
        if not status.initialized:
            self.step_arm.set(TODO, "Prvo završi korak 2.")
        elif wd.arm_translation_installed():
            self.step_arm.set(OK, "ARM prevođenje je instalirano — igre za telefone rade.")
        elif wd.extras_command() is None:
            self.step_arm.set(
                TODO,
                "Treba ti waydroid_script u ~/waydroid_script. Kopiraj komande, "
                "nalepi ih u terminal, a ovaj korak će se sam osvežiti.",
                "Kopiraj komande")
        else:
            self.step_arm.set(
                TODO,
                f"Većina igara je pravljena za ARM telefone. Za {cpu} procesor "
                f"preporučeno je {lib}. Android se posle mora restartovati.",
                f"Instaliraj {lib}", primary=True)

    # ------------------------------------------------------------ akcije

    def _system_action(self):
        status = self._get_status()
        if status.initialized and not self._service_enabled and wd.is_installed():
            self._run("service", wd.ENABLE_CONTAINER_CMD, "Uključujem Waydroid servis…")
        else:
            self.refresh()

    def _run_init(self):
        status = self._get_status()
        if status.initialized:
            answer = QMessageBox.question(
                self, "DroidDeck",
                "Ponovna instalacija briše sve Android aplikacije i podatke.\n\n"
                "Nastaviti?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            if status.session:
                QMessageBox.information(self, "DroidDeck",
                                        "Prvo ugasi Android u glavnom prozoru.")
                return
        self._run("init", wd.INIT_CMD, "Preuzimam i instaliram Android…",
                  shown=wd.INIT_CMD_SHOWN)

    def _google_action(self):
        if self._android_id:
            QDesktopServices.openUrl(QUrl(wd.CERT_URL))
        else:
            self._run("android_id", wd.ANDROID_ID_CMD, "Čitam Android ID…")

    def _arm_action(self):
        lib = wd.recommended_translation()
        cmd = wd.arm_install_cmd(lib)
        if cmd is None:
            QGuiApplication.clipboard().setText(wd.SCRIPT_SETUP_COMMANDS)
            self.log.show()
            self.log.setPlainText("Kopirano u clipboard:\n\n" + wd.SCRIPT_SETUP_COMMANDS)
            return

        # skripta se pokreće kao root: proveri je i jasno pokaži šta se pokreće
        state = wd.script_repo_state()
        if state == "modified":
            QMessageBox.warning(
                self, "DroidDeck",
                f"Fajlovi u {wd.SCRIPT_DIR} su menjani ili dodati posle kloniranja.\n\n"
                "Pošto se ova skripta pokreće kao root, DroidDeck je neće pokrenuti. "
                "Proveri izmene sa  git -C ~/waydroid_script status  ili obriši folder "
                "i kloniraj ga ponovo.",
            )
            return
        note = {
            "aur": "Instalirana kao sistemski paket.",
            "clean": "Kod repoa nije menjan posle kloniranja "
                     "(paketi u venv/ se ne proveravaju).",
            "unknown": "⚠ Nije moguće proveriti da li je skripta menjana (nije git repo).",
        }[state]
        answer = QMessageBox.question(
            self, "DroidDeck — pokretanje kao root",
            "Sledeća komanda će se pokrenuti sa administratorskim pravima:\n\n"
            f"{wd.describe_cmd(cmd)}\n\n{note}\n\nNastaviti?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run("arm", cmd, f"Instaliram {lib}…")

    # ------------------------------------------------------------ procesi

    def _run(self, job: str, cmd: list[str], label: str, shown: str | None = None):
        self._job, self._job_output = job, ""
        self.job_label.setText(label)
        self.log.clear()
        self.log.show()
        if shown is None:
            shown = " ".join(cmd[:4]) + (" …" if len(cmd) > 4 else "")
        self.log.appendPlainText("$ " + shown)
        if job == "init":
            self._tracker = wd.InitProgressTracker()
            self._show_init_progress()
        self.refresh()
        self._proc.start(cmd[0], cmd[1:])

    def _show_init_progress(self):
        t = self._tracker
        if t is None:
            return
        self.step_image.set_progress(t.state.fraction, t.summary())
        frac = t.state.fraction
        pct = f" {frac * 100:.0f}%" if frac is not None else ""
        self.setWindowTitle(f"DroidDeck — podešavanje{pct}")

    def _on_error(self, err):
        # FailedToStart ne šalje finished — bez ovoga bi korak ostao "u toku" zauvek
        if err == QProcess.ProcessError.FailedToStart and self._job is not None:
            self._job_output += f"\nKomanda nije pokrenuta: {self._proc.program()}"
            self._on_finished(-1, None)

    def _on_output(self):
        chunk = bytes(self._proc.readAll()).decode(errors="replace")
        self._job_output += chunk
        if self._tracker is not None:
            # napredak ide u traku; u log samo "prave" poruke
            for line in self._tracker.feed(chunk):
                self.log.appendPlainText(line)
            self._show_init_progress()
            return
        for line in chunk.replace("\r", "\n").splitlines():
            if line.strip():
                self.log.appendPlainText(line)

    def _on_finished(self, exit_code, _status):
        if self._job is None:
            return
        job, output = self._job, self._job_output
        self._job = None
        self.job_label.setText("")
        if self._tracker is not None:
            for line in self._tracker.flush():
                self.log.appendPlainText(line)
            self._tracker = None
            self.step_image.hide_progress()
            self.setWindowTitle("DroidDeck — podešavanje")

        if exit_code == -1:
            self.log.appendPlainText("\nKomanda nije mogla da se pokrene. "
                                     "Proveri da li su waydroid i polkit (pkexec) instalirani.")
        elif job == "android_id":
            android_id = wd.parse_android_id(output)
            if android_id:
                self._android_id = android_id
                QSettings().setValue(K_ANDROID_ID, android_id)
                QSettings().setValue(K_ANDROID_ID_STAMP, wd.cfg_stamp())
                QGuiApplication.clipboard().setText(android_id)
                self.log.appendPlainText(f"\nAndroid ID: {android_id} (kopiran u clipboard)")
                QDesktopServices.openUrl(QUrl(wd.CERT_URL))
            else:
                self.log.appendPlainText(
                    "\nID nije pronađen. Otvori Play Store u Androidu, sačekaj "
                    "minut da se Google servisi pokrenu i pokušaj ponovo.")
        elif exit_code == 0:
            if job == "init":
                # nova Android slika = novi Android ID
                self._android_id = None
                QSettings().remove(K_ANDROID_ID)
                QSettings().remove(K_ANDROID_ID_STAMP)
            msg = {"init": "Android je instaliran. Sada ga upali u glavnom prozoru.",
                   "arm": "ARM prevođenje je instalirano. Ugasi i ponovo upali Android.",
                   "service": "Waydroid servis je uključen i pali se sa sistemom."}
            self.log.appendPlainText("\n" + msg.get(job, "Gotovo."))
        else:
            self.log.appendPlainText(f"\nNije uspelo (kod {exit_code}). "
                                     "Ako si otkazao lozinku, samo pokušaj ponovo.")
        self.refresh()

    # ------------------------------------------------------------ kraj

    def _finish(self):
        QSettings().setValue(SETTINGS_KEY, True)
        self.finished_setup.emit()
        self.close()

    def _job_running_warning(self) -> bool:
        """True (i poruka) ako se zatvaranje mora odložiti."""
        if self._job is None:
            return False
        QMessageBox.information(self, "DroidDeck", "Sačekaj da se trenutni korak završi.")
        return True

    def closeEvent(self, event):               # X na prozoru
        if self._job_running_warning():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):                          # taster Esc
        if not self._job_running_warning():
            super().reject()

    @staticmethod
    def is_done() -> bool:
        return QSettings().value(SETTINGS_KEY, False, type=bool)
