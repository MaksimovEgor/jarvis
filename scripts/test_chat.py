#!/usr/bin/env python3
"""Текстовый CLI-клиент для проверки агента без микрофона.

Использование: python3 scripts/test_chat.py [http://asus-host:8000]
"""

from __future__ import annotations

import sys

import httpx

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
print(f"Джарвис ({url}). Ctrl+C для выхода.")

while True:
    text = input("> ")
    resp = httpx.post(f"{url}/chat/text", json={"text": text}, timeout=60)
    data = resp.json()
    print(data["reply"])
    if data.get("tool_calls"):
        print(f"  [инструменты: {', '.join(data['tool_calls'])}]")
