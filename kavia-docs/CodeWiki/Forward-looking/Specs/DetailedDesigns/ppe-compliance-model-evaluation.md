[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — Model Evaluation and Licensing Trail

## Purpose

This record satisfies the Phase 5 roadmap's model-selection, licensing, and evaluation
requirements: a licence trail for every candidate model, a documented benchmark (not a single
headline accuracy figure), and the production route the POC does not yet ship.

## Candidate registry

| Candidate | Role | Framework | Weights licence | Training data | Access |
| --- | --- | --- | --- | --- | --- |
| `melihuzunoglu/ppe-detection` | **Primary POC adapter** (configured default) | Ultralytics YOLOv11 | `agpl-3.0`, explicitly declared in the model card's YAML frontmatter | Declared only as `custom` — no further provenance published | [huggingface.co/melihuzunoglu/ppe-detection](https://huggingface.co/melihuzunoglu/ppe-detection) |
| `Hansung-Cho/yolov8-ppe-detection` | Cross-check candidate | Ultralytics YOLOv8n | Model card declares `mit` — but see **Licence conflict** below | Declared only as "public PPE / construction datasets (Kaggle etc.)"; card states "licence follows the data provider," i.e. unverified | [huggingface.co/Hansung-Cho/yolov8-ppe-detection](https://huggingface.co/Hansung-Cho/yolov8-ppe-detection) |
| Roboflow Construction Site Safety (hosted API) | Cross-check candidate | Roboflow-hosted | Roboflow Universe project terms — not yet reviewed | Roboflow Universe project — not yet reviewed | **Not yet benchmarked.** Calling the hosted inference API requires a Roboflow account/API key this environment does not have. The provider-neutral adapter interface (`app/detection.py`) is ready to accept a Roboflow-backed `DetectionProvider` implementation once credentials are available; the swap requires no change to `decision.py`, `processing.py`, or the API surface. |

### Licence conflict on `Hansung-Cho/yolov8-ppe-detection`

The model card's YAML frontmatter declares `license: mit`. However, it is a YOLOv8n
fine-tune built with the Ultralytics framework, and Ultralytics states its own AGPL-3.0
licence follows models trained with its code, not only the framework itself (this is the
same caveat the implementation plan already raises for the primary candidate). The
author's MIT tag and Ultralytics' AGPL position on trained weights are in unresolved
tension. **Treat this candidate as AGPL-3.0-encumbered until the author or Ultralytics
confirms otherwise** — do not rely on the self-declared MIT tag alone before any
commercial use.

### What "verified" means here

Per the roadmap's instruction to check code, weights, and data licences separately: the
**framework** licence (Ultralytics, AGPL-3.0) is confirmed for both HF candidates. The
**weights** licence is confirmed for `melihuzunoglu` (AGPL-3.0, explicit) and disputed for
`Hansung-Cho` (see above). The **training data** licence is **unverified for both** — neither
model card publishes a checked, named, licensed source dataset. This alone is sufficient
reason neither candidate is production-cleared, independent of their measured accuracy below.

## Benchmark methodology

- **Dataset:** [`keremberke/hard-hat-detection`](https://huggingface.co/datasets/keremberke/hard-hat-detection) (Hugging Face Datasets — a Roboflow Universe export, COCO-format annotations, `roboflow2huggingface` pipeline). Its `test` split (2,001 images) was used as the held-out set; none of it is used anywhere else in this repository.
- **Ground truth scope:** this dataset labels only two classes — `hardhat` → `helmet`, `no-hardhat` → `no_helmet`. It does **not** label `vest`, `person`, `gloves`, or `glasses`, so this benchmark validates helmet/no_helmet accuracy only. Vest and secondary-PPE accuracy remain unvalidated pending a labeled set that covers them (SH17 is the plan's own reference for that coverage, but is CC BY-NC-SA — benchmarking-only, never for shippable training).
- **Sampling:** 300 of the 1,974 test images that have at least one relevant ground-truth box, drawn with a fixed random seed (`20260909`) so both candidates were scored against the *identical* image set.
- **Matching:** IoU ≥ 0.5, one ground-truth box may match at most one prediction of the same label; a prediction below the provider's `conf=0.25` threshold was never produced by Ultralytics' own filtering.
- **Environment:** CPU-only inference (Apple Silicon, no GPU), one image per predict call — latency numbers reflect that, not a batched/GPU deployment.
- **Reproduce it:** the benchmark script and raw output are attached to this evaluation; rerun with `python run_benchmark.py --repo-id <candidate>`.

## Results (2026-09-09)

### `melihuzunoglu/ppe-detection` (primary)

| Class | TP | FP | FN | Precision | Recall | F1 | Mean TP confidence | Mean FP confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| helmet | 271 | 221 | 401 | 0.551 | 0.403 | 0.466 | 0.715 | 0.635 |
| no_helmet | 78 | 177 | 70 | 0.306 | 0.527 | 0.387 | 0.743 | 0.572 |

Latency: mean 70.1 ms/image, range 56.5–1260.2 ms (CPU). Unmapped-label detections: 0 (this
model's four classes all map cleanly to the application's label set). Total raw detections: 1,587.

### `Hansung-Cho/yolov8-ppe-detection` (cross-check)

| Class | TP | FP | FN | Precision | Recall | F1 | Mean TP confidence | Mean FP confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| helmet | 379 | 271 | 293 | 0.583 | 0.564 | 0.573 | 0.795 | 0.654 |
| no_helmet | 9 | 198 | 139 | 0.043 | 0.061 | 0.051 | 0.619 | 0.567 |

Latency: mean 69.5 ms/image, range 55.9–1281.0 ms (CPU). Unmapped-label detections: 772 of
3,426 total raw detections — this model's 10 classes include `Mask`/`No-Mask`/`Safety
Cone`/`machinery`/`vehicle`, which the adapter correctly normalizes to `unknown_label` and
excludes from the compliance calculation (FR-DET-04) rather than dropping them silently.

### Reading these results

**Neither candidate is adequate for a live pilot as configured**, and the two candidates fail
in different, operationally important ways:

- `melihuzunoglu` is the more balanced of the two (recall skews toward catching violations,
  which is the safety-relevant direction to err on), but both classes sit well under 0.5 F1.
- `Hansung-Cho` scores better on `helmet` (F1 0.573) but is **close to unusable on
  `no_helmet`** (F1 0.051, precision 0.043) — it almost never correctly flags the actual
  violation case, which is the one class this application's architecture treats as the
  authoritative signal (FR-RULE-04's explicit `no_helmet` precedence). A model with strong
  "is wearing a helmet" accuracy but near-zero "is not wearing a helmet" accuracy is worse
  than it looks from the helmet number alone — exactly the "never report a single headline
  accuracy figure" case the roadmap warns about.
- Mean false-positive confidence is close to mean true-positive confidence for both models
  and both classes (e.g. melihuzunoglu helmet: 0.635 vs 0.715) — both models are frequently
  *confidently wrong*, so a simple confidence floor cannot substitute for a labeled
  held-out evaluation like this one.
- This cross-dataset result (models trained on their own unpublished data, evaluated on a
  third-party Roboflow export) is a real illustration of the plan's own domain-gap warning:
  accuracy on a model's own validation split (Hansung-Cho's card claims 0.831
  precision/0.685 recall) does not transfer to an unseen distribution. Renewi's cameras are
  a *further* domain shift (high-mounted, wide-angle, site-specific lighting) beyond what
  this benchmark already shows degrading — expect Phase-2 fine-tuning on real site footage
  to be necessary, not optional, before a pilot.

**Recommendation:** keep `melihuzunoglu` as the configured POC default (already the case) for
its more balanced, safety-appropriate error profile; keep `Hansung-Cho` recorded as a rejected
cross-check specifically because of its `no_helmet` failure rate, not solely its licence
ambiguity. Neither is approved for anything beyond an internal demonstration.

## Production route (documented, not yet built)

The roadmap's Apache-2.0 production path — **not** swapped into `app/detection.py`; this is
research/prototype documentation only, tracked for Phase 2:

1. **Framework:** YOLOX (Apache 2.0, Megvii) or another Apache-2.0 detector (RF-DETR,
   PP-YOLOE), replacing the current Ultralytics/AGPL-3.0 adapter.
2. **Training data:** [Safety Helmet Detection](https://www.kaggle.com/datasets/andrewmvd/hard-hat-detection) (Kaggle, **CC0 1.0** — public domain). Note this is a *different* dataset from the one used for the benchmark above (`keremberke/hard-hat-detection`, a separate Roboflow export); confirm licence terms independently before reusing either for training rather than evaluation.
3. **Benchmarking-only reference:** SH17 (CC BY-NC-SA 4.0, 17 classes including gloves/glasses/earmuffs) — richest class coverage and the academic reference benchmark, but its non-commercial clause means it must never be used to train shippable weights.
4. **Expected trade-off:** the CC0 helmet dataset covers helmet/no-helmet only — a
   fine-tuned YOLOX model would need additional Apache/CC0/permissively-licensed data (or
   Renewi's own annotated footage) to cover vest, gloves, and glasses at production quality.

## Production approval package checklist

Recorded per the roadmap's instruction, even though most items cannot close yet:

| Item | Status |
| --- | --- |
| Framework/source-code licence | ✅ Ultralytics AGPL-3.0 confirmed (POC); YOLOX Apache-2.0 identified for production (not yet integrated) |
| Model architecture licence | ✅ Same as framework for both current candidates |
| Base-weight licence | ✅ `melihuzunoglu` confirmed AGPL-3.0; ⚠️ `Hansung-Cho` disputed (see above) |
| Fine-tuned-weight ownership | ❌ Not applicable — no in-house fine-tune exists yet |
| Training/validation dataset licence | ❌ Unverified for both current candidates; CC0 candidate identified for the production route |
| Annotation-service terms | ❌ Not applicable — no in-house annotation performed |
| Hosted-inference/deployment terms | ⚠️ Roboflow hosted API terms not yet reviewed (blocked on account/credentials) |
| Dependency licences | ✅ `ultralytics`, `huggingface_hub`, `opencv-python-headless` — all permissive/AGPL as already tracked in `backend/requirements.txt` |
| Intended distribution model | ✅ Internal POC/demonstration only; this record itself is the "never silently production-cleared" control |

## Recorded in the application

Both benchmark results above are persisted as `ModelEvaluation` rows via
`POST /api/v1/model-evaluations` (model-evaluator role), visible through the Model registry
screen and `GET /api/v1/model-evaluations`, each with `approval_state=poc_only`.
