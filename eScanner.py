#!/usr/bin/env python3
"""eScanner — fotografér en online-bog side for side og saml resultatet i én PDF.

Tænkt til bøger der kun kan læses i en browser/viewer, hvor man bladrer med
piletasterne. Programmet vælger et vindue (også på en anden skærm end
terminalen), tager et skærmbillede af det, sender en piletast, venter til siden
rent faktisk har skiftet, og gentager. Til sidst samles alle sider i en PDF.

Eksempler:
    python eScanner.py                        # vælg vindue fra en liste
    python eScanner.py --window 35221 -n 300
    python eScanner.py --display 2            # hele skærm 2
    python eScanner.py --region 420,180,900,1200
    python eScanner.py --rebuild-only out/2026-09-23_1200
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:
    import Quartz
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Manglende afhængighed: {exc.name}. Kør: pip install -r requirements.txt")

KEY_CODES = {
    "right": 124, "left": 123, "down": 125, "up": 126,
    "space": 49, "pagedown": 121, "pageup": 116,
}

# Tast der bladrer den anden vej — så en prøvebladring kan gøres om.
MODSAT = {"right": "left", "left": "right", "down": "up", "up": "down",
          "pagedown": "pageup", "pageup": "pagedown"}

# Vinduesejere der aldrig er en bog.
SKIP_OWNERS = {"WindowManager", "Dock", "Kontrolcenter", "Control Center",
               "Spotlight", "Window Server", "UserClient", "Notification Center"}


# --------------------------------------------------------------------------- #
# Tilladelser
# --------------------------------------------------------------------------- #
def require_screen_recording() -> None:
    """Uden denne tilladelse indeholder skærmbilleder kun skrivebordsbaggrunden."""
    if Quartz.CGPreflightScreenCaptureAccess():
        return
    Quartz.CGRequestScreenCaptureAccess()  # udløser macOS-dialogen
    sys.exit(
        "Skærmoptagelse er ikke tilladt for din terminal.\n"
        "Uden den bliver alle sider til et billede af skrivebordsbaggrunden.\n\n"
        "  Systemindstillinger → Privatliv & sikkerhed → Skærmoptagelse\n"
        "  → slå din terminal til, og start den forfra.\n\n"
        "Mens du er der: slå den også til under Tilgængelighed, ellers når\n"
        "piletasterne ikke frem til browseren."
    )


# --------------------------------------------------------------------------- #
# Skærme og vinduer
# --------------------------------------------------------------------------- #
def displays() -> list[tuple[int, tuple[int, int, int, int]]]:
    """Aktive skærme som (nummer, (x, y, bredde, højde)) i globale koordinater."""
    _, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    out = []
    for n, did in enumerate(ids[:count], start=1):
        r = Quartz.CGDisplayBounds(did)
        out.append((n, (int(r.origin.x), int(r.origin.y),
                        int(r.size.width), int(r.size.height))))
    return out


def display_of(rect: tuple[int, int, int, int]) -> int:
    """Hvilken skærm ligger midten af `rect` på."""
    cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
    for n, (x, y, w, h) in displays():
        if x <= cx < x + w and y <= cy < y + h:
            return n
    return 1


def list_windows(relaxed: bool = False) -> list[dict]:
    """Appvinduer på tværs af skærme *og* skærmpladser (Spaces), største først.

    Et vindue i fuldskærm ligger på sin egen skærmplads og tæller derfor ikke
    som "on screen", når terminalen står et andet sted. Derfor kigger vi på alle
    vinduer og sorterer hjælpepaneler fra bagefter — ellers ville listen være
    ubrugelig lang (ti gange så mange vinduer, næsten alle usynlige).
    """
    opts = Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements
    # Skjulte vinduer skal være pænt store for at komme med: et bogvindue er
    # stort, mens systemets usynlige hjælpevinduer typisk er 500×500 eller mindre.
    min_w, min_h = (120, 120) if relaxed else (600, 400)

    found, seen = [], set()
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if w.get("kCGWindowLayer") != 0:
            continue
        owner = w.get("kCGWindowOwnerName") or "?"
        if owner in SKIP_OWNERS:
            continue
        if float(w.get("kCGWindowAlpha", 1.0)) < 0.5:
            continue

        b = w.get("kCGWindowBounds")
        rect = (int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]))
        onscreen = bool(w.get("kCGWindowIsOnscreen"))
        title = w.get("kCGWindowName") or ""

        if onscreen:
            if rect[2] < 300 or rect[3] < 300:
                continue
        elif rect[2] < min_w or rect[3] < min_h:
            # Bemærk: vi kan ikke kræve en titel her. Titler er tomme, indtil
            # skærmoptagelse er tilladt — og så ville bogens vindue falde ud.
            continue

        if (owner, rect) in seen:
            continue
        seen.add((owner, rect))

        found.append({
            "id": int(w.get("kCGWindowNumber")),
            "owner": owner,
            "title": title,
            "rect": rect,
            "display": display_of(rect),
            "onscreen": onscreen,
            "fullscreen": is_fullscreen(rect),
        })

    found.sort(key=lambda d: d["rect"][2] * d["rect"][3], reverse=True)
    return found


def is_fullscreen(rect: tuple[int, int, int, int]) -> bool:
    """Fylder vinduet præcis en hel skærm?"""
    return any(rect == r for _, r in displays())


def window_by_id(wid: int) -> dict | None:
    return next((w for w in list_windows(relaxed=True) if w["id"] == wid), None)


def describe(w: dict, multi_display: bool) -> list[str]:
    x, y, bw, bh = w["rect"]
    navn = w["owner"] + (f" — {w['title'][:45]}" if w["title"] else "")
    detaljer = [f"{bw}×{bh} ved ({x}, {y})"]
    if multi_display:
        detaljer.append(f"skærm {w['display']}")
    if w["fullscreen"]:
        detaljer.append("fuldskærm")
    if not w["onscreen"]:
        detaljer.append("på en anden skærmplads")
    return [navn, "  ".join(detaljer)]


def choose_window(relaxed: bool = False) -> dict:
    """Vælg vindue i terminalen — ingen mus, virker på tværs af skærme og Spaces."""
    wins = list_windows(relaxed=relaxed)
    if not wins:
        sys.exit("Fandt ingen vinduer. Er bogen åben? Prøv --all-windows.")

    multi = len(displays()) > 1
    print("\nVinduer:\n")
    for i, w in enumerate(wins, start=1):
        navn, detaljer = describe(w, multi)
        print(f"  {i:>2}. {navn}")
        print(f"      {detaljer}")
    if not relaxed:
        print("\n  (Mangler bogen? Kør igen med --all-windows.)")

    while True:
        svar = input(f"\nHvilket vindue er bogen i? [1-{len(wins)}]: ").strip()
        if svar.isdigit() and 1 <= int(svar) <= len(wins):
            return wins[int(svar) - 1]
        print("  Skriv et af numrene ovenfor.")


# --------------------------------------------------------------------------- #
# Skærmbilleder
# --------------------------------------------------------------------------- #
def capture(rect: tuple[int, int, int, int]) -> Image.Image:
    """Skærmbillede af et globalt rektangel i fuld opløsning (også Retina).

    Vi kalder `screencapture` direkte: Pillows ImageGrab skalerer resultatet ned
    til punkter og halverer dermed opløsningen på en Retina-skærm, og den kan
    kun se hovedskærmen.
    """
    fd, path = tempfile.mkstemp(".png")
    os.close(fd)
    try:
        subprocess.run(
            ["screencapture", "-x", "-R", ",".join(str(v) for v in rect), path],
            check=False, capture_output=True,
        )
        im = Image.open(path)
        im.load()
        return im.convert("RGB")
    finally:
        os.unlink(path)


def difference(a: Image.Image, b: Image.Image) -> float:
    """Gennemsnitlig forskel (0-255) mellem to billeder, målt på en lille kopi."""
    thumb = (120, 160)
    ga, gb = a.convert("L").resize(thumb), b.convert("L").resize(thumb)
    return ImageStat.Stat(ImageChops.difference(ga, gb)).mean[0]


def _longest_run(profile, minimum, bridge=0):
    """Længste sammenhængende stræk over `minimum`; huller på op til `bridge`
    værdier ignoreres. -> (start, slut)"""
    bedst, start, hul = (0, 0), None, 0
    for i, v in enumerate(profile):
        if v > minimum:
            if start is None:
                start = i
            hul = 0
        elif start is not None:
            hul += 1
            if hul > bridge:
                slut = i - hul + 1
                if slut - start > bedst[1] - bedst[0]:
                    bedst = (start, slut)
                start, hul = None, 0
    if start is not None and len(profile) - start > bedst[1] - bedst[0]:
        bedst = (start, len(profile))
    return bedst


def _smooth(profile, radius: int):
    """Glidende gennemsnit over profilen.

    Mellemrummene mellem tekstlinjer er nul-værdier, der ellers ville dele
    siden op i lige så mange stræk, som der er linjer. Udjævningen fylder de
    små huller, mens overgangen mellem side og baggrund er for stor en
    struktur til at blive udvisket.
    """
    n = len(profile)
    if radius < 1 or n == 0:
        return list(profile)
    kumuleret = [0.0]
    for v in profile:
        kumuleret.append(kumuleret[-1] + v)
    ud = []
    for i in range(n):
        a, b = max(0, i - radius), min(n, i + radius + 1)
        ud.append((kumuleret[b] - kumuleret[a]) / (b - a))   # deler med det der faktisk er
    return ud


def _extend(profile, start, end, low):
    """Forlæng et fundet stræk ud, så længe værdierne holder sig over `low`.

    Kernen findes med en høj grænse, så støj ikke tæller med. Men sidens
    yderste tekstlinje og papirets kant giver svagere udslag end midten —
    uden den her ville de blive klippet af."""
    while start > 0 and profile[start - 1] > low:
        start -= 1
    while end < len(profile) and profile[end] > low:
        end += 1
    return start, end


def _mean(im, per_column):
    size = (im.width, 1) if per_column else (1, im.height)
    return list(im.resize(size, Image.BOX).getdata())


def _spread(im, per_column):
    """Standardafvigelse ned ad hver søjle (per_column) eller hen ad hver række."""
    m = _mean(im, per_column)
    kv = _mean(im.point(lambda v: v * v // 255), per_column)
    return [max(0.0, k * 255 - v * v) ** 0.5 for k, v in zip(kv, m)]


def grow_to_paper(im: Image.Image, box, tol: int = 14, maks: float = 0.15):
    """Udvid kassen ud til papirkanten.

    Det der ændrer sig mellem to sider, er teksten — ikke papirets hvide
    margen. Derfor vokser vi ud, så længe nabostriben har samme farve som
    siden selv, med et loft så vi ikke render ud i viewerens baggrund.
    """
    g = im.convert("L")
    l, t, r, b = box
    papir = ImageStat.Stat(g.crop((l, t, r, min(t + 12, b)))).median[0]

    def samme(kasse) -> bool:
        st = ImageStat.Stat(g.crop(kasse))
        return abs(st.median[0] - papir) <= tol and st.stddev[0] <= tol * 2

    trin = 4
    for _ in range(int((b - t) * maks) // trin):
        if t - trin >= 0 and samme((l, t - trin, r, t)):
            t -= trin
        else:
            break
    for _ in range(int((b - t) * maks) // trin):
        if b + trin <= im.height and samme((l, b, r, b + trin)):
            b += trin
        else:
            break
    for _ in range(int((r - l) * maks) // trin):
        if l - trin >= 0 and samme((l - trin, t, l, b)):
            l -= trin
        else:
            break
    for _ in range(int((r - l) * maks) // trin):
        if r + trin <= im.width and samme((r, t, r + trin, b)):
            r += trin
        else:
            break
    return (l, t, r, b)


def detect_page_from_frames(frames: list, margin: float = 0.02):
    """Find siden ud fra hvad der ændrer sig, når man bladrer. -> kasse | None

    Det stærkeste signal der findes til det her: bogsiden skiftes ud, mens
    indholdsfortegnelse, værktøjslinjer og pile bliver stående. En statisk
    analyse af ét enkelt billede kan ikke skelne et sidepanel med tekst fra
    selve siden — det kan den her.
    """
    graa = [f.convert("L") for f in frames]
    akk = None
    for a, b in zip(graa, graa[1:]):
        d = ImageChops.difference(a, b)
        akk = d if akk is None else ImageChops.lighter(akk, d)
    if akk is None:
        return None

    w, h = akk.size
    skala = max(1, max(w, h) // 600)
    lille = akk.resize((max(1, w // skala), max(1, h // skala)), Image.BOX)
    lw, lh = lille.size

    # Udjævn hen over linjemellemrum, før strækkene findes.
    rx, ry = max(1, lw // 60), max(1, lh // 60)
    soejler = _smooth(_mean(lille, True), rx)
    raekker = _smooth(_mean(lille, False), ry)
    if max(soejler) < 8:
        return None      # intet ændrede sig — samme side, eller for lille forskel

    def graense(profil):
        """Grænse ud fra det typiske niveau i den øverste fjerdedel.

        Ikke en andel af maksimum: et enkelt kraftigt udsving — fx et punkt i
        indholdsfortegnelsen der skifter markering — ville ellers hæve grænsen
        så meget, at selve siden faldt under den.
        """
        top = sorted(profil)[-max(4, len(profil) // 4):]
        return max(6.0, top[len(top) // 2] * 0.3)

    # Længste sammenhængende stræk, ikke første/sidste udslag: et enkelt blink
    # i et sidepanel ville ellers trække kanten helt ud til vinduets rand.
    x0, x1 = _longest_run(soejler, graense(soejler), bridge=max(2, lw // 50))
    if x1 - x0 < 0.05 * lw:
        return None

    # Rækkerne måles inde i sidens søjler, og søjlerne bagefter inde i sidens
    # rækker. Ved en smal side ville baggrunden udenom ellers udvande profilen
    # så meget, at kun den kraftigst ændrede stribe blev fundet.
    for _ in range(2):
        raekker = _smooth(_mean(lille.crop((x0, 0, x1, lh)), False), ry)
        g = graense(raekker)
        y0, y1 = _longest_run(raekker, g, bridge=max(2, lh // 50))
        if y1 - y0 < 0.05 * lh:
            return None
        y0, y1 = _extend(raekker, y0, y1, g * 0.25)

        soejler = _smooth(_mean(lille.crop((0, y0, lw, y1)), True), rx)
        g = graense(soejler)
        x0, x1 = _longest_run(soejler, g, bridge=max(2, lw // 50))
        if x1 - x0 < 0.05 * lw:
            return None
        x0, x1 = _extend(soejler, x0, x1, g * 0.25)

    mx, my = int((x1 - x0) * margin), int((y1 - y0) * margin)
    box = (max(0, (x0 - mx) * skala), max(0, (y0 - my) * skala),
           min(w, (x1 + mx) * skala), min(h, (y1 + my) * skala))
    return grow_to_paper(frames[0], box)


def _median(values):
    v = sorted(values)
    return v[len(v) // 2] if v else 0


def detect_page(im, min_area=0.10):
    """Find bogsiden i et vinduesbillede -> (venstre, top, højre, bund).

    Tre trin, fordi hver retning har sin egen faldgrube:

    1. Rækker, groft: en række hen over siden rummer både viewerens baggrund og
       selve siden og har derfor stor spredning. Browserens værktøjslinje og
       statuslinje er ensfarvede ud til kanten — spredning nul. Det finder
       siden uden at kende nogen farver på forhånd.
    2. Søjler, målt *inde i* de rækker: her tæller niveau, ikke variation. Sidens
       hvide margener har ingen tekst og ville falde ud af et variationsmål —
       men de adskiller sig tydeligt fra baggrundens farve.
    3. Rækker, fint: nu hvor sidens søjler er kendt, sammenlignes hver række
       inden for siden med baggrunden ved siden af. Det får margenerne foroven
       og forneden med, som trin 1 skar væk, og holder stadig værktøjslinjen
       ude — den ser ens ud både inde og ude.
    """
    w, h = im.size
    skala = max(1, max(w, h) // 600)
    lille = im.convert("L").resize((max(1, w // skala), max(1, h // skala)), Image.BOX)
    lw, lh = lille.size

    def ud(x0, y0, x1, y1):
        boks = (x0 * skala, y0 * skala, min(x1 * skala, w), min(y1 * skala, h))
        if (boks[2] - boks[0]) * (boks[3] - boks[1]) < min_area * w * h:
            return (0, 0, w, h)
        return boks

    # --- 1. Rækker, groft -------------------------------------------------- #
    raekker = _spread(lille, per_column=False)
    if max(raekker) < 6:
        return (0, 0, w, h)
    y0, y1 = _longest_run(raekker, max(4.0, max(raekker) * 0.2), bridge=max(2, lh // 50))
    if y1 - y0 < 0.1 * lh:
        return (0, 0, w, h)

    # --- 2. Søjler inde i sidens rækker ------------------------------------ #
    baand = lille.crop((0, y0, lw, y1))
    middel = _mean(baand, per_column=True)
    spredt = _spread(baand, per_column=True)
    kant = max(1, lw // 20)
    bg = _median(middel[:kant] + middel[-kant:])       # viewerens baggrund
    graense_s = max(4.0, max(spredt) * 0.2)
    sidesoejle = [max(abs(m - bg), s * 2) for m, s in zip(middel, spredt)]
    x0, x1 = _longest_run(sidesoejle, max(10.0, graense_s), bridge=max(2, lw // 50))
    if x1 - x0 < 0.05 * lw:
        x0, x1 = 0, lw

    # --- 3. Rækker, fint --------------------------------------------------- #
    inde = _mean(lille.crop((x0, 0, x1, lh)), per_column=False)
    v = _mean(lille.crop((0, 0, x0, lh)), per_column=False) if x0 > 2 else None
    hj = _mean(lille.crop((x1, 0, lw, lh)), per_column=False) if lw - x1 > 2 else None
    if v and hj:
        bv, bh = x0, lw - x1
        ude = [(a * bv + b * bh) / (bv + bh) for a, b in zip(v, hj)]
    else:
        ude = v or hj
    if ude:
        forskel = [abs(a - b) for a, b in zip(inde, ude)]
        fy0, fy1 = _longest_run(forskel, 15, bridge=max(2, lh // 50))
        if fy1 - fy0 >= 0.1 * lh:
            y0, y1 = fy0, fy1

    return ud(x0, y0, x1, y1)


# --------------------------------------------------------------------------- #
# Tastetryk
# --------------------------------------------------------------------------- #
def activate(app: str | None, settle: float = 0.6) -> None:
    """Hent appens vindue frem, så tastetryk lander det rigtige sted."""
    if not app:
        return
    subprocess.run(["osascript", "-e", f'tell application "{app}" to activate'],
                   check=False, capture_output=True)
    time.sleep(settle)


def bring_forward(win: dict, timeout: float = 4.0) -> dict:
    """Hent vinduet frem — og vent på at macOS er færdig med at skifte skærmplads.

    Et fuldskærmsvindue ligger på sin egen skærmplads. Fotograferer vi før
    skiftet er kørt færdigt, rammer vi den forrige skærmplads i stedet.
    """
    activate(win["owner"], settle=0.3)
    deadline = time.time() + timeout
    while time.time() < deadline:
        frisk = window_by_id(win["id"])
        if frisk and frisk["onscreen"]:
            time.sleep(0.5)  # lad animationen falde helt til ro
            return frisk
        time.sleep(0.25)

    print(f"  ! {win['owner']} kom ikke frem af sig selv.")
    print("    Klik selv over på bogen, og kør igen med:")
    print(f"      --window {win['id']} --yes")
    return win


def send_key(app: str | None, key: str) -> None:
    """Send én tast. Uden `app` rammer den det vindue der i forvejen er forrest."""
    aktiver = f'tell application "{app}" to activate\n    delay 0.05\n    ' if app else ""
    script = f'''
    {aktiver}tell application "System Events" to key code {KEY_CODES[key]}
    '''
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        err = res.stderr.strip()
        if "assistive access" in err or "-1719" in err:
            sys.exit(
                "macOS blokerer tastetryk.\n"
                "Systemindstillinger → Privatliv & sikkerhed → Tilgængelighed\n"
                "→ slå din terminal til, og start den forfra."
            )
        print(f"  ! osascript: {err}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Afbryd
# --------------------------------------------------------------------------- #
def countdown(besked: str, sekunder: int) -> None:
    print(besked)
    for s in range(sekunder, 0, -1):
        print(f"  {s}...", end="\r", flush=True)
        time.sleep(1)
    print("        ", end="\r")


def mouse_in_corner(margin: int = 12) -> bool:
    """True hvis musen er trukket ud i et hjørne af en vilkårlig skærm."""
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    for _, (x, y, w, h) in displays():
        for cx in (x, x + w - 1):
            for cy in (y, y + h - 1):
                if abs(loc.x - cx) <= margin and abs(loc.y - cy) <= margin:
                    return True
    return False


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def wait_for_change(reference, rect, box, threshold, settle, max_wait) -> bool:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        ny = capture(rect)
        if difference(reference, ny.crop(box) if box else ny) >= threshold:
            time.sleep(settle)
            return True
    return False


def scan(args, rect, app: str | None, outdir: Path, box=None) -> list[Path]:
    pages_dir = outdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    previous: Image.Image | None = None
    repeats = 0

    kilde = f"«{app}»" if app else "det forreste vindue"
    print(f"\nScanner op til {args.pages} sider fra {kilde} → {pages_dir}")
    print("Stop undervejs: træk musen ud i et skærmhjørne (eller Ctrl+C i terminalen).\n")

    try:
        for _ in range(args.pages):
            if mouse_in_corner():
                print("\nMusen er i et hjørne — stopper.")
                break

            img = capture(rect)
            # Sammenlign kun selve siden: en blinkende markør eller en rullebjælke
            # uden for beskæringen skal ikke tælle som et sideskift.
            maalt = img.crop(box) if box else img

            if previous is not None and difference(previous, maalt) < args.threshold:
                repeats += 1
                print(f"  uændret ({repeats}/{args.stop_after})")
                if repeats >= args.stop_after:
                    print("\nSiden skifter ikke længere — stopper (slut på bogen?).")
                    break
            else:
                repeats = 0
                path = pages_dir / f"page_{len(saved) + 1:04d}.png"
                img.save(path)
                saved.append(path)
                print(f"  side {len(saved):>4}: gemt {path.name}")
                previous = maalt

            send_key(app, args.key)
            if not wait_for_change(maalt, rect, box, args.threshold,
                                   args.settle, args.max_wait):
                time.sleep(args.settle)
    except KeyboardInterrupt:
        print("\nAfbrudt — bygger PDF af det der nåede at blive scannet.")

    return saved


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def crop_from_pages(pages: list[Path]):
    """Beskæring udledt af de scannede sider selv."""
    udvalg = pages[:4]
    if len(udvalg) < 2:
        return None
    return detect_page_from_frames([Image.open(p) for p in udvalg])


def build_pdf(pages: list[Path], out: Path, dpi: int, grayscale: bool,
              box: tuple[int, int, int, int] | None) -> None:
    if not pages:
        print("Ingen sider at samle — ingen PDF oprettet.")
        return

    print(f"\nBygger PDF af {len(pages)} sider...")
    images = []
    for p in pages:
        im = Image.open(p)
        if box:
            im = im.crop(box)
        images.append(im.convert("L" if grayscale else "RGB"))

    images[0].save(out, "PDF", save_all=True, append_images=images[1:],
                   resolution=float(dpi))
    print(f"Færdig: {out}  ({len(images)} sider, "
          f"{out.stat().st_size / 1_048_576:.1f} MB)")


def save_box(outdir: Path, box) -> None:
    (outdir / "beskaering.txt").write_text(",".join(str(v) for v in box) + "\n")


def load_box(outdir: Path):
    fil = outdir / "beskaering.txt"
    if not fil.exists():
        return None
    try:
        return tuple(int(v) for v in fil.read_text().strip().split(","))
    except ValueError:
        return None


def crop_by_flipping(rect, app, key, threshold, settle, max_wait, flips: int = 2):
    """Bladr et par sider frem, se hvad der ændrer sig, og bladr tilbage igen.

    To skift frem for ét: to sider kan tilfældigvis ligne hinanden i en del af
    fladen — fx en halvtom side — og så bliver kassen for lille.
    """
    print(f"\n  Bladrer {flips} sider frem for at se, hvad der ændrer sig...")
    rammer = [capture(rect)]
    for _ in range(flips):
        send_key(app, key)
        if not wait_for_change(rammer[-1], rect, None, threshold, settle, max_wait):
            print("  Siden skiftede ikke. Er det den rigtige tast til at bladre med?")
            break
        rammer.append(capture(rect))

    tilbage = MODSAT.get(key)
    if tilbage:
        print("  ...og tilbage igen.")
        for _ in range(len(rammer) - 1):
            sidst = capture(rect)
            send_key(app, tilbage)
            wait_for_change(sidst, rect, None, threshold, settle, max_wait)
    elif len(rammer) > 1:
        print(f"  Bemærk: «{key}» kan ikke bladres tilbage — bogen står nu "
              f"{len(rammer) - 1} sider frem.")

    if len(rammer) < 2:
        return None
    box = detect_page_from_frames(rammer)
    if box is None:
        print("  Kunne ikke se nogen tydelig side i forskellen.")
    return box


# Procenttrin der tegnes som hjælpelinjer på forhåndsvisningen.
GUIDES = (5, 10, 15, 20, 25)

SKRIFTER = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Geneva.ttf")


def _font(size: int):
    for sti in SKRIFTER:
        try:
            return ImageFont.truetype(sti, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_guides(im: Image.Image) -> Image.Image:
    """Læg et procentgitter på forhåndsvisningen.

    Uden det skal man gætte, hvor meget «v 5» egentlig skærer af. Linjerne
    ligger netop der, hvor de fire kommandoer ville lande. De er stiplede, så
    man kan læse siden igennem dem og se, hvad der ville ryge med.
    """
    w, h = im.size
    lag = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lag)

    tyk = max(1, round(min(w, h) / 800))
    stiplet, mellem = max(8, w // 90), max(6, w // 120)
    stor = _font(max(11, round(min(w, h) / 48)))
    lille = _font(max(9, round(min(w, h) / 62)))

    def linje(vandret: bool, pos: int, fremhaev: bool):
        glorie = (255, 255, 255, 120)          # ses også på en mørk side
        farve = (220, 30, 60, 235 if fremhaev else 150)
        laengde = h if vandret else w
        i = 0
        while i < laengde:
            j = min(i + stiplet, laengde)
            xy = ([(pos, i), (pos, j)] if vandret else [(i, pos), (j, pos)])
            d.line(xy, fill=glorie, width=tyk * 4)
            d.line(xy, fill=farve, width=tyk * 2 if fremhaev else tyk)
            i = j + mellem

    def etiket(tekst, xy, anker, fremhaev):
        skrift = stor if fremhaev else lille
        kasse = d.textbbox(xy, tekst, font=skrift, anchor=anker)
        d.rectangle([kasse[0] - 3, kasse[1] - 2, kasse[2] + 3, kasse[3] + 2],
                    fill=(255, 255, 255, 205))
        d.text(xy, tekst, font=skrift, anchor=anker,
               fill=(170, 0, 45, 255) if fremhaev else (170, 60, 90, 215))

    for pct in GUIDES:
        fremhaev = pct % 10 == 0
        dx, dy = round(w * pct / 100), round(h * pct / 100)
        for vandret, pos in ((True, dx), (True, w - dx), (False, dy), (False, h - dy)):
            linje(vandret, pos, fremhaev)
        etiket(f"v{pct}", (dx + 5, 5), "la", fremhaev)
        etiket(f"h{pct}", (w - dx - 5, 5), "ra", fremhaev)
        etiket(f"o{pct}", (5, dy + 3), "lt", fremhaev)
        etiket(f"n{pct}", (5, h - dy - 3), "lb", fremhaev)

    return Image.alpha_composite(im.convert("RGBA"), lag).convert("RGB")


KANTER = {"v": ("venstre", "bredden"), "h": ("højre", "bredden"),
          "o": ("øverst", "højden"), "n": ("nederst", "højden")}


def apply_trims(base, trims, bredde: int, hoejde: int):
    """Læg procentvise afskæringer på grundkassen.

    Procenterne regnes altid ud fra grundkassen, ikke fra det man sidst så.
    Ellers ville «v 10» to gange ikke være det samme som «v 20», og «v -10»
    ville ikke føre tilbage til udgangspunktet.
    """
    bw, bh = base[2] - base[0], base[3] - base[1]
    l = base[0] + bw * trims["v"] / 100
    r = base[2] - bw * trims["h"] / 100
    t = base[1] + bh * trims["o"] / 100
    b = base[3] - bh * trims["n"] / 100
    return (max(0, int(l)), max(0, int(t)),
            min(bredde, int(r)), min(hoejde, int(b)))


def choose_crop(rect, outdir: Path, start_box=None, app=None, key="right",
                threshold=2.0, settle=0.35, max_wait=4.0):
    """Vis udsnittet, og lad brugeren justere én kant ad gangen."""
    if start_box:
        fuld, base, kilde = capture(rect), start_box, "--crop"
    else:
        # Bladr et par sider frem og tilbage først: det der ændrer sig, ER
        # bogsiden. En statisk analyse kan ikke se forskel på siden og et
        # sidepanel med tekst, så den bruges kun, hvis bladringen ikke lykkes.
        base = crop_by_flipping(rect, app, key, threshold, settle, max_wait) if app else None
        kilde = "fundet ved at bladre et par sider"
        fuld = capture(rect)
        if not base:
            base, kilde = detect_page(fuld), "gættet ud fra ét billede"

    trims = {k: 0.0 for k in KANTER}

    while True:
        box = apply_trims(base, trims, fuld.width, fuld.height)
        beskaaret = fuld.crop(box)
        sti = outdir / "udsnit.png"
        draw_guides(beskaaret).save(sti)

        markeret = fuld.copy()
        ImageDraw.Draw(markeret).rectangle(
            [box[0], box[1], box[2] - 1, box[3] - 1], outline=(255, 0, 0), width=6)
        markeret.save(outdir / "udsnit-helt-vindue.png")

        subprocess.run(["open", str(sti)], check=False)
        bw, bh = beskaaret.size
        print(f"\nVindue {fuld.width}×{fuld.height}  →  side {bw}×{bh}   beskæring {box}")
        aendret = {k: v for k, v in trims.items() if v}
        if aendret:
            print("  skåret af: " + ", ".join(
                f"{KANTER[k][0]} {v:g} %" for k, v in aendret.items()))
        else:
            print(f"  ({kilde})")
        print("  udsnit.png er åbnet — sådan bliver hver side i PDF'en.")
        print(f"  Linjerne viser {GUIDES[0]}, {', '.join(str(g) for g in GUIDES[1:])} % "
              f"fra hver kant og kommer ikke med i PDF'en.")
        print("  udsnit-helt-vindue.png viser hele vinduet med beskæringen markeret.")

        print("\n  [Enter] scan med denne beskæring")
        print("  v 5     skær 5 % af bredden væk i venstre side")
        print("  h 5     ...  i højre side")
        print("  o 5     skær 5 % af højden væk foroven")
        print("  n 5     ...  forneden")
        print("          (negativt tal lægger til igen, fx  v -5)")
        if app:
            print("  b       bladr igen og find siden på ny")
        print("  r       nulstil til det fundne forslag")
        print("  0       ingen beskæring — hele vinduet")
        print("  a       afbryd")

        svar = input("\nValg: ").strip().lower()
        if svar == "":
            return box
        if svar == "a":
            return None

        if svar == "0":
            base, kilde = (0, 0, fuld.width, fuld.height), "hele vinduet"
            trims = {k: 0.0 for k in KANTER}
        elif svar == "r":
            trims = {k: 0.0 for k in KANTER}
        elif svar == "b" and app:
            ny = crop_by_flipping(rect, app, key, threshold, settle, max_wait)
            fuld = capture(rect)
            if ny:
                base, kilde = ny, "fundet ved at bladre et par sider"
                trims = {k: 0.0 for k in KANTER}
        elif svar[0] in KANTER:
            kant = svar[0]
            navn, retning = KANTER[kant]
            tal = svar[1:].strip() or input(f"  Procent af {retning} fra {navn}: ").strip()
            try:
                pct = float(tal.replace(",", "."))
            except ValueError:
                print(f"  Skriv et tal, fx  {kant} 5")
                continue
            proev = dict(trims, **{kant: trims[kant] + pct})
            nyboks = apply_trims(base, proev, fuld.width, fuld.height)
            if nyboks[2] - nyboks[0] < 20 or nyboks[3] - nyboks[1] < 20:
                print("  Det ville ikke efterlade noget af siden.")
                continue
            trims = proev
        else:
            print("  Ukendt valg.")


# --------------------------------------------------------------------------- #
def parse_ints(text: str, count: int, name: str) -> tuple[int, ...]:
    parts = [int(v) for v in text.replace(" ", "").split(",")]
    if len(parts) != count:
        raise argparse.ArgumentTypeError(f"{name} skal være {count} tal adskilt af komma")
    return tuple(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Scan en online-bog til PDF.")
    ap.add_argument("-n", "--pages", type=int, default=500, help="Maks. antal sider")
    ap.add_argument("--key", choices=sorted(KEY_CODES), default="right",
                    help="Tast der bladrer frem (standard: right)")
    ap.add_argument("--window", type=int, help="Vindues-id (fra --list-windows)")
    ap.add_argument("--display", type=int, help="Scan en hel skærm, fx 2")
    ap.add_argument("--region", type=lambda s: parse_ints(s, 4, "--region"),
                    help="Globalt område som x,y,bredde,højde")
    ap.add_argument("--list-windows", action="store_true", help="Vis vinduer og afslut")
    ap.add_argument("--all-windows", action="store_true",
                    help="Vis også små og skjulte vinduer i listen")
    ap.add_argument("--yes", action="store_true", help="Spring bekræftelse af udsnit over")
    ap.add_argument("--out", type=Path, default=Path("out"), help="Outputmappe")
    ap.add_argument("--dpi", type=int, default=150, help="PDF-opløsning")
    ap.add_argument("--grayscale", action="store_true", help="Gråtoner (mindre fil)")
    ap.add_argument("--no-crop", action="store_true", help="Ingen beskæring — hele vinduet")
    ap.add_argument("--crop", type=lambda s: parse_ints(s, 4, "--crop"),
                    help="Beskæring i vinduet: venstre,top,højre,bund")
    ap.add_argument("--threshold", type=float, default=2.0, help="Følsomhed for sideskift")
    ap.add_argument("--settle", type=float, default=0.35, help="Pause efter sideskift")
    ap.add_argument("--max-wait", type=float, default=4.0, help="Maks. ventetid pr. side")
    ap.add_argument("--stop-after", type=int, default=3, help="Stop efter N ens sider")
    ap.add_argument("--rebuild-only", type=Path, help="Byg kun PDF af eksisterende mappe")
    args = ap.parse_args()

    if args.rebuild_only:
        d = args.rebuild_only
        pages_dir = d / "pages" if (d / "pages").is_dir() else d
        sider = sorted(pages_dir.glob("*.png"))
        box = None
        if not args.no_crop:
            box, kilde = args.crop, "--crop"
            if not box:
                box, kilde = load_box(d), "beskaering.txt"
            if not box:
                box, kilde = crop_from_pages(sider), "siderne selv"
            if box:
                print(f"Beskærer til {box} (fra {kilde})")
        build_pdf(sider, d / "bog.pdf", args.dpi, args.grayscale, box)
        return

    if args.list_windows:
        multi = len(displays()) > 1
        for w in list_windows(relaxed=args.all_windows):
            navn, detaljer = describe(w, multi)
            print(f"{w['id']:>7}  {navn}\n         {detaljer}")
        return

    require_screen_recording()

    # --- Hvad skal fotograferes? ------------------------------------------- #
    app, win = None, None
    if args.region:
        rect = args.region
    elif args.display:
        match = dict(displays()).get(args.display)
        if not match:
            sys.exit(f"Der er ingen skærm {args.display}. Fandt: "
                     f"{[n for n, _ in displays()]}")
        rect = match
    else:
        win = window_by_id(args.window) if args.window else choose_window(args.all_windows)
        if not win:
            sys.exit(f"Fandt intet vindue med id {args.window}. Prøv --list-windows.")
        win = bring_forward(win)          # skifter om nødvendigt skærmplads
        rect, app = win["rect"], win["owner"]
        plads = "fuldskærm" if win["fullscreen"] else f"skærm {win['display']}"
        print(f"\nVindue: {win['owner']} ({plads}) — område {rect}")

    outdir = args.out / datetime.now().strftime("%Y-%m-%d_%H%M")
    outdir.mkdir(parents=True, exist_ok=True)

    # --- Beskæring: find siden, vis den, lad brugeren justere -------------- #
    if args.no_crop:
        box = None
    elif args.yes:
        # Uden dialog venter vi med beskæringen til efter scanningen — der kan
        # den udledes af siderne selv, hvilket er langt mere pålideligt.
        box = args.crop
    else:
        box = choose_crop(rect, outdir, args.crop, app, args.key,
                          args.threshold, args.settle, args.max_wait)
        if box is None:
            print("Afbrudt — intet scannet.")
            return
        if win:
            bring_forward(win)
        else:
            countdown("\nHent bogen frem — scanningen starter om...", 5)

    pages = scan(args, rect, app, outdir, box)

    if box is None and not args.no_crop:
        box = crop_from_pages(pages)
        if box:
            print(f"\nBeskæring udledt af siderne: {box}")
    if box:
        save_box(outdir, box)
    build_pdf(pages, outdir / "bog.pdf", args.dpi, args.grayscale, box)


if __name__ == "__main__":
    main()
