# DroidDeck

Lagani PyQt6 pokretač Android aplikacija i igara na Linuxu, preko Waydroid-a.

## Priprema (jednom)

```bash
sudo pacman -S linux-zen linux-zen-headers weston python-pyqt6
yay -S waydroid
```

Restartuj računar sa `linux-zen` kernelom. Ostatak (Waydroid servis, Android
slika, Google sertifikacija, ARM podrška za igre) radi čarobnjak u aplikaciji.

## Instalacija

**Kao paket (preporučeno)** — iz foldera u kom je `PKGBUILD`:

```bash
makepkg -si          # pokretanje: meni aplikacija ili komanda  droiddeck
sudo pacman -R droiddeck
```

**Bez paketa** — iz ovog foldera:

```bash
python main.py              # direktno
sh install.sh               # dodaje DroidDeck u meni aplikacija
sh install.sh --ukloni      # uklanja ga iz menija
```

Koristi samo jedan od ova dva načina.

## Korišćenje

- **Pokreni Android** / **Ugasi Android** — gornje dugme
- **Dupli klik** na ikonicu pokreće aplikaciju (Android se sam upali ako treba)
- **Desni klik** na ikonicu — Pokreni, Zaustavi aplikaciju, Kopiraj naziv
  paketa, Deinstaliraj
- **◀ ● ▢** u donjoj traci — Nazad, Početni ekran, Otvorene aplikacije
- **Play Store**, **Instaliraj APK** ili prevuci `.apk` u prozor
- **☰** — svaka aplikacija u svom prozoru, veličina Android ekrana,
  tamna/svetla tema, zatvaranje u tray
- **⚙ Podešavanje** — čarobnjak (sistem i servis, Android sa Play Store-om,
  Google ID, ARM)
- **Tray** — levi klik prikaži/sakrij, desni klik: poslednje igre, paljenje, izlaz

### Navigacija u Android-u

- **Nazad**: desni klik mišem, Esc, ili ◀ u DroidDeck-u
- **Prebacivanje**: ▢ (ili Alt+Tab kad je fokus u Android prozoru)
- **Gašenje aplikacije**: na ekranu ▢ prevuci karticu nagore, ili u DroidDeck-u
  desni klik → Zaustavi aplikaciju. U režimu „svaka aplikacija u svom prozoru"
  X na prozoru aplikaciju samo sakrije — ona radi dalje u pozadini.

Ako se aplikacija ne otvara: desni klik → Zaustavi aplikaciju, pa ponovo.

### Lozinka

Paket instalira polkit pravilo (`/usr/share/polkit-1/rules.d/50-droiddeck.rules`)
koje bez lozinke propušta samo: paljenje Waydroid kontejnera, Zaustavi aplikaciju
i ◀ ● ▢. Sve ostalo (instalacija Androida, ARM skripta…) i dalje traži lozinku.
Bez paketa (install.sh) te komande traže lozinku pri svakom kliku; pravilo
možeš ručno da instaliraš:

```bash
sudo install -Dm644 polkit/50-droiddeck.rules -t /etc/polkit-1/rules.d
```

## Struktura

```
main.py               ulaz, tema, tray režim, hvatanje grešaka
waydroid_api.py       SVE waydroid komande na jednom mestu
ui/main_window.py     glavni prozor, start/stop, pending akcije
ui/app_grid.py        mreža ikonica, pretraga, desni klik meni
ui/setup_wizard.py    čarobnjak u 4 koraka
ui/tray.py            tray ikonica i meni
ui/icon.py            ikonica nacrtana u kodu
ui/theme.py           tamna i svetla tema
install.sh            unos u meni aplikacija (bez paketa)
polkit/               pravilo: bez lozinke za navigaciju i zaustavljanje
```

Podešavanja se čuvaju u `~/.config/DroidDeck/DroidDeck.conf`.
Ako nešto pukne, pokreni `droiddeck` iz terminala — detalji greške se ispisuju tamo.
