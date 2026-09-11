[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — Model Evaluation and Licensing Trail

## Purpose

This record satisfies the Phase 5 roadmap's model-selection, licensing, and evaluation
requirements: a licence trail for every candidate model, a documented benchmark (not a single
headline accuracy figure), and the production route the POC does not yet ship.

## Other bundled model: face-detector privacy gate

This register also covers the mandatory OpenCV DNN face-detector model used by the evidence
privacy gate (Technology Decision §1) — a different concern from PPE detection below, but
still a model artifact requiring the same source/licence/checksum trail. See
[`backend/models/face_detector/README.md`](../../../../../backend/models/face_detector/README.md)
for the full record: source (`opencv/opencv`, Apache License 2.0), and a checksum verified
against OpenCV's own published manifest.

## Candidate registry

| Candidate | Role | Framework | Weights licence | Training data | Access |
| --- | --- | --- | --- | --- | --- |
| `melihuzunoglu/ppe-detection` | **Primary POC adapter** (configured default) | Ultralytics YOLOv11 | `agpl-3.0`, explicitly declared in the model card's YAML frontmatter | Declared only as `custom` — no further provenance published | [huggingface.co/melihuzunoglu/ppe-detection](https://huggingface.co/melihuzunoglu/ppe-detection) |
| `Hansung-Cho/yolov8-ppe-detection` | Cross-check candidate | Ultralytics YOLOv8n | Model card declares `mit` — but see **Licence conflict** below | Declared only as "public PPE / construction datasets (Kaggle etc.)"; card states "licence follows the data provider," i.e. unverified | [huggingface.co/Hansung-Cho/yolov8-ppe-detection](https://huggingface.co/Hansung-Cho/yolov8-ppe-detection) |
| [Roboflow Construction Site Safety](https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety) (hosted API, `construction-site-safety/27`) | Cross-check candidate | Roboflow-hosted YOLOv8s, `roboflow-universe-projects` (Roboflow's own account) | **CC BY 4.0** (confirmed on the project page) | Roboflow Universe dataset, 717 images, 25 classes; provenance beyond the CC BY 4.0 grant itself not further audited | Benchmarked 2026-09-11 via the hosted inference API (`https://serverless.roboflow.com/construction-site-safety/27`); see results below. The provider-neutral adapter interface (`app/detection.py`) is ready to accept a Roboflow-backed `DetectionProvider` for live use if ever needed; the swap requires no change to `decision.py`, `processing.py`, or the API surface. |

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
- **Sampling:** 300 of the 1,974 test images that have at least one relevant ground-truth box, drawn with a fixed random seed (`20260909`) so all three candidates were scored against the *identical* image set.
- **Matching:** IoU ≥ 0.5, one ground-truth box may match at most one prediction of the same label; a prediction below the provider's `conf=0.25` threshold was never produced by Ultralytics' own filtering (or, for the Roboflow candidate, is filtered client-side after the response — see below).
- **Environment:** the two Hugging Face candidates run CPU-only inference locally (Apple Silicon, no GPU), one image per predict call. The Roboflow candidate is a live hosted-API call per image instead — its coordinates are returned in the API's own fixed inference resolution (640×640) and rescaled back to each image's original pixel dimensions before IoU matching, and its latency numbers reflect network round-trip time, not raw model compute.
- **Reproduce it:** the benchmark scripts and raw output are attached to this evaluation; rerun with `python run_ppe_benchmark.py --repo-id <candidate>` for the two Hugging Face candidates, or `python run_roboflow_benchmark.py` (requires `ROBOFLOW_API_KEY` in the environment) for the Roboflow candidate.

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

### Roboflow `construction-site-safety/27` (cross-check, 2026-09-11)

| Class | TP | FP | FN | Precision | Recall | F1 | Mean TP confidence | Mean FP confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| helmet | 351 | 206 | 321 | 0.630 | 0.522 | 0.571 | 0.841 | 0.782 |
| no_helmet | 6 | 151 | 142 | 0.038 | 0.041 | 0.039 | 0.741 | 0.732 |

Latency: mean 1,161.1 ms/image, range 668.9–4,398.7 ms (live hosted-API round trip, not raw
model compute — not comparable to the two locally-run candidates' latency numbers). Unmapped-
label detections: 547 of 2,462 total raw detections — this model's 25 classes include many
construction-site/vehicle classes (`truck`, `Excavator`, `Ladder`, `Safety Cone`, `Mask`, etc.)
correctly normalized to `unknown_label` and excluded from compliance (FR-DET-04).

### Reading these results

**None of the three candidates is adequate for a live pilot as configured**, and the failure
patterns diverge in ways worth reading carefully rather than as one aggregate score:

- `melihuzunoglu` is the most balanced of the three (recall skews toward catching violations,
  the safety-relevant direction to err on), but both classes sit well under 0.5 F1.
- `Hansung-Cho` scores better on `helmet` (F1 0.573) but is **close to unusable on
  `no_helmet`** (F1 0.051, precision 0.043).
- **Roboflow `construction-site-safety/27` has the best `helmet` score of the three (F1
  0.571) but the single worst `no_helmet` score of the three** (F1 0.039, TP=6 against
  FN=142 — it correctly caught a real "no helmet" case only 6 times out of 148 in the
  ground truth). This is despite Roboflow's own published aggregate numbers for this model
  (84.1% mAP@50, 92.7% precision, 77.4% recall) looking considerably stronger than either
  Hugging Face candidate's self-reported numbers — the highest-advertised candidate of the
  three turned in the worst result on the one class that actually matters most for this
  application.
- All three candidates share the same underlying failure shape: strong on `helmet`, weak to
  catastrophic on `no_helmet` — the one class this application's architecture treats as the
  authoritative violation signal (FR-RULE-04's explicit `no_helmet` precedence). A model
  with strong "is wearing a helmet" accuracy but near-zero "is not wearing a helmet"
  accuracy is worse than it looks from the helmet number alone — exactly the "never report
  a single headline accuracy figure" case the roadmap warns about, and now demonstrated
  three separate times, not once.
- Mean false-positive confidence stays close to mean true-positive confidence for every
  model and every class (e.g. Roboflow helmet: 0.782 vs 0.841) — all three models are
  frequently *confidently wrong*, so a simple confidence floor cannot substitute for a
  labeled held-out evaluation like this one.
- This cross-dataset result (all three models trained on data other than this held-out set)
  is a real illustration of the plan's own domain-gap warning: accuracy on a model's own
  validation split or vendor-published benchmark does not transfer to an unseen
  distribution — Roboflow's own 77.4% recall claim collapsing to 4.1% recall on `no_helmet`
  here is the sharpest demonstration of that gap recorded in this evaluation. Renewi's
  cameras are a *further* domain shift (high-mounted, wide-angle, site-specific lighting)
  beyond what this benchmark already shows degrading — expect Phase-2 fine-tuning on real
  site footage to be necessary, not optional, before a pilot.

**Recommendation:** keep `melihuzunoglu` as the configured POC default (already the case) for
its more balanced, safety-appropriate error profile across both classes rather than either
alternative's stronger-helmet/weaker-no_helmet trade-off; keep `Hansung-Cho` and Roboflow
`construction-site-safety/27` both recorded as rejected cross-checks specifically because of
their `no_helmet` failure rates. None of the three is approved for anything beyond an
internal demonstration.

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
| Training/validation dataset licence | ❌ Unverified for the two HF candidates; ✅ Roboflow `construction-site-safety/27` confirmed CC BY 4.0; CC0 candidate identified for the production route |
| Annotation-service terms | ❌ Not applicable — no in-house annotation performed |
| Hosted-inference/deployment terms | ✅ Roboflow hosted API reviewed and benchmarked (2026-09-11) — serverless inference over `https://serverless.roboflow.com`, requires a per-account API key never committed to source control |
| Dependency licences | ✅ `ultralytics`, `huggingface_hub`, `opencv-python-headless` — all permissive/AGPL as already tracked in `backend/requirements.txt` |
| Intended distribution model | ✅ Internal POC/demonstration only; this record itself is the "never silently production-cleared" control |

## Recorded in the application

The two Hugging Face candidates' benchmark results are persisted as `ModelEvaluation` rows
via `POST /api/v1/model-evaluations` (model-evaluator role), visible through the Model
registry screen and `GET /api/v1/model-evaluations`, each with `approval_state=poc_only`.
The Roboflow candidate's results above are not yet recorded as a `ModelEvaluation` row in
the running application database (this evaluation document is the durable record of the
run); add one via the same endpoint or the Model registry screen if a persisted row is
also wanted.
