# eScanner

Scanner en online-bog side for side og samler resultatet i én PDF.

Tænkt til bøger der kun kan læses i en browser/viewer, hvor man bladrer med
piletasterne. Programmet fotograferer bogens vindue, sender en piletast, venter
til siden rent faktisk har skiftet, og gentager.

Bogen må gerne ligge på en anden skærm end terminalen, og gerne i fuldskærm —
vinduet vælges fra en liste i terminalen, ikke ved at pege med musen.

## Installation

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### Tilladelser (vigtigt)

macOS kræver to ting under **Systemindstillinger → Privatliv & sikkerhed**,
begge for den terminal du kører programmet fra:

| Tilladelse | Uden den sker der | 
| --- | --- |
| **Skærmoptagelse** | Alle sider bliver et billede af skrivebordsbaggrunden — vinduer er usynlige for skærmbilleder |
| **Tilgængelighed** | Piletasterne når aldrig frem til browseren |

Programmet tjekker selv skærmoptagelse ved start og siger til. Husk at starte
terminalen forfra efter du har slået tilladelserne til.

## Brug

```bash
./venv/bin/python eScanner.py
```

Du får en liste over åbne vinduer, vælger bogens vindue med et tal, og får så
beskæringen vist, før scanningen går i gang.

```bash
./venv/bin/python eScanner.py --list-windows          # se vinduer og id'er
./venv/bin/python eScanner.py --window 35221 -n 300 --grayscale
./venv/bin/python eScanner.py --display 2             # hele skærm 2
./venv/bin/python eScanner.py --region 420,180,900,1200
```

| Flag | Betydning |
| --- | --- |
| `--window ID` | Fotografér dette vindue (id fra `--list-windows`) |
| `--display N` | Fotografér en hel skærm |
| `--region x,y,b,h` | Fotografér et globalt område i skærmkoordinater |
| `--list-windows` | Vis vinduer og afslut |
| `--all-windows` | Tag også små og skjulte vinduer med i listen |
| `-n, --pages` | Maks. antal sider (stopper før, hvis bogen slutter) |
| `--key` | Tast der bladrer: `right`, `left`, `down`, `space`, `pagedown` … |
| `--grayscale` | Gråtoner — typisk 3-4× mindre PDF |
| `--no-crop` | Ingen beskæring — hele vinduet kommer med i PDF'en |
| `--crop v,t,h,b` | Fast beskæring i vinduet, i pixels |
| `--dpi` | PDF-opløsning, standard 150 |
| `--threshold` | Hvor stor forskel der tæller som et sideskift (standard 2.0) |
| `--max-wait` | Hvor længe der ventes på at siden skifter (standard 4 s) |
| `--stop-after` | Stop efter N ens sider i træk (standard 3) |
| `--yes` | Spring bekræftelsen af udsnittet over |
| `--rebuild-only MAPPE` | Byg kun PDF'en om ud fra allerede scannede sider |

Resultatet lander i `out/<dato_tid>/` med siderne som PNG i `pages/` og den
samlede `bog.pdf` ved siden af. Sidebillederne bliver liggende, så du kan bygge
PDF'en om med andre indstillinger uden at scanne forfra:

```bash
./venv/bin/python eScanner.py --rebuild-only out/2026-09-23_1200 --grayscale --dpi 200
```

### Beskæring

Programmet finder selv bogsiden inde i vinduet, så browserens værktøjslinje,
sidepaneler og viewerens baggrund ikke kommer med i PDF'en.

Det gør den ved at **bladre et par sider frem og tilbage og se hvad der ændrer
sig**. Det der skiftes ud, er bogsiden; indholdsfortegnelse, pile og bjælker
bliver stående. Det er den eneste metode der pålideligt kender forskel på
bogens tekst og et sidepanel med tekst — et enkelt skærmbillede rummer ikke den
oplysning. Kan den ikke bladre (ukendt tast, eller `--display`/`--region` hvor
der ikke er nogen app at sende tasten til), gætter den ud fra ét billede i
stedet, og så er der større grund til at se efter.

Du får resultatet at se, inden scanningen går i gang:

```
  Bladrer 2 sider frem for at se, hvad der ændrer sig...
  ...og tilbage igen.

Vindue 2940×1840  →  side 1220×1680   beskæring (1276, 20, 2496, 1700)
  (fundet ved at bladre et par sider)
  udsnit.png er åbnet — sådan bliver hver side i PDF'en.
  Linjerne viser 5, 10, 15, 20, 25 % fra hver kant og kommer ikke med i PDF'en.
  udsnit-helt-vindue.png viser hele vinduet med beskæringen markeret.

  [Enter] scan med denne beskæring
  v 5     skær 5 % af bredden væk i venstre side
  h 5     ...  i højre side
  o 5     skær 5 % af højden væk foroven
  n 5     ...  forneden
          (negativt tal lægger til igen, fx  v -5)
  b       bladr igen og find siden på ny
  r       nulstil til det fundne forslag
  0       ingen beskæring — hele vinduet
  a       afbryd
```

Du justerer **én kant ad gangen i procent**: `v 5` skærer 5 % af bredden væk i
venstre side, `n 10` skærer 10 % af højden væk forneden. Negative tal lægger til
igen.

For at gøre procenterne til noget man kan se frem for at gætte, er `udsnit.png`
forsynet med et **procentgitter**: stiplede linjer ved 5, 10, 15, 20 og 25 % fra
hver kant, mærket med den kommando der rammer dem — `v10`, `h20`, `o5`, `n15` og
så videre. Skal en stump sidepanel væk i venstre side, kan du læse direkte på
linjerne, om det er `v 5` eller `v 10`, der skal til. Linjerne er stiplede, så
siden kan læses igennem dem, og de har en lys kant, så de også ses på en mørk
side. De findes kun i forhåndsvisningen og kommer aldrig med i PDF'en — siderne
på disken røres ikke. Hver gang gemmes `udsnit.png` på ny, så du kan se resultatet, inden du
siger ja, og de valgte procenter vises under målene:

```
Vindue 2940×1840  →  side 1123×1680   beskæring (1373, 20, 2496, 1700)
  skåret af: venstre 8 %
```

Procenterne regnes altid ud fra det oprindelige forslag, ikke fra det du sidst
så. Derfor er `v 10` to gange det samme som `v 20`, og `v -10` fører præcis
tilbage til udgangspunktet. `r` nulstiller alle fire kanter.

Bladringen sættes tilbage bagefter, så bogen står samme sted som før. Bruger du
`space` til at bladre — en tast der ikke kan gå tilbage — siger programmet til,
at bogen er rykket et par sider frem.

**Siderne gemmes altid ubeskårne** som PNG. Beskæringen lægges først på, når
PDF'en bygges, og gemmes i `beskaering.txt`. Fortryder du, kan du bygge om med
en anden beskæring uden at scanne forfra:

```bash
./venv/bin/python eScanner.py --rebuild-only out/2026-09-23_1200 --crop 1276,20,2496,1700
./venv/bin/python eScanner.py --rebuild-only out/2026-09-23_1200 --no-crop
```

Er der ingen `beskaering.txt` — fx efter `--yes` — udleder `--rebuild-only` selv
beskæringen af de scannede sider. Det er samme metode som ovenfor, blot på hele
bogen i stedet for to prøvesider, og dermed det mest pålidelige resultat.

### Fuldskærm og flere skærmpladser

Et vindue i fuldskærm ligger på sin egen skærmplads (Space). Det bliver vist i
listen med markeringen **fuldskærm** eller **på en anden skærmplads**, og
programmet skifter selv derover, inden det begynder at fotografere — og venter
til skiftet er kørt færdigt, så den første side ikke bliver et billede af den
skærmplads du kom fra.

Er bogen der stadig ikke, så udvid listen:

```bash
./venv/bin/python eScanner.py --all-windows
```

Og hvis macOS ikke vil hente vinduet frem af sig selv, siger programmet til.
Klik så selv over på bogen og start med vinduets id, så springes både
vindueslisten og beskæringsdialogen over (beskæringen findes da automatisk):

```bash
./venv/bin/python eScanner.py --window 32277 --yes
```

### At stoppe undervejs

Programmet henter bogens vindue frem, så terminalen har ikke tastaturfokus
under scanningen. Derfor: **træk musen ud i et hjørne af en vilkårlig skærm**,
så stopper den efter den igangværende side. `Ctrl+C` virker også, hvis du først
klikker tilbage i terminalen. I begge tilfælde samles de sider du nåede, i en PDF.

## Søgbar tekst (valgfrit)

PDF'en indeholder billeder, ikke tekst. Vil du kunne søge i den:

```bash
brew install ocrmypdf
ocrmypdf -l dan out/2026-09-23_1200/bog.pdf bog_soegbar.pdf
```

## Noter

Kopiering af bøger der kun er stillet til rådighed til onlinelæsning kan være i
strid med ophavsretten eller tjenestens vilkår — tjek hvad der gælder for dit
eget materiale.
