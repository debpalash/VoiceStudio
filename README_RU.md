<div align="center">
  <img src="docs/logo.png" alt="VoiceStudio" width="88" />
  <h1>VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="VoiceStudio в рейтинге Trendshift" width="220" height="48" /></a>
  </p>
  <p><strong>Клонирование и создание голоса, дубляж видео, диктовка, расшифровка и запись аудиокниг на 646 языках — с открытым исходным кодом.</strong></p>
  <p>
    <a href="https://voicestudio.sh/?utm_source=github&utm_medium=readme&utm_campaign=project">Сайт</a> ·
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest">Скачать</a> ·
    <a href="#начало-работы">Начало работы</a> ·
    <a href="#документация">Документация</a> ·
    <a href="https://discord.gg/bzQavDfVV9">Discord</a> ·
    <a href="README.md">English</a> ·
    <a href="README_CN.md">简体中文</a> ·
    <a href="README_JA.md">日本語</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main" alt="CI" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio" alt="Последний релиз" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
  </p>
</div>

![Обзор приложения на Electron: клонирование голоса, создание голоса, дубляж и управление моделями](docs/media/electron/voicestudio.gif)

## Ваш голос. Ваш рабочий процесс.

| Создавайте | Выпускайте | Подключайте |
| :--- | :--- | :--- |
| Клонируйте голос или создайте свой | Дублируйте видео с речью, уложенной по времени | Локальный API и MCP для агентов |
| Диктуйте через плавающий виджет | Истории, аудиокниги и пакетные задания | Повторяемые рецепты и отслеживаемые папки |

Начните с **VoiceStudio** (движок по умолчанию, на основе k2-fsa/OmniVoice) или выберите другой движок. [Каталог возможностей и движков](docs/feature-catalog.md).

Локальные сценарии выполняются на вашем оборудовании. Удалённые сервисы необязательны; аналитика использования включается только с вашего согласия.

<details>
<summary><strong>Рабочие пространства</strong> · Клонирование, дубляж, создание голоса и модели</summary>

<table>
  <tr>
    <td><img src="docs/media/electron/voice-cloning.png" alt="Рабочее пространство клонирования голоса в Electron со встроенным демонстрационным голосом" width="100%" /></td>
    <td><img src="docs/media/electron/dubbing.png" alt="Рабочее пространство дубляжа видео в Electron" width="100%" /></td>
  </tr>
  <tr><td align="center">Клонирование голоса</td><td align="center">Дубляж видео</td></tr>
  <tr>
    <td><img src="docs/media/electron/voice-design.png" alt="Описание голоса в рабочем пространстве создания голоса в Electron" width="100%" /></td>
    <td><img src="docs/media/electron/models.png" alt="Установка локальных речевых моделей и управление ими" width="100%" /></td>
  </tr>
  <tr><td align="center">Создание голоса</td><td align="center">Локальные модели</td></tr>
</table>

<img width="2628" height="1950" alt="Рабочее пространство настольного приложения VoiceStudio" src="https://github.com/user-attachments/assets/b474497d-a453-49a3-a2dd-f023ec6b7659" />

</details>

## Начало работы

### Установка одной командой (macOS / Linux)

```sh
# Последний релиз Electron
curl -fsSL https://voicestudio.sh/install | sh

# Конкретный опубликованный релиз Electron (замените X.Y.Z)
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z

# Собрать текущую ветку main и установить настольное приложение
curl -fsSL https://voicestudio.sh/install | sh -s -- --main

# Удалить приложение, сохранив ваши данные
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall
```

В Windows выполните `irm https://voicestudio.sh/install | iex` в PowerShell.
Для загрузки релиза нужны curl и утилита для SHA-256. Для `--main` нужны Git,
Node.js 22+, Bun, Rust/Cargo и инструменты сборки для вашей платформы; см.
[требования и поведение установщика](docs/install/script.md).
Установщик сохраняет ваши настройки, проекты и модели. Более старые версии
должны содержать пакеты Electron; на архивные сборки Tauri он не переключается.

Или скачайте приложение со страницы [Releases](https://github.com/debpalash/VoiceStudio/releases/latest) (macOS `.dmg`, Windows `.exe`, Linux `.AppImage` или `.deb`) и следуйте руководству для вашей платформы:

**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**

| Оборудование | Поддержка |
|---|---|
| Видеокарта NVIDIA (Windows / Linux) | Ускорение через CUDA |
| Apple Silicon | Ускорение через Metal (MPS) |
| Без дискретной видеокарты (встроенная графика Intel/AMD, старые ПК) | Полностью работает на процессоре, но медленнее; при установке ставится небольшая CPU-сборка PyTorch (около 5 ГБ свободного места) |
| Windows на ARM (Snapdragon X и др.) | **Экспериментально**, проверка не завершена: нативное приложение ARM64, Python-бэкенд x64 под эмуляцией, только процессор |
| Mac на Intel | Только интерфейс приложения; подключайтесь к удалённому бэкенду ([почему](docs/install/macos.md)) |

Откройте **Voice cloning** («Клонирование голоса»), выберите голос или добавьте чистую эталонную запись, введите текст и запустите генерацию. Когда приложение предложит, установите нужную модель. Требования к оборудованию зависят от движка; см. [производительность](docs/performance.md).

### Установка через промпт

Вставьте в своего агента для программирования (Claude Code, Codex, Cursor, …):

```text
Install the VoiceStudio Electron app on this device and verify it works, following
https://github.com/debpalash/VoiceStudio/blob/main/docs/install/agent.md
```

[Руководство для агентов](docs/install/agent.md) описывает определение оборудования, повторное
использование существующих данных, запрос подтверждения перед загрузкой моделей и пробную
генерацию. Агенты с поддержкой навыков могут также выполнить `npx skills add debpalash/VoiceStudio`.

<details>
<summary><strong>Запуск предварительной версии Electron из исходного кода</strong></summary>

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api  # подготовить зависимости Python перед запуском Electron
bun run dev
```

Требования и настройка бэкенда описаны в [руководстве по Electron](electron/README.md).

Команда `bun run smoke-test` собирает и запускает изолированное упакованное приложение Electron.
Добавьте `-- --install`, чтобы проверить установку управляемой среды выполнения по сети.

</details>

> **Electron — единственное настольное приложение и веб-интерфейс.** Версия 0.5.3 была последним релизом на Tauri. Пользователям Tauri нужно [установить Electron отдельно](docs/electron-migration.md). Устаревшая оболочка Tauri и прежние точки входа интерфейса удалены.

## Документация

| Что нужно | С чего начать |
|---|---|
| Помощь с установкой | [Устранение неполадок](docs/install/troubleshooting.md) · [Загрузка моделей](docs/downloading-models.md) |
| Модели и качество звука | [Руководства по движкам](docs/engines/README.md) · [Тесты производительности](docs/benchmarks.md) |
| Интеграции | [Локальный API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Примеры](examples/README.md) |
| Разработка | [Участие в проекте](.github/CONTRIBUTING.md) · [Electron](electron/README.md) · [Список изменений](CHANGELOG.md) |

Навыки для агентов: `npx skills add debpalash/VoiceStudio` — выберите **voicestudio** для работы со звуком или **voicestudio-maintainer** для сопровождения репозитория.

## Спонсоры

<a href="https://forms.gle/2PYCvd39hbwijzX37"><img src="docs/media/sponsor-slot.svg" alt="Ваш бренд — подайте заявку на место избранного спонсора VoiceStudio" width="640" /></a>

**Станьте избранным партнёром.** [Подать заявку на платное размещение](https://forms.gle/2PYCvd39hbwijzX37) · [Написать нам](mailto:partner@voicestudio.sh)

Поддержать разработку: [Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [Подробнее о спонсорстве](SPONSORS.md)

## Лицензия и ответственное использование

Лицензия [AGPL-3.0](LICENSE) распространяется на приложение, включая коммерческое использование на её условиях. Условия для весов моделей, токенизаторов, голосов и сгенерированных результатов отдельные; платная лицензия на приложение этих прав не даёт. Клонируйте голоса только с разрешения их владельцев. Менеджер моделей теперь показывает версионированные сведения об источниках моделей, ссылки на подтверждения и нерешённые вопросы проверки из [общего реестра](docs/licensing/model-inventory.md). Необязательный [предпросмотр проверенных файлов](docs/licensing/model-review-workflow.md) не ограничивает обычные загрузки и не подтверждает право на коммерческое использование. См. [подробности о лицензии](LICENSE-NOTICE.md).
