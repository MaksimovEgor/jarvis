"""Видеокарта (GTX 1050, 2 ГБ) — одна на все задачи: расшифровка длинного
аудио (~1 ГБ VRAM) и локальная резервная LLM вместе не помещаются. Кто держит
lock — тот на GPU; остальные в это время идут на CPU или ждут.
"""

from __future__ import annotations

import asyncio

lock = asyncio.Lock()
