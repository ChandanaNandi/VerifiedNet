# Part 1 — Executive Summary

**Constellation** is a HydraNet-style multi-task computer-vision project for autonomous-driving perception, built around a shared EfficientNet-B0 backbone with an anchor-free FCOS detection head and a segmentation head, plus a rule-based "Constellation X" driving-decision engine, a FastAPI + Postgres data-labeling backend, a React/Vite frontend, and a Gradio demo. It is a genuinely competent, textbook-faithful implementation of the core ML — the FCOS head, FCOS target assigner, Kendall-Gal-Cipolla uncertainty-weighted multi-task loss, and the EfficientNet feature pyramid are all correctly coded from their source papers, and there is real training, real inference, and a real (torchmetrics-backed) mAP evaluation path.

The honest problem is the gap between the ambition of the surface area and what is actually finished and working:

- **The results are weak and partially over-stated.** The headline detection number is **mAP@50 = 4.0%** (5 epochs, 1K-image subset) — genuinely computed, but essentially non-functional as a detector. The headline segmentation number, **"84.9% IoU on Cityscapes"**, is computed with a **non-standard binary foreground-vs-background IoU** (`train_multitask.py::validate`), not per-class mIoU, and is inflated by road pixels dominating the frame.
- **Entire advertised subsystems are empty stubs.** The whole `deployment/` folder (ONNX export, INT8/FP16 quantization, benchmark, edge server) is four files each containing only `# TODO: Implement in Phase 4`. Notebook 04 (quantization) is a stub. `data_engine/shadow_mode.py` and `hard_case_miner.py` are stubs. The `/api/models` and shadow-mode `/disagreements` endpoints return hardcoded empties.
- **Celery/Redis are declared but unused.** `celery>=5.3.6` is in `backend/pyproject.toml` and Redis is in config + docker-compose, but there is **no Celery app, no tasks, no worker service, and no Redis usage anywhere in the code**. The "Celery" of the "FastAPI+Celery+Postgres" description does not exist.
- **A real correctness bug:** the backend inference service applies ImageNet mean/std normalization while training and the Gradio/video path feed raw `image/255.0`. The same checkpoint therefore receives inconsistent preprocessing across surfaces — the backend runs the model out-of-distribution relative to how it was trained.
- **A `torch.load(..., weights_only=False)` pickle-deserialization risk** remains in the inference paths after the "hardening" commit, and the "security" credentials commit is incomplete (a dev password is still hardcoded in `backend/app/config.py`).

**Networking relevance: N/A.** This is a CV/ML systems project and is thematically disconnected from the person's SONiC/networking portfolio. There is no networking content beyond ordinary REST/API plumbing. This should be stated plainly and not force-fit.

**Overall:** a solid, self-directed learning/portfolio project that demonstrates strong breadth (model, training, product backend, frontend, demo, cloud GPU workflow) and faithful reimplementation of published methods, undermined by low model quality, over-claimed metrics, and a product surface engineered far beyond what the model justifies. Strong new-grad / early-L3 signal for ML-adjacent roles; not yet a research or senior-CV artifact.

---

# Part 2 — Architecture

```
                          CONSTELLATION — SYSTEM ARCHITECTURE
                          (solid = wired & working, dashed = stub / declared-but-unused)

  ┌─────────────────────────────────────────────────────────────────────────────────┐
  │                              TRAINING PIPELINE                                     │
  │                                                                                   │
  │  data/cityscapes ──► data_engine/cityscapes_loader.py                             │
  │    (leftImg8bit +       CityscapesDataset                                         │
  │     instance masks)     - polygon/instance masks ─► bbox derivation               │
  │                         - seg mask: bg/road/sidewalk (3 cls)                       │
  │                         - image = uint8/255.0  (NO ImageNet norm)                 │
  │                                 │  collate_fn                                      │
  │                                 ▼                                                  │
  │   train_multitask.py ──►  HydraNetV2 (model/hydranet_v2.py)                        │
  │     - FCOSTargetAssigner (model/fcos_targets.py)                                   │
  │     - FCOSLoss (focal+IoU+centerness)   ┌───────────────────────────────┐         │
  │     - SegmentationLoss (CE+Dice)        │   EfficientNet-B0 backbone     │         │
  │     - MultiTaskLoss (Kendall uncert.)   │   (timm, P3/P4/P5)             │         │
  │     - AdamW + CosineAnnealingLR         │      ├─ DetectionHead (FCOS)   │         │
  │            │                            │      └─ SegmentationHead       │         │
  │            ▼                            └───────────────────────────────┘         │
  │   checkpoints/best_v2.pt  (gitignored, produced on H100/RunPod)                    │
  │                                                                                   │
  │   train.py ──► HydraNet-v1 detection-only path (BDD100K) + torchmetrics mAP        │
  │   model/hydranet.py ──► 5-head HydraNet (det/lane/drivable/depth/TL) — NEVER       │
  │                          trained or served (dead architecture)                    │
  └─────────────────────────────────────────────────────────────────────────────────┘
                                     │ checkpoints/best_v2.pt
             ┌───────────────────────┼──────────────────────────────┐
             ▼                       ▼                              ▼
  ┌────────────────────┐  ┌────────────────────────┐   ┌──────────────────────────────┐
  │  GRADIO DEMO app.py│  │ VIDEO / DECISION ENGINE │   │      PRODUCT BACKEND         │
  │  (HF Space)        │  │ video_processor.py      │   │  backend/app (FastAPI)       │
  │                    │  │ decision_engine.py      │   │                              │
  │  predict()         │  │                         │   │  main.py ─► /health, /        │
  │   HydraNetV2       │  │ VideoProcessor          │   │  api/images.py  (CRUD+upload │
  │   decode_detections│  │  - decode FCOS          │   │        + /auto-label)        │
  │   seg argmax       │  │  - NMS                  │   │  api/predictions.py          │
  │   img/255 (no norm)│  │  - DecisionEngine       │   │     /predict, /predict-upload│
  │                    │  │    rule-based:          │   │     /disagreements  ⇠ STUB   │
  │  predict_video()   │  │    danger-zone + box-h  │   │  api/models.py       ⇠ STUB  │
  │   reuses           │  │    proxy → MAINTAIN/    │   │  services/inference.py       │
  │   VideoProcessor   │  │    SLOW/STOP/CAUTION     │   │    InferenceService(singleton│
  │                    │  │  - cv2 overlay + mp4     │   │    HydraNetV2; ImageNet NORM │
  └────────────────────┘  └────────────────────────┘   │    ── ⚠ preprocessing        │
                                                        │       mismatch vs training)  │
                                                        │  services/ingestion.py       │
                                                        │  db/models.py (Image/Label/  │
                                                        │   Model/Prediction/Disagree) │
                                                        │        │ async SQLAlchemy     │
                                                        │        ▼                      │
                                                        │   Postgres 15                │
                                                        │  · · · Redis 7  (unused)     │
                                                        │  · · · Celery   (unused)     │
                                                        │  workers/__init__.py  EMPTY  │
                                                        └──────────────┬───────────────┘
                                                                       │ REST /api/*
                                                        ┌──────────────▼───────────────┐
                                                        │  FRONTEND  frontend/ (React,  │
                                                        │  Vite, TS, Tailwind)          │
                                                        │  Dashboard.tsx / DataEngine   │
                                                        │  ImageGrid / ImageModal       │
                                                        │  api/client.ts (axios)        │
                                                        │  (NOT in docker-compose)      │
                                                        └───────────────────────────────┘

  data_engine/auto_labeler.py ──► YOLOv8x + MobileSAM (off-the-shelf, separate from
                                   HydraNet) — used by /auto-label, NOT the trained model.

  deployment/ (export_onnx, quantize, benchmark, server) ── ALL "# TODO: Phase 4" stubs
```

**How the pieces actually connect.**

- **Training → checkpoint → all three inference surfaces:** `train_multitask.py` produces `checkpoints/best_v2.pt`; `app.py`, `video_processor.py`, and `backend/app/services/inference.py` all instantiate `HydraNetV2(num_det_classes=8, num_seg_classes=3, pretrained_backbone=False)` and `load_state_dict`. This linkage is real. Checkpoints are gitignored, so the Gradio app raises `FileNotFoundError` without one and the backend prints "Running with random weights…".
- **Backend ↔ model is genuinely wired** (not decorative): `predictions.py::run_prediction` fetches an `Image` row, opens the file, and calls `inference_service.predict()`. But the service normalizes with ImageNet stats — inconsistent with training (`/255` only) and with the Gradio path — so backend predictions are systematically off.
- **Frontend ↔ backend:** `frontend/src/api/client.ts` calls `/api/images`, `/api/predictions/predict`, `/api/images/{id}/auto-label`. These endpoints exist and are functional (CRUD, upload, auto-label via YOLO+SAM). `/api/models` and `/api/predictions/disagreements` are stubs. The frontend is **not** in `docker-compose.yml` (only postgres, redis, backend are).
- **Decision engine ↔ model:** `video_processor.py` decodes FCOS outputs → `DecisionEngine.analyze()` → annotated MP4. Real and self-contained; the "autonomy" is heuristic (see Part 6).
- **Celery/Redis:** declared, never instantiated. No async pipeline exists; all inference is synchronous inside request handlers.

---

# Part 3 — Repository Structure

- **`model/`** — owns the architecture. `hydranet_v2.py` (the trained/served 2-task model: detection + segmentation), `hydranet.py` (a separate 5-task model — det/lane/drivable/depth/traffic-light — that is never trained or served, i.e. dead code), `backbones/efficientnet.py` (timm EfficientNet-B0, features-only P3/P4/P5), `heads/` (`detection_head.py` FCOS `ScaleHead`, `segmentation_head.py` U-Net-style transposed-conv decoder, `depth_head.py`, `traffic_light_head.py` — the latter two only used by the unused 5-task model), `losses/multi_task_loss.py` (`MultiTaskLoss`, `DetectionLoss`, `SegmentationLoss`), `fcos_targets.py` (`FCOSTargetAssigner`). `model/train.py` and `model/inference.py` are 7-line `# TODO` stubs.
- **`train_multitask.py`** — the real multi-task trainer (Cityscapes, det+seg, uncertainty weighting, backbone freeze→unfreeze at epoch 5, checkpointing). **`train.py`** — the detection-only BDD100K trainer that actually computes mAP via `torchmetrics.detection.MeanAveragePrecision`.
- **`decision_engine.py` / `video_processor.py`** — rule-based driving decisions + FCOS decode/NMS + OpenCV video annotation. Real, runnable.
- **`app.py`** — Gradio demo (image tab + video tab), loads `HydraNetV2` and reuses `VideoProcessor`.
- **`inference.py`, `inference_multitask.py`, `backend/inference_multitask.py`** — standalone visualization/inference scripts (duplicate NMS/decode logic across ≥4 files).
- **`data_engine/`** — `cityscapes_loader.py` (dataset, bbox-from-instance-mask, 3-class drivable seg), `data_loader.py` (BDD100K), `auto_labeler.py` (YOLOv8x + MobileSAM, off-the-shelf, real), `augmentations.py`, plus **stubs** `shadow_mode.py`, `hard_case_miner.py`.
- **`backend/app/`** — FastAPI product surface. `main.py` (app + CORS + health), `api/` (images CRUD/upload/auto-label = real; predictions = real; models + disagreements = stubs), `services/inference.py` (singleton model wrapper), `services/ingestion.py` (BDD100K → DB), `db/` (SQLAlchemy async models: Image/Label/Model/Prediction/Disagreement), `workers/__init__.py` (**empty**), `alembic/` (migrations scaffold), `tests/` (2 tests: health + root only).
- **`frontend/`** — real React 18 + Vite + TS + Tailwind app (Dashboard, DataEngine, ImageGrid, ImageModal), axios client, nginx.conf, Dockerfile.
- **`deployment/`** — **entirely stubs** (`export_onnx.py`, `quantize.py`, `benchmark.py`, `server.py` all `# TODO: Implement in Phase 4/5`).
- **`notebooks/`** — 01 data exploration, 02 model training, 03 shadow mode, 04 quantization (**stub**).
- **`docs/`** — architecture/training/deployment/data_engine markdown. **`scripts/`** — data download + ingest. **`output/`** — committed sample visualizations + multi-city GIFs. **`checkpoints/`** — gitignored (no weights in repo).

---

# Part 4 — Complete Execution Flow

**A) `python train_multitask.py --epochs 15 --batch-size 16 --device cuda`**
1. `main()` parses args, resolves device (`cuda`/`mps`/`cpu`), builds `CityscapesDataset(split='train'|'val', image_size=(512,1024))` from `data_engine/cityscapes_loader.py`. `__getitem__` loads `leftImg8bit`, derives per-instance bboxes from the instance mask (`instance_id = class_id*1000 + n`), builds a 3-class seg mask (bg/road/sidewalk), returns `image = tensor/255.0` (no ImageNet norm), boxes (normalized xyxy), labels, seg mask.
2. `DataLoader` with `CityscapesDataset.collate_fn` (variable-length boxes → lists). `HydraNetV2(...)` builds `EfficientNetBackbone` (timm, `features_only`, out_indices [2,3,4] → P3=40, P4=112, P5=320 ch) + `DetectionHead` + `SegmentationHead`; backbone frozen.
3. Losses: `FCOSLoss(num_classes=8)`, `SegmentationLoss(dice_weight=0.5)`, `MultiTaskLoss(['detection','segmentation'])` (learnable `log_vars`), `FCOSTargetAssigner(num_classes=8)`. `AdamW` + `CosineAnnealingLR`.
4. **Per batch** (`train_one_epoch`): move images/boxes/labels/seg to device → `target_assigner.assign_targets_batch(boxes, labels, (512,1024))` generates per-scale `{cls, reg, centerness}` targets (auto-detecting device from boxes — this is the CUDA-fix codepath) → targets `.to(device)` → `optimizer.zero_grad()` → `outputs = model(images)` (backbone → detection head over P3/P4/P5, seg head over P3, upsampled 8× to 512×1024) → `det_loss = FCOSLoss(outputs, det_targets)` (focal cls, IoU box on positive mask, BCE centerness, averaged over 3 scales) → `seg_loss = SegmentationLoss(seg_logits, seg_masks)` (CE + 0.5·Dice) → `total, weighted = MultiTaskLoss({'detection','segmentation'})` (Kendall: `0.5·exp(-log_var)·L + 0.5·log_var`) → `total.backward()` → `clip_grad_norm_(…, 10.0)` → `optimizer.step()`.
5. Epoch 5 → `model.unfreeze_backbone()`. `validate()` computes losses + the **binary foreground IoU** `((pred==gt)&(gt>0)).sum() / ((pred>0)|(gt>0)).sum()`. Checkpoints saved to `latest_v2.pt` / `best_v2.pt`. (Detection **mAP is NOT computed here** — only `train.py` computes mAP.)

**B) `python app.py` (Gradio demo)**
1. Module load: `resolve_checkpoint_path()` searches `checkpoints/best_v2.pt|latest_v2.pt|best.pt|latest.pt`; raises `FileNotFoundError` if none. Instantiates `HydraNetV2(pretrained_backbone=False)`, `load_state_dict(checkpoint['model_state_dict'])`, `.eval()`. Also builds one shared `VideoProcessor`.
2. **Image tab → `predict(image, thr)`:** resize to 1024×512 → `tensor/255.0` (no norm) → `model(tensor)` → `decode_detections` (per-scale sigmoid(cls)·sigmoid(centerness), threshold, distance-to-xyxy via stride, `simple_nms` iou 0.5, top-50) → seg `argmax` → road/sidewalk % → PIL overlay + markdown summary.
3. **Video tab → `predict_video(path, thr)`:** `cv2.VideoCapture`, up to 300 frames, `VideoProcessor.process_frame` per frame → decode + `DecisionEngine.analyze` → annotated MP4 + decision histogram. Launches on `0.0.0.0:7860`.

**C) Backend (`docker compose up` → uvicorn `app.main:app`)**
1. Postgres 15 + Redis 7 + backend containers start (frontend and celery **absent** from compose). `get_settings()` reads env; `lifespan` prints startup; CORS from settings; `api_router` mounted at `/api`.
2. **Ingest:** `scripts/ingest_data.py` / `services/ingestion.py::ingest_bdd100k` walks images, reads labels JSON, inserts `Image`/`Label` rows in batches.
3. **`POST /api/predictions/predict`** (`predictions.py::run_prediction`): `db.get(Image, image_id)` → open file → `inference_service.predict(pil, thr)`. `InferenceService` (singleton) lazy-loads `HydraNetV2`, `_resolve_checkpoint_path()` across `cwd`/`backend`/`/app`; if none, **random weights**. `preprocess_image` resizes then **applies ImageNet mean/std** (⚠ mismatch with training/Gradio) → `model(tensor)` → `decode_detections` + `get_segmentation` → `PredictionResponse`.
4. **`POST /api/images/{id}/auto-label`:** lazy-imports `data_engine.auto_labeler.AutoLabeler` (YOLOv8x + MobileSAM, downloads weights), labels the image, stores `Label(source=AUTO_COMBINED)`, flips status to `LABELED`. **Uses off-the-shelf models, not the trained HydraNet.**
5. **`GET /api/models`, `GET /api/predictions/disagreements`** return hardcoded stubs. Frontend (`api/client.ts`) drives images grid/detail/auto-label against these endpoints.

---

# Part 5 — Networking Concepts

**Essentially N/A.** This is a computer-vision / ML-systems project and is thematically disconnected from the person's SONiC/networking portfolio; there is no networking-domain content (no routing, switching, protocols, packet processing, telemetry, or SONiC/SAI touch-points). The only "networking-adjacent" material is ordinary application plumbing:

- **REST API design** (`backend/app/api/*`): reasonable resource modeling (images/labels/models/predictions), pagination, `UploadFile` handling, Pydantic request/response schemas, CORS middleware.
- **Client-server split**: React SPA ↔ FastAPI over HTTP/JSON (axios), with a dev/prod base-URL switch and an nginx reverse-proxy config for the built frontend.
- **Container networking**: `docker-compose.yml` service-name DNS (`postgres`, `redis`), port mappings, healthchecks, `depends_on`.
- **Distributed training**: only a documented manual workflow (RunPod H100, `runpodctl` transfer, tmux, W&B) — no in-repo DDP/multi-GPU/`torch.distributed` code.

There is no meaningful networking engineering here, and the audit should not manufacture one. For this person's portfolio, this repo demonstrates a **different** skill axis (CV/ML) from the networking work.

---

# Part 6 — AI Concepts

Implemented and real:
- **Shared-backbone multi-task learning** — `HydraNetV2` (det+seg) and `HydraNet` (5 heads). One backbone, multiple heads, single forward pass. Correct pattern; only the 2-head variant is actually trained/served.
- **Transfer learning** — timm EfficientNet-B0 ImageNet weights (`model/backbones/efficientnet.py`), freeze-then-unfreeze schedule (`train_multitask.py`, unfreeze at epoch 5).
- **Anchor-free detection (FCOS)** — `heads/detection_head.py` (shared cls/reg towers with GroupNorm, per-location l/t/r/b regression, centerness branch, learnable per-scale `scale`, focal-loss bias init `-4.6`) and `fcos_targets.py` (in-box assignment, smallest-box tie-break via centerness, per-scale size ranges [0–32]/[32–64]/[64–∞]). Faithful to Tian et al. 2019.
- **Uncertainty-weighted multi-task loss** — `losses/multi_task_loss.py::MultiTaskLoss` implements Kendall-Gal-Cipolla (CVPR 2018): `0.5·exp(-log_var)·L + 0.5·log_var`, learnable `log_vars` per task, numerically-stable `exp(-log_var)`. Correct.
- **Detection loss** — sigmoid focal loss (α=0.25, γ=2), IoU loss on positives, BCE centerness (`FCOSLoss` in `train_multitask.py`; `DetectionLoss` in the losses module).
- **Segmentation** — U-Net-ish transposed-conv decoder (`segmentation_head.py`), CE + Dice loss (`SegmentationLoss`).
- **mAP evaluation** — **only** in `train.py::validate` via `torchmetrics.detection.MeanAveragePrecision` (map/map_50/map_75). This is a legitimate metric path; the 4% figure comes from it.
- **Auto-labeling** — `data_engine/auto_labeler.py`: YOLOv8x detection + MobileSAM segmentation with COCO-format export (real, but off-the-shelf, unrelated to the trained model).
- **Decision engine** — `decision_engine.py`: rule-based hazard logic (center "danger zone", box-height-as-distance proxy, vulnerable-vs-vehicle priority → STOP/SLOW/CAUTION/MAINTAIN). Deterministic heuristics, not learned; no tracking, no temporal smoothing, no calibration, box height is a crude range proxy.

Claimed/scaffolded but **not** implemented:
- **Depth estimation** — `heads/depth_head.py` exists (sigmoid×max_depth), but only wired into the unused 5-task `HydraNet`; **never trained, served, or evaluated**; README's "knowledge distillation from Depth Anything/MiDaS" has no supporting code.
- **Traffic-light classification** — head exists, same dead-code status.
- **Quantization / ONNX export / INT8-FP16** — **stubs only** (`deployment/*`, notebook 04). No `torch.onnx.export`, no quantization anywhere.
- **Active learning / shadow mode / hard-case mining** — DB schema (`Disagreement`) and stubs exist; **no implementation** (`data_engine/shadow_mode.py`, `hard_case_miner.py`, `/disagreements` all empty).
- **GIoU** — README claims "GIoU loss"; code uses **plain IoU loss** (`train.py`, `train_multitask.py`). False claim.

---

# Part 7 — Software Engineering

**Strengths.** Clean, consistent package layout (`model/`, `data_engine/`, `backend/app/`, `frontend/`, `deployment/`, `docs/`). Good use of type hints, dataclasses/`NamedTuple` (`DetectionOutput`, `Detection`, `Decision`), and docstrings with paper citations. Sensible abstraction boundaries (backbone/head/loss separation; `InferenceService` singleton; `DecisionEngine` decoupled from IO). Modern stack: PyTorch 2, timm, FastAPI, async SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Pydantic-settings, Alembic scaffold, React 18 + Vite + TS + Tailwind, Docker + docker-compose with healthchecks and `depends_on` conditions. Pydantic request/response models give the API a real contract. Every core module ships an in-file `test_*()` sanity harness with shape assertions.

**Weaknesses.**
- **Duplication / DRY violations:** the FCOS decode + `simple_nms` logic is copy-pasted across `app.py`, `video_processor.py`, `inference_multitask.py`, `backend/app/services/inference.py`, and `inference.py` (≥4–5 near-identical copies). Should be one shared decoder.
- **Two divergent model definitions** (`hydranet.py` vs `hydranet_v2.py`) with overlapping heads and no shared base; the v1/5-task model is dead weight.
- **Config drift / preprocessing inconsistency:** ImageNet norm in the backend service vs `/255`-only in training and Gradio — a latent correctness bug (Part 10).
- **Testing is near-absent:** only `backend/tests/test_health.py` (health + root) runs under pytest. No unit tests for FCOS assignment, losses, decode/NMS, or the decision engine — the `test_*()` functions are `__main__` scripts, not collected by a runner and not in CI.
- **Logging:** `print()` throughout (including emoji), no `logging` module, no structured logs, no request logging.
- **Error handling:** backend wraps inference in broad `except Exception` → 500 with raw `str(e)` (leaks internals); many scripts assume happy paths.
- **Reproducibility gaps:** no global seed setting, no pinned exact versions (`>=` ranges), no committed checkpoint (gitignored), no `conftest`/CI workflow, metrics reported only in README prose (no results JSON/CSV committed). "Reproduce 84.9% IoU" is not runnable from the repo alone.
- **Dependency mgmt:** split across `requirements.txt`, `backend/pyproject.toml`, `data_engine/pyproject.toml`, `frontend/package.json` with some unused deps (celery).
- **Docker:** no frontend or worker service in compose; `--reload` in the "production" backend command; `changeme` default password fallback.

Overall code quality is **above average for a solo portfolio project** but carries meaningful technical debt and thin test coverage.

---

# Part 8 — Research Quality

If submitted to a CV venue (CVPR/NeurIPS/ICCV), this would be **desk-rejected as a research contribution** — and it does not claim to be one — but assessed as a project the reviewer critique is instructive:

**What reviewers would credit:** correct, from-scratch reimplementation of FCOS + Kendall uncertainty weighting on a shared EfficientNet backbone; a clean multi-task setup on Cityscapes; end-to-end pipeline including a downstream decision module and demos.

**What reviewers would criticize (severely):**
- **The result is non-functional.** mAP@50 = 4.0% is not a working detector; on Cityscapes/BDD detection a credible FCOS baseline is tens of points of mAP. There is no evidence the detector localizes anything reliably.
- **Confounded/under-specified training.** 5 epochs on a 1K-image subset with a frozen backbone is nowhere near convergence; the README itself says full-scale training was "deprioritized." So the number reflects an unfinished run, not a design result.
- **No baselines, no ablations, no statistics.** No comparison to an off-the-shelf FCOS/RetinaNet/YOLO, no ablation of uncertainty weighting vs fixed weights, no seeds/variance/CIs, no per-class breakdown.
- **Metric rigor problems.** The "84.9% IoU" is a **non-standard binary foreground IoU** (road+sidewalk vs background), not per-class mIoU, and is dominated by the large road region — it substantially overstates segmentation quality. Detection and segmentation are also reported on **different datasets** (BDD subset vs Cityscapes) and **different epochs**, so there is no single coherent evaluation.
- **Dataset rigor.** Cityscapes detection boxes are derived from instance masks (reasonable) but with a hard 10-px filter and no crowd/ignore handling; no documented train/val discipline beyond `Subset`.
- **Depth/traffic-light/quantization claims** are architecture-only with no experiments.

As a *systems/engineering* portfolio piece it is respectable; as *research* it lacks the experimental scaffolding (baselines, ablations, proper metrics, significance) that any reviewer requires.

---

# Part 9 — Hiring Committee Review

**Would it impress NVIDIA AV/CV teams?** Partially, and mostly on breadth rather than depth. It shows the candidate can (a) read papers (FCOS, Kendall) and translate them into correct PyTorch, (b) stand up a full training loop on cloud H100, (c) build a product surface (FastAPI + Postgres + React + Gradio) around a model, and (d) debug real GPU issues (the CUDA device-mismatch fix in `fcos_targets.py` is a genuine, plausible H100 bug). An NVIDIA CV interviewer would immediately probe the **4% mAP** and the **inflated IoU metric**, and would want to see the detector actually work — the candidate must be ready to own those numbers.

**General ML-infra committees** would value the systems breadth and the clean abstractions, but note the unused Celery/Redis, stubbed deployment, thin tests, and preprocessing bug as signs of "surface built ahead of substance."

**Skills demonstrated:** PyTorch modeling, multi-task learning, anchor-free detection, transfer learning, dataset engineering, FastAPI/async SQLAlchemy, React/TS, Docker, and a real cloud-GPU workflow (RunPod/W&B/tmux).

**Level read:** **strong new-grad / early L3 (SDE-2 / MLE-I)** for a CV/ML product or ML-infra role. The breadth and independent execution exceed typical new-grad work, but the low model quality, over-claimed metrics, missing tests, and stubbed subsystems keep it below mid/senior. It is **not** senior/staff evidence: no rigorous evaluation, no baselines, no production hardening, no scale. Honest framing: this is a **different skillset** from the networking/SONiC repos — it argues for CV/ML versatility, not for depth in the networking track, and should be presented as such.

---

# Part 10 — Weaknesses (brutally honest)

1. **mAP@50 = 4% — the detector effectively does not work.** Root causes are compounding: only 5 epochs; 1K-image subset; frozen backbone for the reported run; a from-scratch FCOS head initialized to near-zero outputs; IoU (not GIoU) box loss with the FCOS positive-only regression on a tiny dataset; and Cityscapes boxes derived from instance masks with aggressive filtering. This is an under-trained proof-of-plumbing, not a functioning model, and the README's "validate the architecture" framing is the honest read.
2. **Over-claimed / non-standard metrics.** "84.9% IoU on Cityscapes" is a **binary foreground IoU** (`train_multitask.py::validate`), not per-class mIoU, and is inflated by road-pixel dominance. Detection and segmentation numbers come from different datasets/epochs. "GIoU loss" is claimed but plain IoU is used.
3. **Product surface massively over-engineered relative to model quality.** Postgres schema (Image/Label/Model/Prediction/Disagreement), async SQLAlchemy, Alembic, a React data-labeling app, shadow-mode DB tables — all built around a 4%-mAP model, with the most product-differentiating pieces (shadow mode, disagreements, model registry) left as stubs.
4. **Fake/placeholder components:** `deployment/{export_onnx,quantize,benchmark,server}.py`, notebook 04, `data_engine/{shadow_mode,hard_case_miner}.py`, `model/{train,inference}.py`, and `/api/models` + `/disagreements` endpoints are all stubs. The README markets ONNX/quantization/edge and shadow-mode/active-learning as if present.
5. **Celery/Redis are vaporware in this repo.** `celery>=5.3.6` is a dependency; there is **no** Celery app, task, worker, or Redis call. The "FastAPI+Celery+Postgres" description overstates the backend — it is synchronous FastAPI + Postgres.
6. **Correctness bug — preprocessing mismatch.** `backend/app/services/inference.py` normalizes with ImageNet mean/std; training (`cityscapes_loader`) and `app.py`/`video_processor.py` use raw `/255`. The backend runs the trained checkpoint out-of-distribution, so its predictions differ from (and are worse than) the demo's. Silent, untested.
7. **Security issues in/after the "hardening" commits.**
   - `torch.load(..., weights_only=False)` in `video_processor.py` (and default `weights_only` in the inference service / `inference_multitask.py`) — pickle deserialization RCE risk on untrusted checkpoints, exactly what the "harden checkpoint handling" commit should have closed.
   - The "use environment variables for DB credentials" commit only patched `docker-compose.yml` and `.env.example`; **`backend/app/config.py` still hardcodes `constellation:constellation_dev`** as the default `database_url`, and compose falls back to `changeme`.
   - `CORSMiddleware` with `allow_methods=["*"]`, `allow_headers=["*"]`, `allow_credentials=True` (origins are at least restricted).
   - `/predict-upload` and `/upload` accept files with only content-type checks; broad `except Exception` returns raw error strings (info leak).
8. **Backend loads random weights silently** when no checkpoint is found (only a `print` warning) — a production endpoint can serve a random-weight model with a 200 response.
9. **Testing/CI essentially absent** (2 endpoint tests, no CI). No seeds → non-reproducible.
10. **Scalability:** single-process synchronous inference in the request path (no queue/batching despite the Celery/Redis theater), full seg mask serialized to JSON as nested lists in `get_segmentation` (`mask.tolist()` — huge payload if ever returned), no model warmup/pooling, `--reload` in the compose command.
11. **Dead code / duplication:** unused 5-task `HydraNet` + depth/traffic-light heads; ≥4 copies of decode/NMS.

---

# Part 11 — Reusable Components

**Directly reusable (high quality, portable):**
- `model/backbones/efficientnet.py` — clean timm multi-scale extractor; drop-in for any FPN/head project.
- `model/heads/detection_head.py` + `model/fcos_targets.py` — a correct, self-contained FCOS head + target assigner; the strongest reusable ML asset.
- `model/losses/multi_task_loss.py` — `MultiTaskLoss` (Kendall), `DetectionLoss`, `SegmentationLoss` (CE+Dice) are generic and reusable.
- `model/heads/segmentation_head.py` / `depth_head.py` — generic decoder blocks.
- `decision_engine.py` — well-structured rule engine; reusable as a downstream perception→action layer (with the caveat it is heuristic).
- `backend/app/` skeleton (FastAPI + async SQLAlchemy + Pydantic settings + Alembic) and `frontend/` (React+Vite+TS+Tailwind data-viewer) are solid **templates** for future ML product surfaces.
- `data_engine/cityscapes_loader.py` / `auto_labeler.py` (YOLO+SAM) — reusable data tooling.

**Needs consolidation before reuse:**
- The 4–5 duplicated `decode_detections`/`simple_nms` implementations → extract one `postprocess.py`.
- `InferenceService` preprocessing → unify with training transforms (fix the norm mismatch) before reuse.

**Rewrite / discard:**
- All of `deployment/` (stubs) — rewrite from scratch when ONNX/quant is actually needed.
- `data_engine/shadow_mode.py`, `hard_case_miner.py`, `model/train.py`, `model/inference.py`, notebook 04 — stubs; delete or implement.
- The unused 5-task `hydranet.py` + `traffic_light_head.py` — drop or merge into a single configurable model.
- Remove `celery`/Redis from deps until actually used.

---

# Part 12 — Portfolio Positioning

**Keep it independent, but reframe and trim.** This is a legitimate, demonstrable multi-task CV project with a live HF demo — worth showcasing on its own. It should **not** be merged into or presented as part of the networking/SONiC portfolio: it is thematically orthogonal (perception ML vs. network infrastructure), and blending them dilutes both narratives.

Recommended positioning:
- **Present it as a separate "CV / ML systems" pillar**, explicitly labeled as a breadth/versatility project, distinct from the networking depth track. In a resume, one or two lines under a different heading — not intermixed with SONiC work.
- **De-emphasize the metrics; emphasize the engineering and the honest scope.** Lead with "faithful FCOS + multi-task + uncertainty-weighting implementation, full training/serving/demo pipeline on cloud H100," and state the 4% mAP as an explicitly-unfinished proof-of-architecture. Do **not** headline "84.9% IoU" without the per-class/foreground caveat — that invites a credibility hit under scrutiny.
- **Split, don't submodule.** The reusable `model/` (FCOS + losses + backbone) could become a small standalone library if the candidate wants a clean reusable artifact; the backend/frontend are better as a separate "ML product template." But there is no strong reason to make this a submodule of anything networking-related.
- **Before featuring it prominently:** fix the metric framing and the false GIoU claim, either implement or delete the `deployment/`/Celery/shadow-mode stubs (so the README matches reality), and fix the preprocessing bug. A smaller repo that is fully honest and consistent will interview far better than a large one with stubbed/over-claimed pieces.

Net: **independent, honestly-scoped CV showcase, presented separately from the networking portfolio and positioned as versatility rather than as a flagship result.**

---

# Part 13 — Interview Questions (Staff-level, specific to this repo)

1. `train_multitask.py::validate` computes seg IoU as `((pred==gt)&(gt>0)) / ((pred>0)|(gt>0))`. Why is this not per-class mIoU, and how would road-pixel dominance inflate it toward 84.9%?
2. Detection mAP is computed only in `train.py` (torchmetrics) but never in `train_multitask.py`. How would you add a correct COCO-style mAP eval to the multi-task validator, and what target assignment does the metric need vs. the loss?
3. `fcos_targets.py` assigns a box to a scale via `box_size ∈ [min,max)` using `max(w,h)` and FCOS's regression-range gating. Contrast this with canonical FCOS max-regression-target gating — which objects fall through the cracks here?
4. The tie-break in `assign_targets_single_image` keeps the higher-centerness match rather than the smallest-area box. How does this differ from FCOS's "smallest area" rule and what training pathology could it cause?
5. Walk through the exact CUDA device-mismatch bug fixed in commit `ce6609e`. Why did empty-box images specifically trigger it on H100 but not on CPU/MPS?
6. `bbox_pred = F.relu(self.bbox_pred(...)) * self.scale` with per-scale learnable `scale`. Why is the learnable scalar necessary given multi-scale strides, and how is it consumed at decode time in `decode_detections`?
7. The cls head bias is init to `-4.6 = -log((1-0.01)/0.01)`. Derive why, and what breaks in early training if you initialize it to 0.
8. `FCOSLoss.iou_loss` uses plain IoU on l/t/r/b. The README claims GIoU. Implement GIoU for the ltrb parameterization and explain the gradient behavior difference for non-overlapping boxes.
9. `MultiTaskLoss` uses `0.5·exp(-log_var)·L + 0.5·log_var`. Derive this from the Gaussian/Laplacian likelihood in Kendall et al. What does `log_var` converge to for a task with irreducible loss, and can this term drive total loss negative?
10. With only detection+segmentation and both losses O(0.1–3), what would you expect the learned task weights to be, and how would you detect the uncertainty weighting silently collapsing one task?
11. The segmentation head upsamples P3 by fixed 8× transposed convs assuming input divisible by 8. What happens for a 1080p frame that isn't 512×1024, and where would it fail?
12. Backend `InferenceService.preprocess_image` applies ImageNet mean/std; training and `app.py` use raw `/255`. Quantify the expected impact on the same checkpoint and design a test that would have caught this.
13. `video_processor.py` uses `torch.load(..., weights_only=False)`. Explain the RCE surface and how you'd migrate all load paths to `weights_only=True` given the checkpoint stores optimizer/scheduler state and a `ParameterDict`.
14. The decision engine uses box height > 15% of image height as a "close" proxy. Derive the pinhole-camera relationship between object height in pixels and metric distance, and why this proxy fails for trucks vs. pedestrians.
15. `DecisionEngine.analyze` has no temporal state. Design frame-to-frame smoothing/hysteresis to prevent STOP/GO flicker, and where in `process_video` it plugs in.
16. `detections_from_model_output` indexes `CLASS_NAMES[det['class']]` with an 8-class list. What guarantees class-id alignment between the model's 8 detection classes, the loader's `DETECTION_ID_MAP`, and this list — and what fails if they drift?
17. `simple_nms` is class-agnostic across all scales. Why is per-class NMS usually required, and what artifact appears when a car and a truck overlap?
18. FCOS scores are `sigmoid(cls)·sigmoid(centerness)`. Why multiply centerness in at inference, and how does omitting it change the precision/recall tradeoff at a fixed threshold?
19. `decode_detections` clamps ltrb-derived corners to [0,W]/[0,H] but doesn't filter degenerate/zero-area boxes before NMS. What downstream bug can a zero-area box cause in the IoU computation?
20. The backend serializes the full seg mask via `mask.tolist()` in `get_segmentation` (though the API returns only percentages). What's the payload cost at 512×1024 and how would you return masks efficiently (RLE/PNG)?
21. `InferenceService` is a `__new__`-based singleton with class-level `_model`. What concurrency hazards arise under uvicorn workers / async handlers, and how would you make model loading thread-safe?
22. The `/predict` handler is synchronous CPU inference inside an async FastAPI route. Explain event-loop blocking and how you'd offload (threadpool, or the Celery/Redis that's declared but unused).
23. Design the missing Celery pipeline for `/auto-label` (YOLO+SAM is heavy): task granularity, result storage, idempotency, and how the frontend polls status. Why is the current synchronous design a problem at scale?
24. `models.py` and `/disagreements` are stubs. Implement shadow mode end-to-end using the existing `Prediction`/`Disagreement` tables: what defines a "disagreement" for detection vs. segmentation, and how do you score severity?
25. The `Disagreement` table stores `severity: float`. Propose a principled severity metric that combines missed/extra/mislabeled detections and class importance (pedestrian vs. car).
26. `ingest_bdd100k` commits in batches but adds labels tied to un-flushed `Image` objects via relationship. Explain the SQLAlchemy identity/flush ordering that makes this work, and the failure mode if an image errors mid-batch.
27. There are two model classes (`HydraNet` 5-task, `HydraNetV2` 2-task) sharing head code. Design a single config-driven multi-task model that supports arbitrary head subsets without the duplication.
28. Depth head outputs `sigmoid()*max_depth`. What supervision/loss (scale-invariant? distillation from MiDaS?) would you need, and why does naive L1 on metric depth fail for monocular?
29. `assign_targets_batch` runs a Python loop over images and a nested loop over boxes on-device. Profile the bottleneck and vectorize the assignment to be GPU-efficient for batch 16 at 512×1024.
30. Backbone is frozen for 5 epochs then unfrozen, but the optimizer/LR schedule (`CosineAnnealingLR(T_max=epochs)`) is created once. What's wrong with the LR when suddenly training 4M more params at epoch 5, and how would you fix it (param groups / discriminative LR)?
31. `clip_grad_norm_(…, 10.0)` — how did you pick 10, and how would you diagnose whether detection or segmentation gradients dominate under uncertainty weighting?
32. The seg mask has 3 classes (bg/road/sidewalk) derived in `cityscapes_loader._create_seg_mask`. How are Cityscapes' 30+ labels collapsed, and what information loss affects the decision engine's road-percentage logic?
33. Cityscapes detection boxes come from instance masks with a `mask.sum()<10` filter. What biases does this inject (small/occluded objects) and how does it interact with the FCOS size-range gating?
34. There's no seeding anywhere. Enumerate every nondeterminism source in this training loop (dataloader workers, cudnn, dropout-free but BN, init) and make a run bit-reproducible.
35. `app.py` loads the model at import time and builds a second `VideoProcessor` with its own copy. What's the memory/latency cost on a free HF Space, and how would you share one model instance?
36. The frontend `api/client.ts` switches base URL on `import.meta.env.PROD` and calls `/api/...`. Trace how nginx (`frontend/nginx.conf`) must proxy to the backend, and what CORS config is actually needed in prod vs. the current `["*"]` methods.
37. Commit `cb90f1f` "accept JSON body in predictions endpoint" and `3b5f6cd` "API path mismatch." Given the router mounts predictions at `/api/predictions` with a `/predict` route, what was the exact mismatch and how do you prevent path drift (typed client generation)?
38. `run_prediction` reads the image path from the DB and opens it directly. What path-traversal / SSRF-like risks exist if `file_path` is attacker-influenced, and how do you sandbox file access?
39. The "security" commit left `database_url` default hardcoded in `config.py` and `changeme` in compose. Design a secrets story (env, Docker secrets, or a vault) that removes all plaintext defaults without breaking local dev.
40. `MeanAveragePrecision` is only computed every 5 epochs "to save time." What's the cost model of torchmetrics mAP, and how would you make per-epoch mAP cheap (subsample val, cache targets)?
41. Given mAP@50 = 4%, design a controlled debugging protocol (overfit-one-batch, check target assignment visually, verify decode inverts encode) to localize whether the bug is in targets, loss, or decode.
42. The FCOS regression targets are in pixels but boxes arrive normalized (`*W`, `*H`) at (512,1024). If someone trains at a different `image_size`, where does the target/decode stride assumption silently break?
43. `SegmentationLoss` computes Dice via softmax + one-hot over all classes including background. How does including background inflate Dice, and would you exclude it?
44. There is no NMS score-threshold sweep or PR-curve tooling. Build an evaluation harness that reports mAP + PR curves + per-class AP and commits results as JSON for reproducibility.
45. The model is served three ways (Gradio, video, FastAPI) with three slightly different decoders. Propose a single serving abstraction and how you'd contract-test all three against it.
46. `HydraNetV2.forward` always runs both heads; `forward_detection_only`/`forward_segmentation_only` exist but aren't used in serving. When would conditional head execution matter for latency, and how do you keep BN/GroupNorm stats consistent?
47. If you had to hit 30 FPS on a Jetson Orin, walk the full path: which of the (stubbed) `deployment/` steps you'd implement first, expected INT8 accuracy drop for this FCOS+seg model, and where TensorRT would help vs. hurt.
48. The repo mixes `requirements.txt` and three `pyproject.toml`s. Design a single reproducible dependency + lockfile strategy across model/backend/frontend, including the CUDA/timm/torch pinning.
49. Given the `Prediction` table stores `latency_ms`, design an online monitoring story (drift, per-class recall, latency SLOs) for this model in shadow mode — which tables/queries and what alerts.
50. Make the staff-level call: with 4% mAP and a non-standard IoU metric, would you ship the perception stack behind the decision engine to *any* real vehicle? Justify with specific failure modes from `decision_engine.py` and the detector.

---

# Part 14 — Overall Score

| Dimension | Score | One-line justification |
|---|---:|---|
| Architecture | 7/10 | Clean shared-backbone multi-task design with faithful FCOS + heads; marred by dead 5-task variant, decode duplication, and preprocessing drift. |
| Networking | N/A | CV project; no networking-domain content beyond ordinary REST/Docker plumbing — thematically disconnected from the SONiC portfolio. |
| AI | 6/10 | Correct FCOS, uncertainty weighting, transfer learning, real mAP path; but 4% mAP, inflated non-standard IoU, and depth/quant/GIoU only claimed. |
| Systems Design | 6/10 | Full FastAPI+Postgres+React+Gradio surface and cloud-GPU workflow; undercut by unused Celery/Redis, synchronous inference, and stubs. |
| Code Quality | 6/10 | Typed, documented, well-organized; but heavy duplication, print-logging, broad excepts, and near-zero tests. |
| Research | 3/10 | No baselines/ablations/statistics, confounded under-trained results, metrics on mismatched datasets/epochs — not research-grade. |
| Reproducibility | 4/10 | Runnable scripts and documented commands, but no seeds, no committed weights/results, `>=` pins, no CI. |
| Open Source Quality | 5/10 | Polished README + live demo + license; but README over-claims vs. stubbed reality, hurting trust. |
| Portfolio Value | 6/10 | Demonstrable multi-task CV project with a live demo showing genuine breadth; value is versatility, not a headline result. |
| Resume Value | 6/10 | Strong "can build the whole stack" signal for CV/ML roles; needs honest metric framing to survive scrutiny. |
| Hiring Impact | 6/10 | Solid new-grad / early-L3 CV/MLE signal; not senior/staff evidence; a different, complementary axis to the networking work. |
