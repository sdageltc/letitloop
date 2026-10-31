"""orchestrator/codecs.py — Safe, versioned serialization and type preservation for LetItLoop."""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Type


class DurableCodecError(TypeError):
    """Raised when an object cannot be encoded or decoded by the codec registry."""


# Immutable JSON primitives for zero-overhead fast-path
_PRIMITIVES = (type(None), bool, int, float, str)


class DurableCodecRegistry:
    """Registry managing safe serialization and type hydration for durable execution.

    Invariants:
    1. Zero Dynamic Code Execution: Only explicit allowlisted built-in types or
       user-registered codecs are hydrated. Never instantiate arbitrary classes
       from untrusted WAL metadata.
    2. Symmetric Hydration: Values passed to step() yield the same logical type
       on initial execution and replay.
    """

    def __init__(self) -> None:
        self._type_to_codec: Dict[Type, Dict[str, Any]] = {}
        self._tag_to_codec: Dict[str, Dict[str, Any]] = {}
        self._register_builtin_codecs()

    def _register_builtin_codecs(self) -> None:
        # datetime
        self.register_codec(
            cls=datetime,
            type_tag="datetime",
            encode_fn=lambda dt: dt.isoformat(),
            decode_fn=lambda s: datetime.fromisoformat(s),
        )
        # uuid.UUID
        self.register_codec(
            cls=uuid.UUID,
            type_tag="uuid",
            encode_fn=lambda u: str(u),
            decode_fn=lambda s: uuid.UUID(s),
        )
        # pathlib.Path
        self.register_codec(
            cls=Path,
            type_tag="path",
            encode_fn=lambda p: str(p),
            decode_fn=lambda s: Path(s),
        )

    def register_codec(
        self,
        cls: Type,
        type_tag: str,
        encode_fn: Callable[[Any], Any],
        decode_fn: Callable[[Any], Any],
    ) -> None:
        """Register a custom codec for a type (e.g. Pydantic model or dataclass)."""
        if not isinstance(type_tag, str) or not type_tag:
            raise ValueError("type_tag must be a non-empty string.")
        codec_info = {
            "cls": cls,
            "tag": type_tag,
            "encode": encode_fn,
            "decode": decode_fn,
        }
        self._type_to_codec[cls] = codec_info
        self._tag_to_codec[type_tag] = codec_info

    def encode(self, val: Any, *, _visited: Optional[set] = None, _depth: int = 0) -> Any:
        """Recursively encode an object into a JSON-serializable structure with type tags."""
        # Fast path for primitives
        if type(val) in _PRIMITIVES:
            return val

        if _depth > 256:
            raise DurableCodecError("Maximum encoding depth (256) exceeded: object hierarchy too deep or recursive.")

        if _visited is None:
            _visited = set()

        val_id = id(val)
        if val_id in _visited:
            raise DurableCodecError(
                f"Circular reference detected while serializing object of type '{type(val).__name__}'."
            )

        # Registered custom codec (exact type or subclass)
        for cls, codec in self._type_to_codec.items():
            if isinstance(val, cls):
                try:
                    return {
                        "__lil_type__": codec["tag"],
                        "v": codec["encode"](val),
                    }
                except Exception as e:
                    raise DurableCodecError(f"Failed to encode '{codec['tag']}': {e}") from e

        # Containers & complex objects require cycle tracking
        _visited.add(val_id)
        try:
            # Pydantic v2 / v1 models (fallback if not explicitly registered with decode_fn)
            if hasattr(val, "model_dump") and callable(val.model_dump):
                return self.encode(val.model_dump(), _visited=_visited, _depth=_depth + 1)
            if hasattr(val, "dict") and callable(val.dict):
                return self.encode(val.dict(), _visited=_visited, _depth=_depth + 1)

            # Dataclasses (fallback if not explicitly registered)
            if dataclasses.is_dataclass(val) and not isinstance(val, type):
                return self.encode(dataclasses.asdict(val), _visited=_visited, _depth=_depth + 1)

            # Containers
            if isinstance(val, dict):
                return {str(k): self.encode(v, _visited=_visited, _depth=_depth + 1) for k, v in val.items()}
            if isinstance(val, (list, tuple)):
                encoded_list = [self.encode(x, _visited=_visited, _depth=_depth + 1) for x in val]
                if isinstance(val, tuple):
                    return {"__lil_type__": "tuple", "v": encoded_list}
                return encoded_list
        finally:
            _visited.remove(val_id)

        raise DurableCodecError(
            f"Unsupported type '{type(val).__name__}' cannot be serialized. "
            "Register a custom codec via register_codec() or return a supported primitive/model."
        )

    def decode(self, val: Any, *, _depth: int = 0) -> Any:
        """Recursively decode and hydrate tagged structures into their target Python types."""
        if type(val) in _PRIMITIVES:
            return val

        if _depth > 256:
            raise DurableCodecError("Maximum decoding depth (256) exceeded in payload.")

        if isinstance(val, dict):
            if "__lil_type__" in val and "v" in val:
                tag = val["__lil_type__"]
                raw_v = val["v"]
                if tag == "tuple":
                    if not isinstance(raw_v, (list, tuple)):
                        raise DurableCodecError(
                            f"Corrupted tuple payload: expected iterable, got {type(raw_v).__name__}"
                        )
                    try:
                        return tuple(self.decode(x, _depth=_depth + 1) for x in raw_v)
                    except DurableCodecError:
                        raise
                    except Exception as e:
                        raise DurableCodecError(f"Failed to hydrate tuple elements: {e}") from e

                if tag in self._tag_to_codec:
                    codec = self._tag_to_codec[tag]
                    try:
                        return codec["decode"](raw_v)
                    except Exception as e:
                        raise DurableCodecError(f"Failed to hydrate tagged '{tag}' payload: {e}") from e

                # Unregistered tag: preserve as dict for forward compatibility
                return {k: self.decode(v, _depth=_depth + 1) for k, v in val.items()}

            return {k: self.decode(v, _depth=_depth + 1) for k, v in val.items()}

        if isinstance(val, list):
            return [self.decode(x, _depth=_depth + 1) for x in val]

        return val


# Global default codec registry
DEFAULT_CODEC_REGISTRY = DurableCodecRegistry()


def encode_step_result(result: Any, registry: Optional[DurableCodecRegistry] = None) -> Any:
    """Encode result and verify valid JSON roundtrip."""
    if type(result) in _PRIMITIVES:
        return result

    reg = registry or DEFAULT_CODEC_REGISTRY
    encoded = reg.encode(result)
    # Validate strict JSON serializability
    try:
        json_str = json.dumps(encoded, ensure_ascii=False)
        return json.loads(json_str)
    except (TypeError, ValueError) as exc:
        raise DurableCodecError(f"Failed to serialize encoded object to JSON: {exc}") from exc


def decode_step_result(payload: Any, registry: Optional[DurableCodecRegistry] = None) -> Any:
    """Hydrate encoded JSON structure back into Python types."""
    if type(payload) in _PRIMITIVES:
        return payload

    reg = registry or DEFAULT_CODEC_REGISTRY
    return reg.decode(payload)
