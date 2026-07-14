"""Supervised training-corpus layer (Gate 10A).

This package answers ONE question before any model training may exist: which
prepared examples are legally eligible for training, and exactly how do they
become model input/target pairs? It is a deterministic, immutable, DERIVED
projection of the Gate 6 prepared corpus (ADR-0022):

* train-partition, accepted-fault, accepted-diagnosis examples ONLY —
  validation, test, and abstention examples are structurally excluded;
* model input is rendered from model-visible features via an explicit,
  versioned template (allowlist construction, never dump-and-delete);
* the supervised target is canonical JSON from the authoritative accepted label;
* audit metadata stays separate and never enters model-visible text;
* evaluation and benchmark artifacts are NEVER training sources — this package
  may not import ``verifiednet.evaluation`` (AST-enforced);
* NO model training occurs here: no torch/transformers/PEFT imports, no
  optimizers, no checkpoints, no GPU or model execution of any kind.
"""

from verifiednet.training.corpus import (
    AUTHORIZED_TARGET_KEYS,
    FORBIDDEN_INPUT_TOKENS,
    SupervisedTrainingExample,
    SupervisedTrainingInput,
    SupervisedTrainingTarget,
    TrainingCorpus,
    TrainingCorpusError,
    TrainingLeakageCode,
    TrainingLeakageFinding,
    TrainingLeakageResult,
    TrainingTraceMetadata,
    audit_training_example,
    build_training_corpus,
    derive_training_corpus_id,
    derive_training_example_id,
)
from verifiednet.training.policy import (
    TRAINING_CANDIDATE_FAMILIES,
    TrainingDataPolicy,
    TrainingInputTemplate,
    TrainingTargetTemplate,
    derive_input_template_id,
    derive_target_template_id,
    derive_training_data_policy_id,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
)
from verifiednet.training.store import (
    EXPECTED_TRAINING_FILES,
    LoadedTrainingCorpus,
    TrainingCorpusManifest,
    TrainingPair,
    TrainingStoreError,
    TrainingVerificationResult,
    WrittenTrainingCorpus,
    build_training_export,
    compute_training_corpus_digest,
    load_training_corpus,
    load_training_pairs,
    verify_training_corpus,
    write_training_corpus,
)

__all__ = [
    "AUTHORIZED_TARGET_KEYS",
    "EXPECTED_TRAINING_FILES",
    "FORBIDDEN_INPUT_TOKENS",
    "TRAINING_CANDIDATE_FAMILIES",
    "LoadedTrainingCorpus",
    "SupervisedTrainingExample",
    "SupervisedTrainingInput",
    "SupervisedTrainingTarget",
    "TrainingCorpus",
    "TrainingCorpusError",
    "TrainingCorpusManifest",
    "TrainingDataPolicy",
    "TrainingInputTemplate",
    "TrainingLeakageCode",
    "TrainingLeakageFinding",
    "TrainingLeakageResult",
    "TrainingPair",
    "TrainingStoreError",
    "TrainingTargetTemplate",
    "TrainingTraceMetadata",
    "TrainingVerificationResult",
    "WrittenTrainingCorpus",
    "audit_training_example",
    "build_training_corpus",
    "build_training_export",
    "compute_training_corpus_digest",
    "derive_input_template_id",
    "derive_target_template_id",
    "derive_training_corpus_id",
    "derive_training_data_policy_id",
    "derive_training_example_id",
    "diagnosis_input_template",
    "diagnosis_target_template",
    "diagnosis_training_policy",
    "load_training_corpus",
    "load_training_pairs",
    "verify_training_corpus",
    "write_training_corpus",
]
