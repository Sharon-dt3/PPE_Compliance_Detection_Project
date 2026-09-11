[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — Model Evaluation and Licensing Trail

## Purpose

This record satisfies the Phase 5 roadmap's model-selection, licensing, and evaluation
requirements: a licence trail for every candidate model, a documented benchmark (not a single
headline accuracy figure), and the production route — evidenced with a real, trained,
benchmarked checkpoint (2026-09-11), not just proposed on paper — that the POC does not yet
ship.

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
| YOLOX-Nano, fine-tuned in-house (`backend/benchmarks/yolox_finetune`) | **Production-route candidate — actually built, 2026-09-11** | Megvii YOLOX (Apache 2.0), fine-tuned from the official `yolox_nano.pth` checkpoint | **Apache 2.0** (framework + base weights) — the fine-tuned weights are ours; ownership follows this repository | [Voxel51/hard-hat-detection](https://huggingface.co/datasets/Voxel51/hard-hat-detection), a verified **CC0 1.0** mirror of the exact Kaggle dataset the roadmap names (`andrewmvd/hard-hat-detection`); 750 of its 5,000 images used (600 train / 150 val, fixed seed `20260911`) | Trained locally (CPU, ~30 min for 20 epochs including 5 evaluation passes); checkpoint at `backend/benchmarks/yolox_finetune/YOLOX_outputs/ppe_nano/best_ckpt.pth`. Not wired into `app/detection.py` as a live `DetectionProvider` — this is a benchmarked artifact, not yet an integrated option. |
| `Hexmon/vyra-yolo-ppe-detection` | Tier-2 cross-check candidate | Ultralytics YOLOv8 | Model card declares `cc-by-4.0` — but see **Licence conflict** below (same Ultralytics-AGPL dispute as `Hansung-Cho`) | Declared as `roboflow/personal-protective-equipment-combined-model`; this exact dataset repo does not resolve on Hugging Face (404 on lookup), so provenance beyond the name itself is unverified | [huggingface.co/Hexmon/vyra-yolo-ppe-detection](https://huggingface.co/Hexmon/vyra-yolo-ppe-detection) |
| `hafizqaim/Workspace-Safety-Detection-using-YOLOv8` | Tier-2 cross-check candidate | Ultralytics YOLOv8 | **No licence declared at all** — confirmed via the GitHub API (`license: null`); default copyright applies, meaning no redistribution/production rights exist absent explicit permission from the author | Roboflow "PPE Detection v3" project, ~23,000 images, 17 classes; not further audited | [github.com/hafizqaim/Workspace-Safety-Detection-using-YOLOv8](https://github.com/hafizqaim/Workspace-Safety-Detection-using-YOLOv8); weights are not on Hugging Face Hub, only attached to a [GitHub Release](https://github.com/hafizqaim/Workspace-Safety-Detection-using-YOLOv8/releases/tag/v1.0.0). The repo's own `inference.py` already supports both webcam (`cv2.VideoCapture(0)`) and video-file input (`cv2.VideoCapture('test1.mp4')`) — the two are switched by commenting/uncommenting one line, confirmed by reading the script directly rather than assumed from the README. |

### Licence conflict on `Hansung-Cho/yolov8-ppe-detection` and `Hexmon/vyra-yolo-ppe-detection`

The model card's YAML frontmatter declares `license: mit` for `Hansung-Cho`. However, it is a
YOLOv8n fine-tune built with the Ultralytics framework, and Ultralytics states its own AGPL-3.0
licence follows models trained with its code, not only the framework itself (this is the
same caveat the implementation plan already raises for the primary candidate). The
author's MIT tag and Ultralytics' AGPL position on trained weights are in unresolved
tension. **Treat this candidate as AGPL-3.0-encumbered until the author or Ultralytics
confirms otherwise** — do not rely on the self-declared MIT tag alone before any
commercial use.

**The identical dispute applies to `Hexmon/vyra-yolo-ppe-detection`.** Its model card
explicitly declares `license: cc-by-4.0` (verified via `HfApi().model_info(...).card_data`,
not assumed) and it is, on its own accuracy numbers below, the strongest candidate recorded in
this entire evaluation. But it is also an Ultralytics YOLOv8 fine-tune (`tags: ['ultralytics',
'yolov8', ...]`), so the same Ultralytics-AGPL-follows-trained-weights position that puts
`Hansung-Cho`'s self-declared MIT in dispute applies here with equal force. **Best accuracy of
any candidate in this evaluation, but its CC-BY-4.0 tag should be treated as similarly
disputed/AGPL-encumbered, not as a clean licence**, until the author or Ultralytics confirms
otherwise — it does not carry the same production-cleared status as the YOLOX-Nano fine-tune,
which was trained with genuinely Apache-2.0-licensed code (Megvii's YOLOX, not Ultralytics).

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
- **Sampling:** 300 of the 1,974 test images that have at least one relevant ground-truth box, drawn with a fixed random seed (`20260909`) so all four candidates were scored against the *identical* image set.
- **Matching:** IoU ≥ 0.5, one ground-truth box may match at most one prediction of the same label; a prediction below the provider's `conf=0.25` threshold was never produced by Ultralytics' own filtering (or, for the Roboflow candidate, is filtered client-side after the response — see below).
- **Environment:** the two Hugging Face candidates and the fine-tuned YOLOX model all run CPU-only inference locally (Apple Silicon, no GPU), one image per predict call. The Roboflow candidate is a live hosted-API call per image instead — its coordinates are returned in the API's own fixed inference resolution (640×640) and rescaled back to each image's original pixel dimensions before IoU matching, and its latency numbers reflect network round-trip time, not raw model compute.
- **Reproduce it:** the benchmark scripts and raw output are attached to this evaluation; rerun with `python run_ppe_benchmark.py --repo-id <candidate>` for the Hugging-Face-hosted candidates (`melihuzunoglu/ppe-detection`, `Hansung-Cho/yolov8-ppe-detection`, `Hexmon/vyra-yolo-ppe-detection`), `python run_roboflow_benchmark.py` (requires `ROBOFLOW_API_KEY` in the environment) for the Roboflow candidate, `python run_workspace_safety_benchmark.py` for `hafizqaim/Workspace-Safety-Detection-using-YOLOv8` (weights downloaded from its GitHub Release, not Hugging Face Hub), or `PYTHONPATH="$(pwd)/yolox_finetune" python run_yolox_benchmark.py` for the fine-tuned YOLOX model. `python compare_candidates.py` prints all six side by side from the saved result files.

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

### YOLOX-Nano fine-tuned, licence-clean production candidate (2026-09-11)

| Class | TP | FP | FN | Precision | Recall | F1 | Mean TP confidence | Mean FP confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| helmet | 519 | 263 | 153 | 0.664 | 0.772 | **0.714** | 0.747 | 0.627 |
| no_helmet | 85 | 100 | 63 | 0.459 | 0.574 | **0.511** | 0.723 | 0.608 |

Latency: mean 60.6 ms/image, range 57.5–111.4 ms (CPU, real inference — fastest of all four
candidates despite being fine-tuned in-house). Unmapped-label detections: 659 of 2,604 total
raw detections (this model's third class, `person`, has no ground truth in this benchmark
dataset and is excluded the same way every other candidate's non-helmet classes are).

**This is the best result recorded in this evaluation on both classes, by a clear margin.**
Trained via `backend/benchmarks/prepare_voc_dataset.py` + a patched fork of
`Megvii-BaseDetection/YOLOX` (see "Production route" below for what the patches were and
why), on 600 of the CC0-licensed `Voxel51/hard-hat-detection` images, for 20 epochs
(~30 minutes, CPU-only). Full raw output: `backend/benchmarks/2026-09-11_yolox-nano-finetuned-cc0.txt`.

Two things worth being precise about:
- **This result is not free — it required real training**, not just downloading and running
  a model like every other candidate. That effort is exactly what the roadmap asked this
  requirement to prove is achievable, not a shortcut.
- **`person` detection from the same fine-tuned model is weak (VOC-style AP 0.023 on its own
  held-out validation set)** — the CC0 dataset's `person` labels are sparse in the 750-image
  subset used, and 20 epochs was not enough to learn that class well. Not a blocker for the
  compliance use case (which keys off `helmet`/`no_helmet`, not `person`, for the violation
  decision), but a real, disclosed weakness of this specific checkpoint.

### `Hexmon/vyra-yolo-ppe-detection` (Tier-2 cross-check, 2026-09-11)

| Class | TP | FP | FN | Precision | Recall | F1 | Mean TP confidence | Mean FP confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| helmet | 606 | 68 | 66 | 0.899 | 0.902 | **0.900** | 0.714 | 0.489 |
| no_helmet | 112 | 15 | 36 | 0.882 | 0.757 | **0.815** | 0.658 | 0.576 |

Latency: mean 288.7 ms/image, range 236.7–2331.2 ms (CPU). Unmapped-label detections: 38 of
929 total raw detections — this model's 14 classes include `Fall-Detected`, `Ladder`, `Safety
Cone`, `Goggles`/`NO-Goggles`, `Mask`/`NO-Mask`, all correctly normalized to `unknown_label`
and excluded from compliance (FR-DET-04). Full raw output:
`backend/benchmarks/2026-09-11_hexmon-vyra-yolo-ppe-detection.txt`.

**This is the best result recorded in this entire evaluation on both classes** — ahead of
even the fine-tuned YOLOX production candidate (helmet 0.900 vs 0.714; no_helmet 0.815 vs
0.511). See the **Licence conflict** section above: this strength comes with the same
disputed-licence caveat as `Hansung-Cho`, not a clean bill of health.

### `hafizqaim/Workspace-Safety-Detection-using-YOLOv8` (Tier-2 cross-check, 2026-09-11)

| Class | TP | FP | FN | Precision | Recall | F1 | Mean TP confidence | Mean FP confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| helmet | 305 | 134 | 367 | 0.695 | 0.454 | 0.549 | 0.544 | 0.491 |
| no_helmet | 1 | 116 | 147 | 0.009 | 0.007 | **0.008** | 0.492 | 0.418 |

Latency: mean 84.9 ms/image, range 62.9–1114.3 ms (CPU). Unmapped-label detections: 698 of
1,884 total raw detections — this model's 17 trained classes (`Barefoots`, `Ear-protection`,
`Harness`, `Sandals`, `boots`, `face_mask`, `glasses`, `hand_glove`, `shoes`, etc.) are mostly
out of this benchmark's scope; only `head_helmet`→`helmet`, `head_nohelmet`→`no_helmet`,
`person`, and `vest` map to it. Full raw output:
`backend/benchmarks/2026-09-11_workspace-safety-detection-yolov8.txt`.

**This is the single worst `no_helmet` result recorded in this evaluation** — TP=1 against
FN=147, meaning it correctly caught a real "no helmet" case only once out of 148 in the ground
truth (recall 0.7%). Its own published README numbers (`head_helmet` mAP50 86.6%) describe a
combined helmet-detection metric across its own validation split, not the specific
"correctly flags an unhelmeted person against unseen data" behavior this benchmark measures —
another instance of the vendor-published-vs-held-out-benchmark gap already noted for
Roboflow's candidate below.

### Reading these results

**None of the six candidates is production-cleared as configured** — the fine-tuned model
being the licence-clean one doesn't mean it's finished; see the checklist below for what
still needs review. `Hexmon/vyra-yolo-ppe-detection` scores highest of all six on raw
accuracy but is not licence-clean either (see above). The failure patterns diverge in ways
worth reading carefully rather than as one aggregate score:

- `melihuzunoglu` (the current POC default) is the most balanced of the three off-the-shelf
  candidates (recall skews toward catching violations, the safety-relevant direction to err
  on), but both classes sit well under 0.5 F1.
- `Hansung-Cho` scores better on `helmet` (F1 0.573) but is **close to unusable on
  `no_helmet`** (F1 0.051, precision 0.043).
- **Roboflow `construction-site-safety/27` has the best `helmet` score of the off-the-shelf
  three (F1 0.571) but the single worst `no_helmet` score of any candidate** (F1 0.039,
  TP=6 against FN=142 — it correctly caught a real "no helmet" case only 6 times out of 148
  in the ground truth). This is despite Roboflow's own published aggregate numbers for this
  model (84.1% mAP@50, 92.7% precision, 77.4% recall) looking considerably stronger than any
  other candidate's self-reported numbers — the highest-advertised candidate turned in the
  worst result on the one class that actually matters most for this application.
- All three original off-the-shelf candidates share the same underlying failure shape:
  strong on `helmet`, weak to catastrophic on `no_helmet` — the one class this application's
  architecture treats as the authoritative violation signal (FR-RULE-04's explicit
  `no_helmet` precedence). `hafizqaim/Workspace-Safety-Detection-using-YOLOv8` shows the
  same shape even more severely (`no_helmet` F1 0.008 — the single worst result recorded in
  this evaluation). **Two candidates break that pattern**: the fine-tuned YOLOX model
  (`no_helmet` F1 0.511, close to its own `helmet` F1 of 0.714 — a genuinely balanced result)
  and `Hexmon/vyra-yolo-ppe-detection` even more so (`no_helmet` F1 0.815, `helmet` F1
  0.900 — the best result on both classes in this whole evaluation, licence caveat aside).
- Mean false-positive confidence stays close to mean true-positive confidence for every
  off-the-shelf model and every class (e.g. Roboflow helmet: 0.782 vs 0.841) — those three
  models are frequently *confidently wrong*, so a simple confidence floor cannot substitute
  for a labeled held-out evaluation like this one. The fine-tuned model shows the same
  pattern to a lesser degree (helmet: 0.627 vs 0.747) — still real, just smaller.
- This cross-dataset result (the three off-the-shelf models trained on data other than this
  held-out set) is a real illustration of the plan's own domain-gap warning: accuracy on a
  model's own validation split or vendor-published benchmark does not transfer to an unseen
  distribution — Roboflow's own 77.4% recall claim collapsing to 4.1% recall on `no_helmet`
  here is the sharpest demonstration of that gap recorded in this evaluation, and the
  fine-tuned model's own strength is the flip side of the same lesson: it does *not* suffer
  this gap on helmet/no_helmet because it was actually trained toward this kind of data,
  not just evaluated on it cold. Renewi's cameras are a *further* domain shift (high-mounted,
  wide-angle, site-specific lighting) beyond what any of these four results validates —
  fine-tuning on real site footage remains necessary, not optional, before a pilot, even for
  the fine-tuned candidate.

**Recommendation:** keep `melihuzunoglu` as the configured **POC** default for now (already
the case) — switching the live app to the fine-tuned YOLOX model would need a new
`DetectionProvider` adapter (today's adapter is Ultralytics-only) and its own full
production-approval review, not just better benchmark numbers. Keep `Hansung-Cho`, Roboflow
`construction-site-safety/27`, and `hafizqaim/Workspace-Safety-Detection-using-YOLOv8` all
recorded as rejected cross-checks specifically because of their `no_helmet` failure rates.
Treat the fine-tuned YOLOX model as the **leading production-route candidate** going
forward — it is the only strong-accuracy candidate in this evaluation with no licence
exposure at all. `Hexmon/vyra-yolo-ppe-detection` has the best raw accuracy of any candidate
recorded here, but stays a rejected cross-check alongside `Hansung-Cho` until its
Ultralytics-AGPL licence dispute is actually resolved — a strong benchmark result does not
override an unresolved licence question.

## Production route (built and benchmarked, 2026-09-11 — not yet wired into the app)

The roadmap's Apache-2.0 production path is no longer only documentation — it now has a
real, trained, benchmarked checkpoint (results above). It is still **not** swapped into
`app/detection.py`; that would need a new `DetectionProvider` implementation (today's only
handles Ultralytics-format models) plus its own full production-approval pass.

1. **Framework:** [Megvii-BaseDetection/YOLOX](https://github.com/Megvii-BaseDetection/YOLOX) (Apache 2.0), YOLOX-Nano variant, fine-tuned from the official `yolox_nano.pth` release checkpoint. RF-DETR/PP-YOLOE remain untried alternatives, listed for completeness in case YOLOX underperforms on real site footage later.
2. **Training data:** [Voxel51/hard-hat-detection](https://huggingface.co/datasets/Voxel51/hard-hat-detection) — a verified **CC0 1.0** Hugging Face mirror of the exact Kaggle dataset the roadmap names (`andrewmvd/hard-hat-detection`, "Safety Helmet Detection"). This is a *different* dataset from the one used for the benchmark comparisons above (`keremberke/hard-hat-detection`), kept deliberately separate so the training data and the held-out test data never overlap. 750 of its 5,000 images were used (600 train / 150 val, fixed seed `20260911`) to keep CPU training time tractable; the remaining ~4,250 are available for a fuller future run.
3. **What training actually required, on this CPU-only machine:** YOLOX's official training code assumes a CUDA GPU throughout — not just a config flag, but its data-loading pipeline (`DataPrefetcher` uses `torch.cuda.Stream()` and async `.cuda()` transfers with no CPU fallback). Getting a real training run working required patching several CUDA-only assumptions in the cloned repo (`yolox/core/trainer.py`, `yolox/data/data_prefetcher.py`, `yolox/exp/yolox_base.py`, `yolox/evaluators/*.py`, `yolox/models/yolo_head.py`) to fall back to CPU when no GPU is present, plus fixing an abandoned-dependency crash (`thop`/`distutils` on Python 3.12), the VOC data loader's hardcoded 20-class Pascal list (replaced with the dataset's real 3 classes), and a PyTorch-version type bug in mosaic augmentation (disabled rather than patched further, a real accuracy trade-off — see below). All patches are isolated to `backend/benchmarks/yolox_finetune/` (a git-ignored clone), not to any file the app itself imports.
4. **Benchmarking-only reference:** SH17 (CC BY-NC-SA 4.0, 17 classes including gloves/glasses/earmuffs) — richest class coverage and the academic reference benchmark, but its non-commercial clause means it must never be used to train shippable weights. Not used here.
5. **Expected trade-offs, now measured rather than just anticipated:**
   - **Class coverage:** the CC0 dataset covers helmet/no-helmet/person only — vest, gloves,
     and glasses would need additional permissively-licensed data (or Renewi's own annotated
     footage) layered on to reach full production coverage.
   - **`person` accuracy is weak** in this specific checkpoint (VOC AP 0.023) — likely sparse
     `person` labels in the 750-image training subset, not a fundamental limitation of the
     approach.
   - **Mosaic augmentation was disabled**, not fixed — a genuine PyTorch-version
     incompatibility in `MosaicDetection`'s output types was worked around by turning the
     augmentation off rather than patching it further. Mosaic typically improves small-object
     and occlusion robustness, so a properly-patched, mosaic-enabled run would likely score
     higher still; this result is a lower bound on what fine-tuning YOLOX can achieve here,
     not a ceiling.
   - **Only 750 of 5,000 available training images were used**, and only 20 epochs — both
     deliberately scoped down to keep CPU training time practical in this environment. A
     longer run on the full dataset (or with a GPU) would very plausibly do better.

## Production approval package checklist

Recorded per the roadmap's instruction, even though most items cannot close yet:

| Item | Status |
| --- | --- |
| Framework/source-code licence | ✅ Ultralytics AGPL-3.0 confirmed (POC); ✅ YOLOX Apache-2.0 confirmed and actually fine-tuned (2026-09-11) — not yet integrated into `app/detection.py` |
| Model architecture licence | ✅ Same as framework for all current candidates |
| Base-weight licence | ✅ `melihuzunoglu` confirmed AGPL-3.0; ⚠️ `Hansung-Cho` disputed (see above); ⚠️ `Hexmon` disputed (same Ultralytics-AGPL reasoning as `Hansung-Cho`, see above); ❌ `Workspace-Safety-Detection` has no licence declared at all (GitHub API confirms `license: null`); ✅ YOLOX-Nano base checkpoint confirmed Apache 2.0 (official Megvii release) |
| Fine-tuned-weight ownership | ✅ In-house YOLOX-Nano fine-tune exists (`backend/benchmarks/yolox_finetune/YOLOX_outputs/ppe_nano/best_ckpt.pth`) — ownership follows this repository, no third-party fine-tuning service involved |
| Training/validation dataset licence | ❌ Unverified for the two HF cross-check candidates; ✅ Roboflow `construction-site-safety/27` confirmed CC BY 4.0; ✅ the production fine-tune's own training data (`Voxel51/hard-hat-detection`) confirmed CC0 1.0 and actually used, not just identified |
| Annotation-service terms | ❌ Not applicable — no in-house annotation performed; the CC0 dataset's existing labels were used as-is |
| Hosted-inference/deployment terms | ✅ Roboflow hosted API reviewed and benchmarked (2026-09-11) — serverless inference over `https://serverless.roboflow.com`, requires a per-account API key never committed to source control |
| Dependency licences | ✅ `ultralytics`, `huggingface_hub`, `opencv-python-headless` — all permissive/AGPL as already tracked in `backend/requirements.txt`. The YOLOX fine-tuning environment (`backend/benchmarks/yolox_finetune/`) is git-ignored and kept isolated from the app's own dependency set. |
| Intended distribution model | ✅ Internal POC/demonstration only; this record itself is the "never silently production-cleared" control. The fine-tuned YOLOX checkpoint is *not* wired into the running app and carries no risk of being silently deployed. |

## Recorded in the application

The original two Hugging Face candidates' benchmark results are persisted as
`ModelEvaluation` rows via `POST /api/v1/model-evaluations` (model-evaluator role), visible
through the Model registry screen and `GET /api/v1/model-evaluations`, each with
`approval_state=poc_only`. The Roboflow candidate's, the fine-tuned YOLOX model's, and the
two Tier-2 candidates' (`Hexmon/vyra-yolo-ppe-detection`,
`hafizqaim/Workspace-Safety-Detection-using-YOLOv8`) results above are not yet recorded as
`ModelEvaluation` rows in the running application database (this evaluation document is
the durable record of all four runs); add them via the same endpoint or the Model registry
screen if persisted rows are also wanted. Any row added for `Hexmon` should carry the same
`poc_only` state and the same licence-dispute caveat as `Hansung-Cho`'s existing row, not a
higher approval state — a strong benchmark result on this held-out set is not the same as a
completed production-approval review, and does not resolve an open licence question either.
