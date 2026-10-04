# Natywny Windows ROCm — DRAFT integracji desktopowej

[English guide](windows-rocm.md) · [Instalacja Windows](windows.md)

**DRAFT / niepełna walidacja.** Integracja aplikacji i silników zależy od
[receptury źródłowej](windows-rocm-source.md) z PR #2600. To nie jest oficjalne
wydanie, nowy zwalidowany instalator ani deklaracja pełnej zgodności z CUDA.
Oficjalny instalator v0.5.6 nie zawiera tych zmian desktopowych.

## Warunkowa konfiguracja aplikacji

Przyszły build tej gałęzi wymaga Windows 11 x64 oraz karty i sterownika z
[macierzy AMD ROCm 7.2.1](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityrad/windows/windows_compatibility.html).
Pozostaw środowisko obliczeniowe **Automatycznie** lub wybierz **AMD ROCm**,
a następnie jawnie rozpocznij instalację lokalnego środowiska. Ręczny wybór nie
zapewnia zgodności nieobsługiwanej karty. NVIDIA i inne nieobsługiwane komputery
zachowują dotychczasowe ścieżki. Oficjalny aktualizator nie gwarantuje zachowania
nieopublikowanej poprawki.

Konfigurator sprawdza wykonanie HIP i CTranslate2 przed zapisaniem gotowości.
Nie instaluje sterownika, WSL, narzędzi C++ ani deweloperskiego SDK. Wznowienie
oraz naprawa są jawne i muszą zachowywać dane. Zmiana pełnego znacznika receptury
wymaga naprawy starszego eksperymentalnego środowiska; samo sprawdzenie gotowości
go nie przebudowuje.
Konfigurator desktopowy i naprawa pomijają zapisane w lockfile pakiety CUDA Torch
oraz biblioteki NVIDIA przed instalacją sprawdzonych kół Windows HIP. Nie muszą
najpierw pobierać tymczasowego stosu CUDA; poniższe polecenia dla źródeł to osobna ścieżka.

## Wspólna receptura i źródła

`scripts/windows-rocm-recipe.json` jest używany przez konfigurację źródeł oraz
Electron: Python 3.12, torch/torchaudio 2.9.1+rocm7.2.1, torchvision 0.24.1,
`rocm[libraries]==7.2.1` i CTranslate2 4.8.2 Windows HIP sprawdzany sumami.
Zwykłe koło CTranslate2 z PyPI nie uzyskuje HIP przez samą instalację AMD PyTorch.
[Kontrakt utrzymania](../maintainers/windows-rocm.md) opisuje wymagane kontrole.
Silniki w osobnych środowiskach nadal mają własne piny i znaczniki weryfikacji.

Konfiguracja źródłowa zmienia tylko środowisko danego checkoutu:

```powershell
$env:OMNIVOICE_TORCH_VARIANT = 'rocm'
uv sync --frozen --no-dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'Base dependency sync failed' }
uv run --no-sync --python 3.12 python scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw 'Windows ROCm bootstrap failed' }
```

Nie naprawiaj działającego środowiska AMD zwykłym `uv sync`: wspólny lockfile
przywraca domyślne koła, więc po takiej synchronizacji trzeba ponownie wykonać
kontrolowaną konfigurację. Nie dodano deweloperskiego SDK ani produkcyjnego
obejścia przez zmienne środowiskowe.

## Zakres silników

- Faster-whisper transkrybuje przez sprawdzone koło CT2 HIP. Żądanie znaczników
  słów może korzystać z niezależnego modułu wyrównywania WhisperX; szybkie
  dyktowanie i tłumaczenie nie używają tej ścieżki. Nieobsługiwany język lub
  brak opcjonalnych zasobów offline pozostawia rodzime znaczniki słów.
  Błędy obliczania wyrównania nadal są zgłaszane. Podział na zdania jest poprawny
  tylko przy zachowaniu całego tekstu w tej samej kolejności i znaczników czasu słów.
  To nie jest pełny WhisperX.
  `HF_HUB_OFFLINE` lub `TRANSFORMERS_OFFLINE` ustawione na `1`, `on`, `yes` albo
  `true` (niezależnie od wielkości liter) blokują pobieranie opcjonalnych zasobów
  wyrównania. Zapisane modele nadal używają wybranego GPU; nie dodaje to inferencji CPU.
- Adapter pyannote 3.x przywraca metadane i jawne odczyty soundfile usunięte
  w torchaudio 2.9 bez zmiany zainstalowanych zależności ani obliczeń tensorowych.
  Test adaptera nie potwierdza jąder GPU ani diaryzacji.
  Kodeki odczytywane tylko sekwencyjnie, np. GSM w WAV, są dekodowane od początku
  bez nieobsługiwanego przewijania; dla ścieżek plików pyannote może wyciąć
  fragment po pełnym odczycie. Wycinanie od niezerowej pozycji w strumieniu
  plikopodobnym nadal podlega istniejącemu ograniczeniu pyannote.
- Świeże instalacje VoxCPM2 i CosyVoice mają osobne receptury ROCm i kontrole GPU.
  Ukończone instalacje CPU nie są automatycznie konwertowane ani fałszywie
  oznaczane jako przyspieszone. Migracja i ograniczenia są w opisach silników.
  Znaczniki ukończenia są zapisywane atomowo; częściowy zapis nie oznaczy
  instalacji jako poprawnej ani nie zablokuje jawnego ponowienia.
- Sortformer przez audio.cpp używa Vulkan, nie HIP. Wskaźnik gotowości odnosi
  się do wybranego silnika, a nie innego wpisu katalogu.
- Przygotowanie danych na CPU i opisane ścieżki rezerwowe pozostają możliwe,
  jak na CUDA. Sprawdź Ustawienia → Wydajność; sam dźwięk nie dowodzi użycia GPU.

## Znany problem ścieżek natywnych

**Pełny WhisperX pozostaje niedostępny w produkcji.** Zgłoszone testy offline
zainicjowały oficjalne deweloperskie SDK w środowisku ze znakami Unicode
oraz spacjami. Te same zależności przez aliasy ASCII, także **ze spacjami**,
przeszły MIOpen VAD + tiny ASR + wyrównywanie angielskie na GPU: 17/17 słów,
normalne zakończenie, bez cache jąder, tylko z `ROCM_PATH`, bez HIPRTC append
ani globalnych modułów SDK. Alias ASCII ze spacjami zakończył test w 11,45 s.

Ścieżki nie-ASCII, także z samymi polskimi znakami, nadal zawodzą w tych testach
natywnych. Środowisko w całości pod Unicode zawiodło przy `instance_norm` na
konwersji znaków ścieżki tymczasowej MIOpen. `ROCM_PATH` Unicode z interpreterem
i katalogiem tymczasowym ASCII przeszedł `instance_norm`, lecz przy LSTM zgłosił
brak nagłówka rocRAND mimo istnienia pliku. Nieudane procesy mogły zawiesić
zamykanie i wymagały własnego limitu czasu. Tryb UTF-8 Pythona oraz
`locale.LC_ALL=.UTF8` nie naprawiły błędu. ASCII ze spacjami działa; to nie jest
ogólny problem spacji ani gotowe obejście produkcyjne.
Pełna diaryzacja na modelach wymagających zgody pozostaje niezweryfikowana.

Diagnostykę bez modeli uruchamia się badanym Pythonem:
`scripts/smoke_windows_rocm.py --miopen`. Test sprawdza kernele InstanceNorm/LSTM
o wymiarach używanych przez VAD, a nie cały silnik. Odtwarza błąd polskiej ścieżki
SDK jako ograniczony czasowo błąd procesu; ścieżki ASCII, także ze spacjami,
przeszły test. Nie instaluje nagłówków i nie włącza WhisperX. Szczegóły zawiera
[opis dla opiekunów](../maintainers/windows-rocm.md#model-free-miopen-diagnostic).

## Dowody i ograniczenia przeglądu

Wcześniejsze testy RX 9070 XT sprawdziły transkrypcję i syntezę HIP oraz
podstawowy dubbing z Sortformerem Vulkan w zainstalowanej aplikacji. To wyniki
historyczne szerszego lokalnego buildu, nie tego selektywnego draftu. Nie
przeniesiono niezależnych poprawek języka przechwytywania, workerów ani cache WAV.
Dla tego transferu nie zbudowano finalnego instalatora.

Pozostają: czysty Windows, ścieżki nie-ASCII, nowy zainstalowany artefakt,
rzeczywiste regresje macOS/Linux/NVIDIA, długie zadania, jakość tłumaczeń i mowa
NVDA. NLLB wcześniej pomijał treść zarówno na CPU, jak i HIP; wyeksportowany
WAV nie potwierdza kompletności tłumaczenia.

Przed przyjęciem wymagane są CLA, zgoda na CI/Security forka i decyzja opiekuna
o adresach pobierania. Wyrównywanie może pobierać modele HF/TorchAudio oraz dane
NLTK po żądaniu transkrypcji; polityka dodatkowych adresów i zgody wymaga przeglądu
bez wyłączania działających znaczników słów. Wybór środowiska używa już wspólnej
kontrolki obsługiwanej klawiaturą; istotne ostrzeżenie o zgodności pozostaje
widoczne. Automatyczne testy klawiatury nie zastępują próby z NVDA. Zachowano dotychczasowe
lokalizacje profilu; ten draft nie zapisuje wszystkich danych obok programu.
