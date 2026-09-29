# Slices: Итерация 1 — Быстрее

Каждый слайс заканчивается так: тесты на asus зелёные → `SKIP_PIP=1 ./scripts/deploy.sh` → сервисы active →
проверка вживую (curl / телефон) → галочка в `00-status.md`. Перед выкаткой смотрю `AGENT_STATUS.md`
(jarvis-core общий).

1. **Tracer bullet — строка времени насквозь.** `metrics.begin/mark/record_server` только с `stt` и `route`, `timing` в
   событии `reply`, клиент считает `firstSound` и шлёт `POST /metrics/turn`, `LatencyLine` в свёрнутом виде
   («заговорил через X с»), `GET /metrics/latency` отдаёт пока просто число записей. Проверка: вопрос с телефона → строка под ответом, curl видит запись.
2. **Метрики по-настоящему.** Все этапы (`llm_first` без потока = `llm_total`, `tts_first`), kind хода, `summary()` с
   медианой/p90 и слиянием клиент+сервер, `prune()`, раскрытие строки по тапу, жёлтый цвет > 2,5 с. `tests/test_metrics.py`.
   С этого момента копится неделя «до».
3. **Быстрые команды.** Время, дата (порядковое словами), погода «сейчас», таймеры; работают и на asus. `tests/test_router_quick.py`.
   Проверка: «который час» голосом → строка «⚡ быстро ≤ 1 с».
4. **Поток LLM (без озвучки).** `LLMClient.stream`, `ChainLLM.cloud_stream`, `SentenceSplitter`, `run_agent(on_sentence)`;
   `llm_first` меряется честно. Озвучка пока по полному ответу. `tests/test_llm_stream.py`, `tests/test_orchestrator_stream.py`.
5. **Растущая озвучка.** `SpeechStream`, `chunk()` → 204, `_answer(on_speech)`, событие `speech.streaming`, проигрывание
   на клиенте до 204, без повтора озвучки в `reply`. `tests/test_speech_stream.py`, дополнения `test_core_flow.py`.
   Проверка: длинный вопрос голосом → голос начинается до появления текста ответа, в строке «заговорил через» < 2,5 с.
6. **«Прервано».** `interrupt()` помечает звучавший ход, «— прервано» в чате, `interrupted/silenced_after` в метриках.
   Проверка: «Джарвис» посреди ответа.
7. **Доки и итог.** `docs/architecture.html` (поток ответа, метрики, быстрые команды), `README.md` (`/metrics/latency`),
   дорожная карта, запись в «Завершено». Через неделю — сравнение медиан с целями Gate 1 (отдельная короткая сессия).
