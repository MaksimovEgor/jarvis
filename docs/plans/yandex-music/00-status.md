# Status: Яндекс Музыка как основной источник

- Gate 1 — Product: APPROVED 2026-09-24 (с FLAC: лайки FLAC, кэш MP3 320, фолбэк на 320)
- Gate 2 — Architecture: APPROVED 2026-09-24
- Gate 3 — Program Design: APPROVED 2026-09-24
- Gate 4 — Slice plan: APPROVED 2026-09-24 (делать все слайсы подряд)

## Slices
- [x] Slice 1 — подключение (код устройства, статус, карточка)
- [x] Slice 2 — играть из Яндекса (sources, MP3 320, /media/file)
- [x] Slice 3 — FLAC и лайки (sync, импорт)
- [x] Slice 4 — волна Яндекса (все настройки Яндекса, фидбек)
- [x] Slice 5 — SoundCloud + калибровка совпадений
- [x] Slice 6 — метрика по источникам, доки

## Notes for a fresh session
- Продолжение фичи docs/plans/music-library (волна, лайки, dj, хранилище 10 ГБ) — всё задеплоено.
- У пользователя есть Яндекс Плюс. Хочет: Яндекс — основной (качество + его рекомендации),
  чего нет в Яндексе — YouTube и другие альтернативные источники.
- Проверено 2026-09-24: api.music.yandex.net доступен с asus напрямую (0,15 с); pip-пакет
  yandex-music 3.0.0 (MarshalX): search, rotor_station_tracks + feedback (Моя волна),
  users_likes/dislikes, tracks_download_info (MP3 до 320), device-code OAuth
  (request_device_code/poll_device_token). Lossless в библиотеке нет.
- Сейчас YouTube отдаёт максимум AAC 128k / Opus ~125k без Premium.
- Решено при Gate 4: поддержать ВСЕ настройки волны Яндекса (занятие, характер, настроение,
  язык), не только наши 6 настроений — голосом и чипами. Точные значения API проверить на аккаунте.

## Итог (2026-09-24)
- Всё задеплоено, 118 тестов зелёные. Аккаунт подключён (maksimoveg2016, Плюс), импортировано 1030 лайков.
- FLAC работает: get-file-info с ключом десктопного клиента → flac-mp4 → ремукс в .flac (24 бит / 44,1 кГц, ~45 МБ).
- Волна: старый rotor API (settings3 → 415, radioStarted → «condition is not met») мёртв — перешли на
  rotor/session/new + /tracks + /feedback (зёрна settingMoodEnergy/Diversity/Language). Проверены:
  грустное·русское, бег, 90-е, русский рок, обычная волна.
- С настройками волну ведёт только Яндекс (наши корзины не знают языка/жанра).
- Похожие: tracks_similar, пусто — сессия track:<id>.
- Баги, пойманные вживую: .flac не было в AUDIO_EXTS; ym-XXXXXXXX (11 симв.) принимался за id YouTube в /media/cover.
- Тестовые прослушивания удалены; фидбек «skip» от тестов ушёл в волну Яндекса (несколько событий) — откатить нельзя.
