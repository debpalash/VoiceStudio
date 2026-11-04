# Windows ROCm — ograniczona kwalifikacja WhisperX

[Pełny przewodnik angielski](windows-rocm.md) · [Instalacja Windows](windows.md)

W konfiguratorze lokalnego środowiska aplikacji wybierz **AMD ROCm** i jawnie
rozpocznij instalację. Zapis wyboru sam nie pobiera pakietów. Działające środowisko
można zmienić w ustawieniach wydajności; po zapisaniu wyboru zakończ aplikację
razem z procesem zasobnika i uruchom ją ponownie, a następnie potwierdź instalację.
Nie trzeba ustawiać zmiennych w terminalu. Domyślna konfiguracja Windows nadal
wybiera CUDA dla NVIDIA, a poza tym CPU.

Tłumaczenie NLLB również uwzględnia wybrane w aplikacji urządzenie obliczeniowe.
Zachowany w pamięci model przechodzi między CPU i akceleratorem po zmianie wyboru,
a po opuszczeniu akceleratora zwalnia jego pamięć podręczną. Nieudane umieszczenie
na GPU zachowuje dotychczasową ścieżkę awaryjną CPU. Nadal wymagane są lokalne
wagi i akceptacja warunków modelu.

Test połączenia w konfiguratorze uwzględnia ABI Pythona i wariant Torch wybranego
środowiska, także zapisane preferencje i jawne nadpisania zmiennymi środowiskowymi.
Sprawdza wyłącznie adresy pakietów zapisane w dołączonym pliku blokady zależności.
Dla pliku receptury AMD nieobecnego w tej blokadzie zgłasza niedostępność testu
i nie pobiera próbki.

Wymagane są Windows 11 x64, obsługiwana karta z macierzy AMD 7.2.1, odpowiedni
sterownik oraz zainstalowany przez użytkownika pakiet Microsoft Visual C++ x64.
Konfigurator nie instaluje sterownika i nie zmienia zapisanych głosów, projektów
ani modeli. Korzysta ze wspólnej receptury `scripts/windows-rocm-recipe.json`:
Python 3.12, ROCm/SDK 7.2.1, Torch i torchaudio 2.9.1+rocm7.2.1,
torchvision 0.24.1+rocm7.2.1 oraz CTranslate2 4.8.2 z koła HIP o przypiętych sumach.
Zwykłe koło CT2 CUDA nie uzyskuje obsługi HIP przez instalację AMD PyTorch.

Osobny [PR #3712](https://github.com/debpalash/VoiceStudio/pull/3712) proponuje
dobrany zestaw ROCm 10 i kompilację CTranslate2 z dodatkowymi architekturami RDNA 2.
W [testach RX 6700 XT](https://github.com/debpalash/VoiceStudio/issues/2468#issuecomment-6572416338)
oficjalne CT2 4.8.2 i 4.8.4 wykrywały kartę, ale transkrypcja kończyła się błędem.
Konkretna kompilacja opiekuna CT2 z kodem `gfx1031` przeszła krótkie testy
transkrypcji GPU; sama aktualizacja numeru wersji nie wystarcza.

Propozycja nadal wymaga akceptacji źródeł pakietów i przeglądu integracji.
Nie rozszerza zakresu kart tej receptury ani profilu WhisperX dla `gfx1201`.
Nie podmieniaj ręcznie kół w zarządzanym środowisku i nie mieszaj ROCm 10 z 7.2.1.
Lista architektur Torch lub rocBLAS nie potwierdza działania jąder CTranslate2
ani całego modelu; nadal potrzebne są przypięte sumy i testy wykonania obliczeń.

W środowisku Windows ROCm należącym do zainstalowanej aplikacji Electron wybiera
SDK dołączone do interpretera: usuwa odziedziczone `HIP_PATH`, `ROCM_HOME` i
`ROCM_PATH` wyłącznie ze środowiska procesów uruchamiających backend, instalację
oraz kontrole gotowości i importów. Ustawienia Windows i zapisane preferencje
pozostają bez zmian. Kopie źródłowe, własne komendy backendu i środowiska zarządzane
zewnętrznie zachowują te zmienne; dotyczy to także CPU/CUDA i pozostałych systemów.
Nadpisania architektury, widoczności GPU i MIOpen nadal podlegają ścisłej kontroli.
Brakujące, przekierowane lub niezgodne ścieżki SDK/TEMP/cache nadal blokują WhisperX.

## WhisperX w katalogu modeli

W istniejącym środowisku AMD wybierz **WhisperX** w karcie ASR katalogu modeli.
Kwalifikacja jest automatyczna, bez dodatkowego przełącznika. Automatyczny wybór
silnika na Windows HIP nadal preferuje faster-whisper. Nie zmieniono domyślnego
modelu `large-v3` ani zwykłych ścieżek CPU, CUDA i innych systemów.

Jedna wspólna kontrola dopuszcza wyłącznie ASR WhisperX i wyrównywanie wav2vec2,
sprawdzając też zgodność przed użyciem już wczytanych modeli. Ta sama kontrola
pozwala katalogowi zgłaszać rzeczywiste przyspieszenie ROCm zamiast CPU.
Zarejestrowany profil MIOpen wymaga:

- Windows x64, Python 3.12 i dokładnej, niezmienionej receptury 7.2.1 opisanej
  wyżej, WhisperX 3.4.5 oraz pyannote.audio 3.4.0. Inne wersje nie są dopuszczane
  na podstawie samej nazwy ROCm.
- Dostępnego urządzenia HIP nr 0 o architekturze **gfx1201** i pozytywnej
  ograniczonej czasowo kontroli CT2, w tym zgodności DLL. Pozostałe architektury
  wspierane przez recepturę nie uzyskują automatycznie kwalifikacji WhisperX.
- Fizycznych katalogów ASCII zarządzanego SDK core/devel tego interpretera.
  Nagłówki HIP, MIOpen, rocRAND i rocPRIM muszą już być rozpakowane; dotyczy to
  także `rocrand_xorwow.h`. Jawne `ROCM_PATH` musi wskazywać ten sam katalog devel.
  Odczyt katalogu modeli niczego nie pobiera ani nie rozpakowuje.
- Istniejących fizycznych katalogów ASCII `TEMP` i `TMP`, zgodnych ścieżek
  `TMPDIR` i zapamiętanego katalogu tymczasowego Pythona oraz zgodnych katalogów
  pamięci podręcznej. Ścieżki Unicode, względne, sieciowe i przekierowane nie
  kwalifikują profilu. Kontrola obejmuje ustawione katalogi MIOpen/XDG albo
  katalogi domowe i profilu używane przez domyślną pamięć podręczną.
- Braku niezweryfikowanych nadpisań SDK, mapowania architektury, widoczności GPU
  i MIOpen. Dotychczasowy tryb aplikacji `FAST` jest dozwolony. Wyłączenie dyskowej
  pamięci MIOpen nie potwierdza zgodności SDK ani ścieżek tymczasowych Unicode.

### Wariant Unicode wybierany przy starcie

Przy tej samej recepturze, dokładnych wersjach pakietów, karcie gfx1201 i
zweryfikowanej bibliotece HIP CT2 backend może wybrać skompilowane operacje
ATen/HIP dla fizycznych zarządzanych ścieżek SDK/TEMP/cache zawierających Unicode.
Nagłówki nadal muszą istnieć. Ścieżki względne, sieciowe i przekierowane, obce
nadpisania oraz uszkodzone pakiety pozostają zablokowane. Dopuszczane jest tylko
automatyczne obejście pamięci podręcznej dodane przez aplikację; ręczne ustawienie
`MIOPEN_DISABLE_CACHE` nie wystarcza.

Wybór następuje przed wczytaniem modeli i wyłącza MIOpen dla wszystkich modeli
Torch w tym procesie backendu. Obliczenia pozostają na GPU przez HIP. Działająca
konfiguracja ASCII zachowuje MIOpen, a osobne procesy silników mają własną politykę.
Dostępność i ponowne użycie modeli sprawdzają wybrany tryb oraz kontekst ścieżek;
późniejsza zmiana wymaga restartu backendu. Domyślne zachowanie CPU/CUDA/MPS/Linux
pozostaje zachowane.

Dla źródeł `4bb861465` RX 9070 XT przeszedł osobny test rzeczywistej funkcji
inicjalizacji z własnym SDK, TEMP i pamięcią podręczną w ścieżkach Unicode.
Zwykły wybór domyślnego `large-v3` utworzył VAD, HIP CT2 i aligner GPU bez
zastępowania kontroli zgodności. Zimne i ponowne wywołanie uzyskały czasy dla
12 z 12 rozpoznanych słów; pełne wyniki i granice VAD były identyczne z zachowanymi
wynikami ASCII/MIOpen. Rzeczywista generacja NLLB-600M w drugim wątku przeplatała
się z VAD; wagi i obserwowane obliczenia były na GPU, a wybrany tryb pozostawał
stały. To potwierdza przeplatanie zadań, a nie jednoczesne jądra ani wydajność.
Proces testowy i kontroler zakończyły się kodem zero, pliki chronione pozostały
niezmienione, a zadania i uchwyty zostały zamknięte.

Wcześniejsza próba pary nadal ma wynik błędny: do pomiaru nakładania krótkich
operatorów użyła zegara Windows o zbyt małej rozdzielczości. Jej pełne wyniki ASCII
służą do porównania; nie zmieniono poprzedniego wyniku na zaliczony. Osobna próba
Unicode korzysta z dokładnego zegara. Zwykły start `main.py`, OmniVoice i inne TTS,
osobne procesy silników oraz aktualna paczka wymagają własnej walidacji. Sam
HIPRTC nadal nie ma naprawionej obsługi ścieżek nagłówków Unicode.

## Zarządzany CosyVoice

Po jawnym wyborze ROCm rozpocznij instalację w **Model Catalogue → Engines →
CosyVoice**. Osobne środowisko używa CP312, Torch2.9.1 i SDK7.2.1; zachowuje
przejrzane piny zależności z poprawkami bezpieczeństwa. PyWORLD nadal wymaga
istniejących narzędzi C++. Pozostałe receptury CPU/CUDA/Linux/macOS i własne
instalacje źródłowe zachowują dotychczasowe działanie.

Aktualny marker jest publikowany atomowo po kontroli interpretera, pakietów,
przypiętych źródeł, rzeczywistego kernela HIP i lokalnego nagrania wzorcowego.
Stary marker CPU nie dopuszcza HIP. Adapter soundfile działa tylko w modułach
wejścia CosyVoice dla lokalnych WAV/FLAC/MP3, bez globalnej podmiany TorchAudio
i bez wymogu DLL TorchCodec. LLM, flow i HiFT muszą używać zadeklarowanego
urządzenia; pomocnicze ONNX pozostają na CPU. Jawny wybór CPU jest respektowany.
Zmiana urządzenia daje procesowi standardowe trzy sekundy na zwykłe zamknięcie
przed ograniczonym czasowo wymuszonym sprzątaniem; nadal żywy proces blokuje
uruchomienie jego następcy.
Kontrola osobnego środowiska odczytuje nazwę każdego pakietu raz, nadal odrzucając
duplikaty i niezgodne wersje. Nie zapamiętuje dopuszczenia między wywołaniami.
Zmiana odcisku kwalifikacji CosyVoice wymaga ponownego uruchomienia Install
i rzeczywistej kontroli natywnej; istniejące wagi modelu są wykorzystywane ponownie.
Kontrola bez wag nie zastępuje syntezy ani kwalifikacji nowego pakietu na sprzęcie.

Zdrowa istniejąca instalacja CosyVoice CP310/Torch2.7 na CPU pozostaje dostępna
jako CPU fallback po zmianie głównego środowiska na Auto lub ROCm. Sam wybór
nie unieważnia jej markera ani nie rozpoczyna ponownej instalacji. Wymiana
środowiska wymaga jawnej reinstalacji; własne kopie źródłowe zachowują swój kontrakt.

## Wagi znaku wodnego w trybie offline

Przy `HF_HUB_OFFLINE=1` lub `TRANSFORMERS_OFFLINE=1` AudioSeal najpierw sprawdza
lokalny plik wybrany przez zainstalowaną kartę modelu. Wstępne wczytywanie
rozpoznaje rzeczywistą nazwę cache; same wagi detektora nie wystarczają dla
generatora. Brak pliku pomija konstruktor pobierający wagi i zachowuje
dotychczasowe ostrzeżenie oraz fallback, więc nagranie może nie zawierać
niewidocznego znaku wodnego. AudioSeal używa dotychczasowych domyślnych
konstruktorów CPU. Te zasady cache dotyczą także pozostałych wspieranych platform.

## Osobny pakiet natywnego rozpoznawania mówców

Sortformer jest osobną, opcjonalną ścieżką rozpoznawania mówców przez Vulkan
w audio.cpp. W Ustawienia → Modele → Rozpoznawanie mówców jego pakiet wybiera
wyłącznie plik Q8_0 o wielkości 176 MB; instalacja i akceptacja nie obejmują Breeze
TTS. Istniejące wagi pozostają w cache, a usunięcie pakietu zachowuje pliki innych
modeli. Rejestr wskazuje licencję źródłowego modelu CC-BY-NC-4.0, lecz pochodzenie
kwantyzacji oraz pełne warunki komponentów i wyników nadal są niezweryfikowane.
Zmiana zapisanych warunków może wyświetlić zwykłą prośbę o ponowną akceptację.
To niezależna ścieżka od WhisperX: pozostają limity czterech mówców i 120 sekund,
bez wymuszenia dokładnej liczby osób. Sam test natywnego programu na Radeonie
nie potwierdza całego przepływu katalogu i dubbingu.

## Zakres dowodów i ograniczenia

Produkcyjny backend przetestowano na RX 9070 XT z niezmienionym domyślnym modelem
`large-v3`, bez nadpisania wyboru modelu i bez diagnostycznego wyjątku w bramce zgodności.
WhisperX sam utworzył domyślny VAD pyannote; rzeczywisty ffmpeg dekodował dźwięk
24 do 16 kHz, CT2 wykonywał ASR przez HIP, a wav2vec2 wyrównywał słowa na GPU.
Dla 4,8 s i powtarzanego nagrania 39,6 s uzyskano czasy odpowiednio 12/12 oraz
48/48 rozpoznanych słów. Dłuższy przebieg automatycznie rozpoznał francuski.
Rzeczywisty domyślny rozmiar partii wynosił 1, bez nadpisania. Raporty klasy i
instancji prawidłowo wskazywały przyspieszenie ROCm. Obsługiwana konfiguracja
korzystała z już wczytanych modeli. Nieobsługiwane nadpisania SDK/TEMP/cache
blokowały ich użycie przed kolejnym wykonaniem; przywrócenie środowiska pozwalało
ponowić pracę. Oba drzewa procesów zakończyły się kodem 0 bez wymuszenia.
Zarejestrowano zero prób połączeń zewnętrznych, odczytu plików tokena i zapisu poza
obszarem testu, a kontrola diaryzacji nie wywołała rozwiązania tokena. Wcześniejsze
próby biblioteki i backendu z `small` pozostają osobnymi dowodami diagnostycznymi.

To pokrycie czasów rozpoznanych słów, nie potwierdzenie rozpoznania każdego słowa
w nagraniu ani wszystkich modeli i języków. Walidacja pakietu i świeżej instalacji
Windows pozostaje osobnym zadaniem. Diaryzacja mówców pyannote oraz
nieznane możliwości pozostają zablokowane na GPU przed odczytem tokena i użyciem
ciepłego modelu. Zwykły test gotowości głównego środowiska ich nie certyfikuje.

W razie odrzucenia kwalifikacji można wybrać CPU w ustawieniach wydajności.
Brak opcjonalnych zasobów wyrównywania w trybie offline zachowuje rodzime czasy
słów bez pobierania. Ponowienie po naprawieniu zasobów nie oznacza uszkodzonego
importu. Oddzielne środowiska silników zachowują własne wymagania i walidację.

Przy zmianie receptury, wersji pakietów lub macierzy GPU autorzy powinni ponowić
odpowiednie próby sprzętowe przed odnowieniem rejestracji profilu WhisperX.
Dostępność, wykonanie przed użyciem wczytanych modeli i raportowanie urządzenia
muszą nadal korzystać z tej samej kontroli. Możliwość ASR nie dopuszcza diaryzacji.
