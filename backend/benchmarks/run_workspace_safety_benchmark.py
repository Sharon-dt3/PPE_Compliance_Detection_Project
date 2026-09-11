"""Benchmark hafizqaim/Workspace-Safety-Detection-using-YOLOv8 against the same held-out
test set, seed, and matching methodology as run_ppe_benchmark.py -- the second of the two
previously-untested Tier-2 candidates.

Unlike every other candidate benchmarked so far, this one's weights are not published on
Hugging Face Hub -- the model card only exists as a GitHub repository
(https://github.com/hafizqaim/Workspace-Safety-Detection-using-YOLOv8) whose trained
`best.pt` is attached to a GitHub Release, not committed to the repo itself. Hence its own
loader here (download-by-URL) rather than reusing `run_ppe_benchmark.py`'s
`hf_hub_download`-based CANDIDATES registry.

The checkpoint's embedded class list (17 classes, inspected directly from the loaded
Ultralytics model rather than assumed) is:
  0 Barefoots, 1 Ear-protection, 2 Harness, 3 No_Ear-Protection, 4 No_Glasses, 5 Sandals,
  6 boots, 7 face_mask, 8 face_nomask, 9 glasses, 10 hand_glove, 11 hand_noglove,
  12 head_helmet, 13 head_nohelmet, 14 person, 15 shoes, 16 vest
Only head_helmet/head_nohelmet/person/vest map to this benchmark's ground truth
(helmet/no_helmet only, per run_ppe_benchmark.py's dataset scope note); every other class
is preserved as unmapped, not silently dropped.

Usage:
    cd backend/benchmarks
    ../.venv/bin/python run_workspace_safety_benchmark.py
"""

from __future__ import annotations

import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from ultralytics import YOLO

from run_ppe_benchmark import CONF_THRESHOLD, IOU_THRESHOLD, RANDOM_SEED, SAMPLE_SIZE, DATA_DIR, iou, load_ground_truth
import random

WEIGHTS_URL = (
    "https://github.com/hafizqaim/Workspace-Safety-Detection-using-YOLOv8/"
    "releases/download/v1.0.0/best.pt"
)
WEIGHTS_CACHE = Path("data/workspace-safety-detection-best.pt")

LABEL_MAP = {
    "head_helmet": "helmet",
    "head_nohelmet": "no_helmet",
    "person": "person",
    "vest": "vest",
}


def download_weights() -> Path:
    if not WEIGHTS_CACHE.is_file():
        WEIGHTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading weights from {WEIGHTS_URL} ...")
        urllib.request.urlretrieve(WEIGHTS_URL, WEIGHTS_CACHE)
    return WEIGHTS_CACHE


def main() -> None:
    print("Candidate: hafizqaim/Workspace-Safety-Detection-using-YOLOv8")
    model_path = download_weights()
    model = YOLO(str(model_path))

    images, gt_by_image = load_ground_truth()
    eligible_image_ids = [image_id for image_id in images if gt_by_image.get(image_id)]
    random.Random(RANDOM_SEED).shuffle(eligible_image_ids)
    sample_ids = eligible_image_ids[:SAMPLE_SIZE]

    counts: dict[str, dict[str, int]] = {label: {"tp": 0, "fp": 0, "fn": 0} for label in ("helmet", "no_helmet")}
    tp_confidences: dict[str, list[float]] = defaultdict(list)
    fp_confidences: dict[str, list[float]] = defaultdict(list)
    unmapped_detections = 0
    total_detections = 0
    below_threshold_would_be = 0
    latencies_ms: list[float] = []
    failure_examples: list[str] = []

    for index, image_id in enumerate(sample_ids):
        image_meta = images[image_id]
        image_path = DATA_DIR / image_meta["file_name"]
        if not image_path.is_file():
            continue

        start = time.perf_counter()
        result = model.predict(source=str(image_path), conf=CONF_THRESHOLD, verbose=False)[0]
        latencies_ms.append((time.perf_counter() - start) * 1000)

        gt_boxes = [dict(item) for item in gt_by_image.get(image_id, [])]
        predictions = []
        names = result.names
        for box in result.boxes:
            raw_label = str(names[int(box.cls[0])]).lower()
            confidence = float(box.conf[0])
            total_detections += 1
            label = LABEL_MAP.get(raw_label)
            if label is None:
                unmapped_detections += 1
                continue
            if confidence < CONF_THRESHOLD:
                below_threshold_would_be += 1
                continue
            predictions.append({"label": label, "box": tuple(box.xyxy[0].tolist()), "confidence": confidence})

        image_had_failure = False
        for prediction in predictions:
            if prediction["label"] not in counts:
                continue
            best_match, best_iou = None, 0.0
            for gt in gt_boxes:
                if gt["matched"] or gt["label"] != prediction["label"]:
                    continue
                current_iou = iou(prediction["box"], gt["box"])
                if current_iou > best_iou:
                    best_match, best_iou = gt, current_iou
            if best_match is not None and best_iou >= IOU_THRESHOLD:
                best_match["matched"] = True
                counts[prediction["label"]]["tp"] += 1
                tp_confidences[prediction["label"]].append(prediction["confidence"])
            else:
                counts[prediction["label"]]["fp"] += 1
                fp_confidences[prediction["label"]].append(prediction["confidence"])
                image_had_failure = True

        for gt in gt_boxes:
            if not gt["matched"]:
                counts[gt["label"]]["fn"] += 1
                image_had_failure = True

        if image_had_failure and len(failure_examples) < 8:
            failure_examples.append(image_meta["file_name"])

        if (index + 1) % 50 == 0:
            print(f"  ...{index + 1}/{len(sample_ids)} images scored")

    print(f"\nSampled {len(sample_ids)} of {len(eligible_image_ids)} eligible held-out test images")
    print(f"Random seed: {RANDOM_SEED}  IoU threshold: {IOU_THRESHOLD}  Confidence threshold: {CONF_THRESHOLD}")
    print()
    print("Per-class results:")
    for label, values in counts.items():
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall and (precision + recall) else None
        mean_tp_conf = sum(tp_confidences[label]) / len(tp_confidences[label]) if tp_confidences[label] else None
        mean_fp_conf = sum(fp_confidences[label]) / len(fp_confidences[label]) if fp_confidences[label] else None
        print(
            f"  {label:12s} TP={tp:4d} FP={fp:4d} FN={fn:4d} "
            f"precision={precision if precision is None else round(precision, 3)} "
            f"recall={recall if recall is None else round(recall, 3)} "
            f"f1={f1 if f1 is None else round(f1, 3)} "
            f"mean_TP_confidence={mean_tp_conf if mean_tp_conf is None else round(mean_tp_conf, 3)} "
            f"mean_FP_confidence={mean_fp_conf if mean_fp_conf is None else round(mean_fp_conf, 3)}"
        )
    print()
    print(f"Total raw detections: {total_detections}")
    print(f"Unmapped-label detections (13 of 17 trained classes are out of scope; excluded from compliance calc): {unmapped_detections}")
    print(f"Detections below the {CONF_THRESHOLD} confidence threshold (excluded before matching): {below_threshold_would_be}")
    if latencies_ms:
        print(
            f"Latency: mean={sum(latencies_ms)/len(latencies_ms):.1f}ms  "
            f"min={min(latencies_ms):.1f}ms  max={max(latencies_ms):.1f}ms  (CPU inference, single image per call)"
        )
    print()
    print("Representative failure-case filenames (false positive or missed detection present):")
    for filename in failure_examples:
        print(f"  {filename}")


if __name__ == "__main__":
    main()
