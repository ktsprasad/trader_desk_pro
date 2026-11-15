"""Secrets vault. API keys and tokens live in the OS keychain (Windows Credential Manager
via keyring), never in the repo or a plain .env. Tests use the in-memory store."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
SERVICE = "trader-desk-pro"


class SecretStore:
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringStore(SecretStore):
    def __init__(self, service: str = SERVICE):
        import keyring  # imported lazily so tests never touch the real keychain
        self._kr = keyring
        self.service = service

    def get(self, key):
        return self._kr.get_password(self.service, key)

    def set(self, key, value):
        self._kr.set_password(self.service, key, value)

    def delete(self, key):
        try:
            self._kr.delete_password(self.service, key)
        except Exception:
            pass


class MemoryStore(SecretStore):
    def __init__(self, initial: dict[str, str] | None = None):
        self._d = dict(initial or {})

    def get(self, key):
        return self._d.get(key)

    def set(self, key, value):
        self._d[key] = value

    def delete(self, key):
        self._d.pop(key, None)


def secret_key(account_id: str, name: str) -> str:
    return f"{account_id}:{name}"
