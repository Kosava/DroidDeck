# Izmene

## 1.0.3 — 22.10.2026.

### Aplikacije koje se ne podignu / Android koji ne reaguje
- DroidDeck više ne proglašava Android spremnim posle fiksnih 6 sekundi:
  čeka `sys.boot_completed = 1` (najviše 90 s). Aplikacije kliknute dok se
  Android pali pokreću se tek kad je sistem stvarno spreman.
- Greška pri pokretanju aplikacije se prikazuje (šta je Waydroid odgovorio),
  umesto da se tiho izgubi.
- Novo na desni klik: **⏹ Zaustavi aplikaciju** — potpuno gasi aplikaciju,
  i onu koja je posle zatvaranja prozora ostala da radi u pozadini.
- Gašenje čeka da se Weston prozor stvarno zatvori (posle 3 s ga ubija),
  pa brzo gašenje pa paljenje više ne kači Android na Weston koji umire.
- Pet duplih klikova na istu igru dok se Android pali = jedno pokretanje.

### Novo
- Navigacija u donjoj traci: **◀ Nazad · ● Početna · ▢ Otvorene aplikacije**.
- ☰ → Veličina Android ekrana (1280×720, 1600×900, 1920×1080) na X11.
- Paket donosi polkit pravilo (`/usr/share/polkit-1/rules.d/50-droiddeck.rules`):
  bez lozinke rade SAMO paljenje Waydroid kontejnera, Zaustavi aplikaciju i
  tri navigaciona tastera. Sve ostalo i dalje traži lozinku.
- Drugo pokretanje DroidDeck-a (iz menija) prikazuje prozor koji već radi.

### Sitnice
- Svesno gašenje Androida tokom instalacije više ne prijavljuje grešku.
- Posle ažuriranja već instalirane aplikacije nema lažnog upozorenja.
- Sačuvani Android ID se odbacuje kad je Android reinstaliran (i iz terminala).

## 1.0.2 — 21.10.2026.

- Korak 2 čarobnjaka (instalacija Androida) ima traku napretka: koja slika se
  preuzima (1/2 sistemska, 2/2 vendor), MB, brzina i procena preostalog
  vremena; posle toga "Proveravam" i "Raspakujem" faze. Procenat je i u
  naslovu prozora.
- Linije napretka više ne zatrpavaju log — u njemu su samo prave poruke.
- `waydroid init` se pokreće sa `PYTHONUNBUFFERED=1` da napredak stiže
  odmah, a ne u paketima (polkit zato prikazuje `/usr/bin/env` kao program).

## 1.0.1 — 21.10.2026.

### Kritično
- Program koji ne postoji (npr. Weston na X11) više ne ruši DroidDeck:
  pokretanje spoljnih komandi nikad ne baca izuzetak, a `sys.excepthook`
  hvata sve ostalo (poruka + ispis u terminal umesto gašenja).
- Status se čita iz stdout-a i stderr-a; "nije inicijalizovan" se prepoznaje
  i preko `/var/lib/waydroid/waydroid.cfg`. Na sveže instaliranom sistemu
  čarobnjak se sada sam otvara (ranije je UI stajao na "Proveravam…").
- Bez instaliranog Waydroid-a UI to jasno kaže i otvara čarobnjak.
- Instalacija APK-a ima limit od 3 minuta; Android se može ugasiti i tokom
  instalacije; spoljno gašenje Androida prekida instalaciju i briše red.

### Pouzdanost
- Paljenje proverava systemd servis (`systemctl is-active`) umesto LXC
  stanja, pa se lozinka ne traži bez potrebe. Čarobnjak (korak 1) nudi
  `systemctl enable --now waydroid-container` — posle toga nikad lozinka.
- Istek paljenja (90 s): Android se prvo gasi, pa tek onda prikazuje poruka;
  zakasnela sesija se više ne prijavljuje kao "Android radi".
- Zaostali pkexec dijalog od prošlog pokušaja se zatvara pre novog paljenja.
- Poruke o greškama se prikazuju posle promene stanja (ne blokiraju poll).
- Izlaz tokom instalacije traži potvrdu.
- Posle instalacije DroidDeck proverava da li se pojavila nova ikonica.

### Bezbednost
- Provera waydroid_script repoa obuhvata i nove (netrackovane) fajlove;
  venv i Python keš se preskaču. Skripta se pokreće sa `python -B`, pa root
  ne piše `__pycache__` u tvoj repo.

### UX
- Tray meni ispravno prikazuje "Prikaži/Sakrij" od starta i prati minimizovanje.
- Vraćanje iz tray-a čuva maksimizovan prozor.
- Status se proverava na 12 s dok je prozor u tray-u (3 s inače).
- Dijalog za APK otvara pravi folder za preuzimanja (XDG).
- Android ID se pamti između pokretanja (briše se posle reinstalacije).
- Mreža aplikacija čuva skrol i izabranu ikonicu pri osvežavanju.
- Poruka o prevlačenju APK-a se briše posle puštanja.

### Pakovanje
- PKGBUILD: uklonjen pogrešan `url` i suvišan `makedepends`;
  `StartupWMClass=DroidDeck`.
- `install.sh`: `%` u putanji, escape za `Path=`, nova opcija `--ukloni`.

## Audit — 21.10.2026. (1.0)

### Kritično (aplikacija je mogla da se zaglavi)
- Paljenje i gašenje Androida imaju vremenski limit (90 s / 30 s); posle toga
  DroidDeck prijavi grešku i otključa UI.
- Čarobnjak, instalacija APK-a, pokretanje kontejnera i kratke komande sada
  obrađuju i slučaj kad komanda uopšte ne može da krene (`errorOccurred`).
- Instalacioni red nastavlja posle APK-a čija instalacija nije mogla da krene.

### Pouzdanost
- Životni ciklus je eksplicitan: OFF → STARTING → BOOTING → ON → STOPPING.
- Zaostali Weston socket posle pada se briše; "spreman" znači da Weston zaista radi.
- Zatvaranje Weston prozora koji je otvorio DroidDeck gasi Android
  (Android upaljen van DroidDeck-a se ne dira).
- Klik tokom podizanja sistema čeka da Android bude spreman.
- Akcije tokom gašenja se odbijaju umesto da se izvrše pri sledećem paljenju.
- Multi-window se usklađuje sa podešavanjem pri svakom paljenju.
- `waydroid status` koji visi se ubija posle 10 s; prazan izlaz ne menja stanje.
- Weston se traži preko /proc (tačan program + argument) umesto `pkill -f`,
  koji je mogao da ubije nepovezane procese.

### Bezbednost
- Pre pokretanja waydroid_script kao root prikazuje se tačna komanda uz potvrdu.
- Ako su fajlovi kloniranog repoa menjani, DroidDeck odbija da ga pokrene.
- Putanje se citiraju (`shlex.quote`); `install.sh` podnosi razmake i navodnike.
- Android ID se čita samo iz linije koja sadrži isključivo cifre.

### Sitnice
- Čarobnjak proverava stanje samo dok je otvoren.
- Keš ikonica prati vreme izmene fajla.
- Samo jedna kopija DroidDeck-a može da radi.
