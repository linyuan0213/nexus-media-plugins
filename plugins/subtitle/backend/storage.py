"""媒体库路径与存储后端解析."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.storage import LocalStorageBackend, StorageBackendFactory
from app.storage.backends.base import StorageBackend, StorageType
from app.storage.config_models import LocalStorageConfig
from app.utils import PathUtils


@dataclass(frozen=True)
class LibraryRoot:
    path: str
    backend_id: str
    backend: StorageBackend
    media_type: str


class BackendResolver:
    def __init__(self, app_context):
        self._context = app_context
        self._roots: list[LibraryRoot] | None = None

    def roots(self) -> list[LibraryRoot]:
        if self._roots is not None:
            return self._roots
        roots: list[LibraryRoot] = []
        cfg = self._context.media_config_service.get_config() if self._context else {}
        for media_type, prefix in (("movie", "movie"), ("tv", "tv"), ("anime", "anime")):
            paths = cfg.get(f"{prefix}_path") or []
            backend_ids = cfg.get(f"{prefix}_backend") or []
            for index, path in enumerate(paths):
                if not path:
                    continue
                backend_id = str(backend_ids[index]) if index < len(backend_ids) and backend_ids[index] else "local"
                backend = self.get_backend(backend_id)
                if backend is not None:
                    roots.append(LibraryRoot(_norm(path), backend_id, backend, media_type))
        self._roots = roots
        return roots

    def get_backend(self, backend_id: str) -> StorageBackend | None:
        value = str(backend_id or "local")
        if value.lower() in ("", "local", "0"):
            return LocalStorageBackend(LocalStorageConfig(id="local", name="本地", type=StorageType.LOCAL))
        try:
            info = self._context.storage_backend_service.get_backend(int(value))
        except (TypeError, ValueError, AttributeError):
            return None
        if not info or not info.get("enabled"):
            return None
        config_info = StorageBackendFactory.get_config_info(str(info.get("type") or ""))
        if not config_info:
            return None
        stype, config_cls = config_info
        config = config_cls(
            id=str(info.get("id") or value),
            name=info.get("name") or "",
            type=stype,
            enabled=True,
        )
        for key, val in (info.get("config") or {}).items():
            if hasattr(config, key):
                setattr(config, key, val)
        return StorageBackendFactory.create(config)

    def resolve(self, path: str) -> tuple[StorageBackend, LibraryRoot] | None:
        normalized = _norm(path)
        matches = [root for root in self.roots() if PathUtils.is_path_in_path(root.path, normalized)]
        if not matches:
            return None
        root = max(matches, key=lambda item: len(item.path))
        return root.backend, root

    def resolve_or_local(self, path: str) -> tuple[StorageBackend, str]:
        resolved = self.resolve(path)
        if resolved:
            return resolved[0], resolved[1].path
        return self.get_backend("local") or LocalStorageBackend(
            LocalStorageConfig(id="local", name="本地", type=StorageType.LOCAL)
        ), _norm(os.path.dirname(path))


def _norm(path: str) -> str:
    return os.path.normpath(str(path or "").replace("\\", "/"))
