"""Loads broker plugins. Built-ins live in traderdesk/plugins/<id>/plugin.yaml; third-party
packages register under the `traderdesk.brokers` entry point and ship the same manifest.
No broker is a special case in the core."""
from __future__ import annotations

import importlib
import json
import logging
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

from .base import BrokerAdapter, PluginManifest

log = logging.getLogger(__name__)
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "plugins"
ENTRY_POINT_GROUP = "traderdesk.brokers"


def _load_manifest(path: Path) -> PluginManifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema: dict = {}
    ref = data.get("settings_schema")
    if isinstance(ref, str):
        schema_path = (path.parent / ref).resolve()
        if schema_path.exists():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
    elif isinstance(ref, dict):
        schema = ref
    return PluginManifest.from_dict(data, schema=schema, path=str(path.parent))


class PluginRegistry:
    def __init__(self) -> None:
        self.manifests: dict[str, PluginManifest] = {}

    def discover(self, extra_dirs: list[Path] | None = None) -> "PluginRegistry":
        for d in [BUILTIN_DIR, *(extra_dirs or [])]:
            if not d.exists():
                continue
            for mf in sorted(d.glob("*/plugin.yaml")):
                try:
                    m = _load_manifest(mf)
                    self.manifests[m.id] = m
                except Exception as e:  # a broken plugin must not stop the app
                    log.error("plugin manifest %s failed to load: %s", mf, e)
        try:
            eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
        except TypeError:  # pragma: no cover - old importlib
            eps = metadata.entry_points().get(ENTRY_POINT_GROUP, [])
        for ep in eps:
            try:
                obj = ep.load()
                manifest = getattr(obj, "MANIFEST", None)
                if manifest:
                    m = PluginManifest.from_dict(manifest)
                    m.entry = f"{obj.__module__}:{obj.__name__}"
                    self.manifests[m.id] = m
            except Exception as e:
                log.error("plugin entry point %s failed: %s", ep.name, e)
        return self

    def get(self, plugin_id: str) -> PluginManifest:
        if plugin_id not in self.manifests:
            raise KeyError(f"no broker plugin named {plugin_id!r}")
        return self.manifests[plugin_id]

    def create(self, plugin_id: str, account_id: str, settings: dict[str, Any], secrets) -> BrokerAdapter:
        m = self.get(plugin_id)
        if not m.entry:
            raise RuntimeError(f"plugin {plugin_id} has no entry point")
        mod_name, cls_name = m.entry.split(":")
        cls = getattr(importlib.import_module(mod_name), cls_name)
        adapter = cls(account_id=account_id, settings=settings, secrets=secrets, manifest=m)
        missing = [k for k in m.settings_schema.get("required", []) if k not in settings]
        if missing:
            log.warning("account %s (%s) is missing settings: %s", account_id, plugin_id, missing)
        return adapter

    def list(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.manifests.values()]
