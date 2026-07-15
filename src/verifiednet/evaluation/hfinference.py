"""Local HF inference backend over a VERIFIED checkpoint (Gate 11).

The ONE sanctioned lazy-ML inference site in the evaluation package, mirroring
the training-side ``verifiednet.training.hfexecutor`` precedent: torch and
transformers are imported ONLY inside function bodies, so importing this
module (and the evaluation package) succeeds without the ``training-hf``
extras, and offline CI never touches an ML runtime or a checkpoint payload.

Read-only by construction: the checkpoint is re-verified at the moment of
first load (fail-closed if any byte changed since bundle construction), the
model is put in eval mode with every parameter's gradient disabled, generation
runs under ``torch.inference_mode()``, decoding is strictly greedy
(``do_sample=False``, one beam, fixed ``max_new_tokens``), Hugging Face
offline mode is forced before any Transformers call (a cache miss is a
structured refusal, never a download), remote code is never trusted, and no
training, optimizer, scheduler, save, or weight-mutation API is referenced
anywhere in this module (statically guarded by the security tier).

Lifecycle: LAZY — nothing is loaded at construction; the model and tokenizer
load on the first ``generate`` call and are cached for the backend's lifetime
(Option B, matching the lazy-import convention). Model outputs are NOT claimed
bit-identical across platforms; the framework guarantees deterministic prompt
construction, greedy decoding parameters, and deterministic parsing — exactly
the Gate 8 determinism posture.
"""

from __future__ import annotations

import json
from typing import Any

from verifiednet.evaluation.checkpointpred import (
    CheckpointInferenceDevicePolicy,
    VerifiedInferenceBundle,
)
from verifiednet.evaluation.inference import (
    BackendUnavailableError,
    DecodingConfig,
    InferenceError,
    InferenceResponse,
)

HF_CHECKPOINT_BACKEND_ID = "hf-checkpoint-inference-v1"


class HfCheckpointInferenceBackend:
    """Greedy, CPU, float32 text generation from a VERIFIED bundle.

    Implements the Gate 8 ``InferenceBackend`` protocol so the checkpoint
    predictor plugs into the SAME evaluation boundary as every other backend.
    Construction is cheap and import-pure; all ML work is deferred.

    Gate 12 note: the backend accepts any ``VerifiedInferenceBundle`` — the
    Gate 11 verified real checkpoint or the Gate 12 verified base-model
    snapshot — so the matched base-versus-trained comparison runs through ONE
    inference stack with the weights as the only difference. There is no path
    into this backend from an unverified directory.
    """

    def __init__(
        self,
        *,
        bundle: VerifiedInferenceBundle,
        device_policy: CheckpointInferenceDevicePolicy,
        backend_id: str = HF_CHECKPOINT_BACKEND_ID,
    ) -> None:
        self._bundle = bundle
        self._policy = device_policy
        self._backend_id = backend_id
        self._model: Any = None
        self._tokenizer: Any = None
        self._config: dict[str, Any] | None = None

    @property
    def backend_id(self) -> str:
        return self._backend_id

    # -- lazy ML runtime ---------------------------------------------------

    def _import_ml(self) -> Any:
        """Force offline mode, then lazily import torch (fail-closed)."""
        import os as _os

        _os.environ["HF_HUB_OFFLINE"] = "1"
        _os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch  # type: ignore[import-not-found, unused-ignore]
        except ImportError as exc:
            raise BackendUnavailableError(
                f"torch unavailable: {exc}") from exc
        return torch

    def _ensure_loaded(self, torch: Any) -> None:
        if self._model is not None:
            return
        # Verification at the moment of use: refuse a checkpoint mutated
        # since bundle construction BEFORE interpreting any weight byte.
        self._bundle.reverify()
        try:
            from transformers import (  # type: ignore[import-not-found, unused-ignore]
                AutoModelForCausalLM,
                PreTrainedTokenizerFast,
            )
        except ImportError as exc:
            raise BackendUnavailableError(
                f"transformers unavailable: {exc}") from exc

        config = json.loads(self._bundle.config_path.read_text("utf-8"))
        architectures = config.get("architectures")
        supported = self._bundle.inference_compatibility.supported_architectures
        if (not isinstance(architectures, list) or len(architectures) != 1
                or architectures[0] not in supported):
            raise InferenceError(
                f"checkpoint architecture {architectures!r} is outside the "
                f"supported scope {supported!r}")
        try:
            tokenizer = PreTrainedTokenizerFast(  # type: ignore[no-untyped-call, unused-ignore]
                tokenizer_file=str(self._bundle.tokenizer_path))
        except Exception as exc:
            raise InferenceError(
                f"tokenizer snapshot failed to load: {exc}") from exc
        try:
            model = AutoModelForCausalLM.from_pretrained(
                str(self._bundle.weights_path.parent),
                local_files_only=True, trust_remote_code=False,
                torch_dtype=torch.float32)
        except Exception as exc:
            raise InferenceError(
                f"verified checkpoint failed to load: {exc}") from exc
        # Read-only inference: eval mode, no gradients anywhere.
        model.eval()  # type: ignore[no-untyped-call, unused-ignore]
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        self._config = config
        self._tokenizer = tokenizer
        self._model = model

    # -- generation ----------------------------------------------------------

    def generate(
        self, prompt: str, *, decoding: DecodingConfig
    ) -> InferenceResponse:
        if decoding.temperature != 0.0:
            raise InferenceError(
                "only greedy decoding (temperature 0) is supported")
        if decoding.stop:
            raise InferenceError(
                "stop sequences are not supported by the checkpoint backend")
        torch = self._import_ml()
        self._ensure_loaded(torch)
        assert self._config is not None  # narrowed by _ensure_loaded

        encoded = self._tokenizer(prompt, return_tensors="pt")
        input_length = int(encoded["input_ids"].shape[1])
        max_positions = self._config.get("max_position_embeddings")
        if (isinstance(max_positions, int)
                and input_length + decoding.max_tokens > max_positions):
            raise InferenceError(
                f"prompt of {input_length} tokens plus {decoding.max_tokens} "
                f"new tokens exceeds the model context of {max_positions}")
        raw_eos = self._config.get("eos_token_id")
        if isinstance(raw_eos, int):
            eos_ids: list[int] = [raw_eos]
        elif isinstance(raw_eos, list) and all(
                isinstance(i, int) for i in raw_eos):
            eos_ids = list(raw_eos)
        else:
            raise InferenceError(
                "model config declares no usable eos_token_id")
        try:
            with torch.inference_mode():
                output = self._model.generate(
                    input_ids=encoded["input_ids"],
                    attention_mask=encoded["attention_mask"],
                    do_sample=False, num_beams=1,
                    max_new_tokens=decoding.max_tokens,
                    eos_token_id=eos_ids, pad_token_id=eos_ids[0])
        except Exception as exc:
            raise InferenceError(
                f"checkpoint inference failed: {exc}") from exc
        # Decode ONLY the generated completion, never the echoed prompt.
        text = self._tokenizer.decode(
            output[0][input_length:], skip_special_tokens=True)
        return InferenceResponse(text=str(text))
