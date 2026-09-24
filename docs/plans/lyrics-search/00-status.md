# Status: Тексты песен и поиск по каталогам

- Gate 1 — Product: APPROVED 2026-09-24 (+ книги, подкасты, детям, очередь)
- Gate 2 — Architecture: APPROVED 2026-09-24
- Gate 3 — Program Design: APPROVED 2026-09-24
- Gate 4 — Slice plan: APPROVED 2026-09-24 (делать подряд)

## Slices
- [x] Slice 1 — тексты
- [x] Slice 2 — очередь
- [x] Slice 3 — поиск Яндекс/YouTube/SoundCloud
- [x] Slice 4 — книги/подкасты/детям
- [x] Slice 5 — голос и доки

## Notes for a fresh session
- Продолжение docs/plans/music-library и docs/plans/yandex-music (всё задеплоено).
- Проверено 2026-09-24: Яндекс tracks_lyrics(format_="LRC") → синхронный текст (fetch_lyrics_async);
  lrclib.net/api/get (artist_name, track_name[, duration]) → syncedLyrics/plainLyrics, доступен с asus
  напрямую; Яндекс search(type_="all") → best, artists, albums, tracks, playlists.
- Поиск Яндекса (type_=all|podcast) отдаёт podcasts[] с album.type audiobook|podcast — так делим Книги/Подкасты.
- Детские станции: editorial:station-1 (Сказки для сна), -4 (Сказки), -5 (Развивайки), -17 (Колыбельные), -13 (детские новогодние).
- Яндекс отвечал 429 на серию запросов — поиск кэшировать, дёргать один раз на ввод (debounce).

## Состояние на 2026-09-24 (asus пропал)
- Код всех 5 слайсов написан локально (бэкенд + фронт, vue-tsc чистый), НЕ задеплоен.
- Новые тесты (test_lyrics, test_catalog, test_queue, фраза «покажи текст») скопированы на asus, их прогон
  завис (--- какой тест, неизвестно), и сразу после этого asus ушёл в офлайн (Tailscale offline, туннель на
  point не отвечает). Когда вернётся: `journalctl -b -1` (причина), затем прогнать тесты по файлам с
  `timeout`, найти зависший, задеплоить, проверить вживую тексты/поиск/книги/очередь.

## Итог (2026-09-24, после возвращения asus)
- asus не падал (uptime 15 ч) — пропадала связь при нехватке памяти: RAM 5,8 ГБ, своп 3,7 ГБ
  (jarvis-tts Vosk ~2,1 ГБ в свопе, ядро с Whisper ~1,1 ГБ, два gateway Hermes, dashboard, photon).
  Тесты тогда прошли (135 passed) — висел только ssh.
- Всё задеплоено и проверено вживую: тексты (Яндекс 21 строка, LRCLIB для SoundCloud), все 6 вкладок
  поиска (Яндекс 0,55 с), книга по главам, подкаст с последнего выпуска, исполнитель, очередь, голос.
- Починено по скриншотам: панели текста/очереди выталкивали кнопки за экран.
