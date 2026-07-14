"""Deliberate violation fixture: an evaluation module (NOT the sanctioned
checkpoint predictor) importing the training layer. Never imported; scanned
by the AST guard self-validation tests only."""

from verifiednet.training.trainer import FakeTrainer  # noqa: F401
