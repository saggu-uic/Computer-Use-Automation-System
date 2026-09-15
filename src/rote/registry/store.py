"""File-based catalog of profiles, capabilities, and policies. JSON, canonical hashing, semantic versions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from rote.config import CATALOG_DIR, POLICIES_DIR
from rote.models import Capability, Policy, ProductProfile


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(model: BaseModel) -> str:
    data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(model, Capability):
        # the hash identifies what executes; release metadata (version, status, provenance) is excluded,
        # so recompiling the same flow is recognised as the same capability instead of a new version
        data.pop("provenance", None)
        data.pop("status", None)
        data.pop("version", None)
    if isinstance(model, ProductProfile):
        data.get("entry", {}).pop("origin", None)  # deployment detail, not product knowledge
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


_DEFAULTS_TO_DROP = {"exact": True, "fragile": False, "proposed": False}


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _DEFAULTS_TO_DROP and item == _DEFAULTS_TO_DROP[key]:
                continue
            if key in ("expect", "may_follow", "constants", "review_flags") and item in ([], {}):
                continue
            out[key] = _prune(item)
        return out
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def dump_model(model: BaseModel) -> dict[str, Any]:
    return _prune(model.model_dump(mode="json", by_alias=True, exclude_none=True))


def _semver_key(version: str) -> tuple[int, int, int]:
    return tuple(int(p) for p in version.split("."))  # type: ignore[return-value]


class Catalog:
    def __init__(self, root: Path | None = None, policies: Path | None = None) -> None:
        self.root = root or CATALOG_DIR
        self.policies_dir = policies or POLICIES_DIR

    # ---------------- profiles and policies ----------------
    def products(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/profile.json"))

    def profile(self, product: str) -> ProductProfile:
        path = self.root / product / "profile.json"
        return ProductProfile.model_validate_json(path.read_text(encoding="utf-8"))

    def profile_for_origin(self, origin: str) -> ProductProfile | None:
        wanted = _origin(origin)
        for product in self.products():
            profile = self.profile(product)
            if _origin(profile.entry.origin) == wanted:
                return profile
        return None

    def policy(self, product: str) -> Policy:
        path = self.policies_dir / f"{product}.policy.json"
        return Policy.model_validate_json(path.read_text(encoding="utf-8"))

    # ---------------- capabilities ----------------
    def _capability_dir(self, cap_id: str, product: str | None = None) -> Path | None:
        name = cap_id.rsplit(".", 1)[-1]
        products = [product] if product else self.products()
        for prod in products:
            candidate = self.root / prod / "capabilities" / name
            if candidate.exists():
                return candidate
        return None

    def versions(self, cap_id: str) -> list[str]:
        folder = self._capability_dir(cap_id)
        if not folder:
            return []
        versions = []
        for path in folder.glob("*.json"):
            if re.fullmatch(r"\d+\.\d+\.\d+", path.stem):
                try:
                    if Capability.model_validate_json(path.read_text(encoding="utf-8")).id == cap_id:
                        versions.append(path.stem)
                except ValueError:
                    continue
        return sorted(versions, key=_semver_key)

    def load_capability(self, ref: str) -> tuple[Capability, Path]:
        """ref: a file path, `id`, `id@1`, `id@1.2`, or `id@1.2.3`. Picks the newest matching version."""
        path = Path(ref)
        if path.suffix == ".json" and path.exists():
            return Capability.model_validate_json(path.read_text(encoding="utf-8")), path
        cap_id, _, wanted = ref.partition("@")
        versions = self.versions(cap_id)
        if wanted:
            versions = [v for v in versions if v == wanted or v.startswith(wanted + ".")]
        if not versions:
            raise FileNotFoundError(f"no capability matches {ref!r}")
        folder = self._capability_dir(cap_id)
        path = folder / f"{versions[-1]}.json"
        return Capability.model_validate_json(path.read_text(encoding="utf-8")), path

    def capability_path(self, cap: Capability) -> Path:
        name = cap.id.rsplit(".", 1)[-1]
        return self.root / cap.app.product / "capabilities" / name / f"{cap.version}.json"

    def save_capability(self, cap: Capability) -> Path:
        cap.provenance.content_hash = content_hash(cap)
        path = self.capability_path(cap)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dump_model(cap), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def find_same_content(self, cap: Capability) -> tuple[Capability, Path] | None:
        digest = content_hash(cap)
        for version in self.versions(cap.id):
            existing, path = self.load_capability(f"{cap.id}@{version}")
            if content_hash(existing) == digest:
                return existing, path
        return None

    def next_version(self, cap_id: str) -> str:
        versions = self.versions(cap_id)
        if not versions:
            return "1.0.0"
        major, minor, _patch = _semver_key(versions[-1])
        return f"{major}.{minor + 1}.0"

    def all_capabilities(self) -> list[Capability]:
        found = []
        for path in sorted(self.root.glob("*/capabilities/*/*.json")):
            try:
                found.append(Capability.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return found


def _origin(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").replace("localhost", "127.0.0.1")
    return f"{parsed.scheme}://{host}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
