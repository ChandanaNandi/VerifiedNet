"""Inference backend abstraction + deterministic fake + optional local backend (Gate 8).

The evaluation package stays OFFLINE by default: the only backend used by the test
suite and the deterministic pipeline is ``FakeInferenceBackend`` (no network, no
subprocess). ``OllamaBackend`` is provided for OPTIONAL local integration only; it
imports its network client LAZILY inside ``generate`` so importing this module
never pulls in a network client, and it is exercised solely by integration tests
that skip when no local model is available.

Determinism note: the framework requests greedy decoding (temperature 0, fixed
max tokens/stop, and a seed when the backend supports one). A real local backend
may not guarantee bit-identical text across builds; the framework does NOT claim
model-output bit-identity — it guarantees deterministic PROMPT construction,
deterministic parsing/validation/normalization, and a deterministic mapping from
a given model output to a prediction. The FakeInferenceBackend is fully
deterministic end to end.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel


class InferenceError(VerifiedNetError):
    """A model backend failed to produce a response."""


class BackendUnavailableError(InferenceError):
    """The model backend could not be reached."""


class InferenceTimeoutError(InferenceError):
    """The model backend did not respond within the deadline."""


class DecodingConfig(StrictModel):
    """Deterministic decoding configuration (greedy by default)."""

    schema_version: Literal[1] = 1
    temperature: float = 0.0
    max_tokens: int = Field(default=256, ge=1)
    stop: tuple[str, ...] = Field(default_factory=tuple)
    seed: int | None = None

    @model_validator(mode="after")
    def _greedy(self) -> DecodingConfig:
        if self.temperature < 0:
            raise ValueError("temperature must be >= 0")
        return self

    @property
    def config_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stop": list(self.stop),
            "seed": self.seed,
        }
        return "dec-" + sha256_canonical(payload)[:16]


class InferenceResponse(StrictModel):
    """The raw text a backend returned (never authoritative; parsed downstream)."""

    schema_version: Literal[1] = 1
    text: str


@runtime_checkable
class InferenceBackend(Protocol):
    """A minimal text-in/text-out inference backend."""

    @property
    def backend_id(self) -> str: ...

    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        ...


class FakeInferenceBackend:
    """A deterministic, offline backend for tests and the model-free pipeline.

    ``responder`` maps a rendered prompt (+ decoding) to raw text, enabling
    well-formed, malformed, or edge-case responses without any real model. Set
    ``fail`` to simulate an unavailable backend or a timeout.
    """

    def __init__(
        self,
        *,
        responder: Callable[[str, DecodingConfig], str] | None = None,
        fixed_text: str = "",
        fail: Literal["unavailable", "timeout"] | None = None,
        backend_id: str = "fake-v1",
    ) -> None:
        self._responder = responder
        self._fixed_text = fixed_text
        self._fail = fail
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        if self._fail == "unavailable":
            raise BackendUnavailableError("fake backend is unavailable")
        if self._fail == "timeout":
            raise InferenceTimeoutError("fake backend timed out")
        text = self._responder(prompt, decoding) if self._responder else self._fixed_text
        return InferenceResponse(text=text)


class OllamaBackend:
    """Optional local Ollama backend (integration-only; lazy network import).

    Not used by offline tests or the default pipeline. It requires a running local
    Ollama daemon; failures surface as ``BackendUnavailableError`` /
    ``InferenceTimeoutError`` rather than raw exceptions.
    """

    def __init__(
        self,
        *,
        model: str,
        host: str = "http://127.0.0.1:11434",
        timeout_s: float = 60.0,
        backend_id: str = "ollama-v1",
    ) -> None:
        self._model = model
        self._host = host.rstrip("/")
        self._timeout = timeout_s
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        import json as _json
        import urllib.error
        import urllib.request

        options: dict[str, object] = {
            "temperature": decoding.temperature,
            "num_predict": decoding.max_tokens,
        }
        if decoding.stop:
            options["stop"] = list(decoding.stop)
        if decoding.seed is not None:
            options["seed"] = decoding.seed
        body = _json.dumps(
            {"model": self._model, "prompt": prompt, "stream": False, "options": options}
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - fixed localhost host, not user input
            f"{self._host}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                payload = _json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise InferenceTimeoutError(f"ollama timed out: {exc}") from exc
        except urllib.error.URLError as exc:
            raise BackendUnavailableError(f"ollama unavailable: {exc}") from exc
        return InferenceResponse(text=str(payload.get("response", "")))
