# SectionLoft

Siatka wejściowa → rodzina przekrojów → dopasowane krzywe B-spline → loft, dla FreeCAD.

**Status: v0.2.** Algorytm, trzy obiekty parametryczne, parowanie konturów między
przekrojami, kreator i workbench. 152 testy, wszystkie przechodzą na FreeCAD 1.1.3.

Kolejność z §9 specyfikacji została zachowana: GUI powstało dopiero po tym, jak
v0.1 rozstrzygnęła empirycznie, że aproksymacja daje krzywe nadające się do loftu.

## Odpowiedź na pytanie v0.1

Tak, daje — ale dopiero po trzech poprawkach, których w specyfikacji nie było.
Wyniki na siatkach analitycznych, 12 przekrojów, tolerancja z automatu:

| Siatka | Tolerancja | Maks. odchyłka | Załamanie na szwie | Loft |
|---|---|---|---|---|
| Sfera R=50, siatka zgrubna | 1,443 mm | 0,271 mm | 0,38° | poprawna bryła |
| Sfera R=50, siatka gęsta | 0,538 mm | 0,263 mm | 0,37° | poprawna bryła |
| Walec R=20 h=80 | 0,772 mm | 0,110 mm | 0,33° | 90 445 mm³ vs 90 478 analitycznie (0,04%) |
| Prostopadłościan 40×30×60 | 2,0 mm | 0,000 mm | 0° | 64 800 mm³ dokładnie, 4 narożniki |

Budżet wydajności z §7.3 dotrzymany z zapasem: 50 880 trójkątów, 30 przekrojów,
cały łańcuch **1,53 s** przy budżecie 5 s (`bench.py`).

## Instalacja

FreeCAD 1.1 używa **katalogu wersjonowanego** — nie `%APPDATA%\FreeCAD\Mod`,
tylko:

```bash
robocopy . "%APPDATA%\FreeCAD\v1-1\Mod\SectionLoft" /E /XD __pycache__ .git /XF run_tests.py bench.py
```

Ścieżkę można zawsze sprawdzić przez `App.getUserAppDataDir()`. Alternatywnie
zostawić repozytorium gdziekolwiek i uruchamiać makro stamtąd — samo dopisuje
ścieżkę do `sys.path`.

## Użycie

**Droga główna.** Przełącz się na workbench SectionLoft, zaznacz siatkę
w drzewie, kliknij *Utwórz z siatki…*. Kreator ma trzy kroki:

1. **Przekroje** — kierunek cięcia (Auto wybiera najdłuższą oś bryły) i liczba
   przekrojów na suwaku 4–40.
2. **Dopasowanie** — jeden suwak „Wierność" od *Gładko* do *Dokładnie*,
   checkbox „Zachowaj narożniki", pod „Zaawansowane" reszta parametrów.
   Pod spodem na bieżąco: odchyłka od siatki w milimetrach.
3. **Powierzchnia** — bryła czy powłoka, zamknięcia (płaskie, otwarte, szpic).

Podgląd nie jest osobnym trybem: kreator od razu tworzy prawdziwe obiekty
parametryczne i edytuje ich właściwości. *Anuluj* wycofuje wszystko przez
transakcję dokumentu, *OK* zostawia to, co już widać.

Po zakończeniu w drzewie zostają trzy obiekty, edytowalne dalej normalnym
edytorem właściwości. Zmiana czegokolwiek przelicza łańcuch w dół.

**Droga ekspercka.** Cztery osobne polecenia tworzą po jednym obiekcie, plus
*Pokaż nieudane przekroje*, które rysuje na czerwono kontury, których nie udało
się dopasować.

**Droga skryptowa.**

```python
from freecad.sectionloft.objects import (make_section_set,
                                         make_fitted_sections,
                                         make_section_loft)

sections = make_section_set(doc, mesh_obj)
sections.Count = 16
fitted = make_fitted_sections(doc, sections)
loft = make_section_loft(doc, fitted)
doc.recompute()
print(fitted.Status, loft.Volume)
```

Bez dokumentu, headless (ta droga została z v0.1 i jest nadal testowana):

```python
from freecad.sectionloft.core import pipeline as pp
from freecad.sectionloft.core.fitting import FitParams

report = pp.run(mesh_obj.Mesh, pp.SliceParams(count=12), FitParams())
print(report.status)
```

## Testy

```bash
"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" run_tests.py
```

> **Uwaga przy uruchamianiu z zainstalowanym dodatkiem.** FreeCAD *importuje*
> skrypt po nazwie modułu, a katalogi `Mod` są na `sys.path` przed katalogiem
> skryptu. Jeśli w zainstalowanej kopii leży `run_tests.py`, uruchomi się ona,
> nie ta z katalogu roboczego — i testy zwalidują nieaktualny kod, nie mrugnąwszy
> okiem. Dlatego instaluj bez `run_tests.py` i `bench.py`
> (`robocopy ... /XF run_tests.py bench.py`) albo testuj z pustym
> `FREECAD_USER_HOME`.

152 testy, wszystkie przechodzą na FreeCAD 1.1.3. Kreator też jest testowany —
panel nie importuje `FreeCADGui`, więc daje się zbudować na offscreenowym Qt
i wyklikać programowo. Moduły `planes`, `contours`, `polyline` i `pairing` są
czystym numpy i uruchamiają się zwykłym interpreterem:

```bash
python run_tests.py
```

Fixture'y są generowane proceduralnie — w repozytorium nie ma plików binarnych.

## Odstępstwa od specyfikacji i dlaczego

**§8.6, `approximate()` kontra `interpolate()`.** Nieaktualne dla FreeCAD 1.1.3:
`approximate()` przyjmuje listę `App.Vector`, zgłoszenie #16319 jest naprawione.
Konwersja została zachowana dla zgodności z 1.0.

**§3.2, kolejność kroków 3 i 4.** Decymacja idzie *przed* detekcją narożników,
nie po. Douglas-Peucker z definicji zachowuje prawdziwe narożniki (to wierzchołki
o maksymalnym odchyleniu), a usuwa szum triangulacji, który inaczej przekracza
każdy próg kąta wystarczająco niski, by złapać rzeczywistą fazkę. W odwrotnej
kolejności wybór jest między gubieniem fazek a wymyślaniem dziesiątek fałszywych
narożników na każdym zaokrągleniu.

**Zakres przekrojów liczony z punktów siatki, nie z bbox.** Rzutowanie narożników
bbox przeszacowuje zasięg dla każdego kierunku, który nie jest osią główną:
sfera R=50 cięta wzdłuż (1,1,1) dostaje zakres ±86 zamiast ±50 i skrajne
płaszczyzny mijają siatkę. Funkcja `auto_range` (bbox) została, ale domyślnie
używana jest `auto_range_from_points`.

**Domyślny `inset` 2%.** Specyfikacja daje domyślny zakres ±bbox/2, czyli skrajne
płaszczyzny styczne do siatki — a to daje kontur zdegenerowany albo pusty.
Skrajne płaszczyzny są domyślnie cofnięte o 2% zakresu.

## Trzy rzeczy, których w specyfikacji nie było, a bez których to nie działa

**1. Płaszczyzna w rzędzie współpłaszczyznowych wierzchołków.** Sfera cięta
dokładnie na równiku (albo jakakolwiek bryła obrotowa cięta przez rząd
wierzchołków) — `crossSections()` obchodzi pierścień tam i z powrotem i zwraca
polilinię o zerowym polu, 321 punktów przy 160 unikalnych. Nie da się tego
naprawić po fakcie: kontur nie ma orientacji, więc nie ma też szwu ani loftu.
Rozwiązanie dwustopniowe: płaszczyzny lądujące w rzędzie wierzchołków są
przesuwane o 0,1% zakresu (`SliceParams.avoid_vertex_rows`), a gdyby coś się
przecisnęło — kontur o polu poniżej 1e-6·L² jest odrzucany z ostrzeżeniem.
Zobacz `pipeline._is_degenerate` i `planes.avoid_vertex_rows`.

**2. Szew zamkniętego konturu nie jest gładki.** `approximate()` na liście
punktów z domkniętym pierwszym punktem daje ciągłość tylko C0 na szwie, czyli
widoczne załamanie biegnące wzdłuż całego loftu. Rozwiązanie: lista punktów jest
przedłużana cyklicznie z obu stron, dopasowywana z jawnie podanymi parametrami
długości cięciwy, a potem przycinana z powrotem do dokładnie jednej pętli
(`fitting.approximate_closed`). Dopasowanie „widzi" dane po obu stronach szwu.

**3. Przy luźnej tolerancji OCC schodzi do minimalnej liczby biegunów** — 7 dla
całej pętli przy stopniu 5, czyli dwa segmenty na okrąg — i taka pętla nie ma
jak domknąć się gładko. Zmierzone: 7° załamania. Rozwiązanie: załamanie jest
mierzone i jeśli przekracza `max_seam_kink` (domyślnie 5°), dopasowanie jest
powtarzane z tolerancją o połowę mniejszą, do dwóch razy. Tolerancja jest
górnym ograniczeniem odchyłki, więc dopasowanie ciaśniej niż zażądano jest
zawsze dozwolone. Na siatce zgrubnej to zbija 6,96° do 0,38° i przy okazji
odchyłkę z 1,29 mm do 0,27 mm.

Punkt 3 jest bezpośrednią odpowiedzią na ryzyko „aproksymacja daje faliste
krzywe" z §10 — problemem okazała się nie falistość, tylko przeciwnie: zbyt
mała liczba stopni swobody i załamanie na szwie.

## Wiele brył

Odpowiedź na pytanie §11.2: **parowanie po centroidzie, mierzone w płaszczyźnie
przekroju.** Pole zawodzi, gdy ramię się zwęża; zawieranie opisuje inną relację
(otwór w konturze), nie tę. Centroid jest jedyną z trzech miar, która nie łamie
się na zwężeniu ani na obrocie.

Parowanie jest zachłanne, od najbliższej pary. Kontur, który nie ma pary,
zaczyna nowy łańcuch — siatka rozwidlająca się w połowie wysokości daje jeden
łańcuch na trzon i po jednym na każde ramię, zamiast błędu. Każdy łańcuch jest
loftowany osobno. Sytuacje niejednoznaczne (drugi kandydat prawie tak samo
blisko jak pierwszy — typowo symetryczne rozwidlenie) trafiają do
`AmbiguousSections` i do komunikatu w kreatorze, zamiast być cicho rozstrzygane.

## Struktura

```
freecad/sectionloft/
├── core/                 # bez FreeCAD GUI, bez dokumentu
│   ├── planes.py         # rodzina płaszczyzn, ramka lokalna, omijanie rzędów wierzchołków
│   ├── contours.py       # domykanie, orientacja, szew, normalna Newella
│   ├── polyline.py       # Douglas-Peucker, narożniki, odchyłka
│   ├── pairing.py        # łączenie konturów między przekrojami
│   ├── fitting.py        # aproksymacja B-spline (wymaga Part)
│   └── pipeline.py       # droga headless (wymaga Mesh, Part)
├── objects/              # Part::FeaturePython
│   ├── section_set.py
│   ├── fitted_sections.py
│   └── section_loft.py
├── gui/
│   ├── wizard.py         # panel zadania, nie importuje FreeCADGui
│   └── commands.py
├── icons/*.svg
├── init.py               # tryb konsolowy
├── init_gui.py           # workbench
└── tests/
```

`planes`, `contours`, `polyline` i `pairing` nie importują FreeCAD w ogóle.

## Jak przepływają dane między obiektami

Bez wchodzenia w stan Pythona, który nie przeżywa zapisu dokumentu:

- `SectionSet` publikuje `ContourCount` — ile wire'ów w compoundzie należy do
  której płaszczyzny. `FittedSections` odtwarza z tego grupy bez sięgania do
  proxy obiektu źródłowego.
- `FittedSections` publikuje `ChainSizes` i układa wire'y łańcuchami, więc
  `SectionLoft` wie, co z czym loftować, czytając jedną listę liczb.
- `FittedSections` przyjmuje też zwykły compound wire'ów z dowolnego obiektu —
  wtedy każdy wire jest osobnym przekrojem.

## Znane ograniczenia v0.2

- Szyny prowadzące (`Rails`) są zadeklarowane, ale jeszcze nieużywane — v0.3.
- Kontur wewnętrzny nie jest traktowany jako otwór, tylko jako osobna bryła.
  To druga połowa pytania §11.2 i wymaga innej relacji niż odległość centroidów.
- Detekcja narożników działa na fazkach i ostrych krawędziach; na zaokrągleniach
  o promieniu porównywalnym z tolerancją jest zgadywanką (ryzyko z §10, próg
  edytowalny, całość wyłączalna).
- `MinTravel` jest zaimplementowany i przetestowany jednostkowo, ale
  na realnych obudowach niesprawdzony — pytanie §11.1 zostaje otwarte.
- Loft z jednego łańcucha przy `StartCap = Szpic` i wyłączonej powierzchni
  prostokreślnej potrafi się wybrzuszyć przy wierzchołku — gładka powierzchnia
  od punktu do okręgu przestrzeliwuje promień. Zamierzone, ale warto wiedzieć.

## Co dał test w prawdziwym GUI

Workbench, pasek narzędzi, menu i pięć poleceń rejestrują się poprawnie; kreator
otwiera panel zadania, tworzy łańcuch, a *OK* zostawia trzy obiekty i bryłę
(walec R=20, wysokość 80: 96 391 mm³ wobec 96 511 analitycznie, 0,12% błędu,
`isValid() == True`, gładka powierzchnia NURBS bez fasetowania).

Wyszły trzy błędy, których testy headless nie mogły złapać — wszystkie
naprawione, każdy ma teraz własny test:

1. **`getStandardButtons()` rzucał `TypeError`.** Pod PySide6 kombinacja flag to
   `enum.StandardButton`, a `int()` odmawia jej konwersji. Trzeba rozpakować
   `.value`. Testy headless tej metody nigdy nie wołały — bo woła ją FreeCAD.
2. **`Gui.Control.closeDialog()` nie wycofywał obiektów.** FreeCAD woła
   `reject()` tylko przy *Anuluj*; zamknięcie panelu Escape'em, przełączeniem
   workbencha albo innym dialogiem po prostu kasuje widget. Użytkownik zostawał
   z trzema sierotami i — gorzej — otwartą transakcją, która połykała jego
   następne działania. Panel podpina się teraz pod sygnał `destroyed`.
3. **„Zaawansowane" nie zwijało się, tylko wyszarzało.** `QGroupBox`
   z `setCheckable(True)` wyłącza dzieci, ale ich nie ukrywa — krok 2 pokazywał
   sześć kontrolek zamiast dwóch i wypychał odczyt odchyłki poza widok.
   Zawartość siedzi teraz w osobnym widgecie, którego widoczność idzie za
   stanem checkboxa.

## Licencja

LGPL-2.1-or-later. **Uwaga: plik `LICENSE` nie został jeszcze dodany** — trzeba
wgrać oficjalny tekst LGPL-2.1 z https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt
