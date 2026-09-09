"""Benchmark melihuzunoglu/ppe-detection against a held-out labeled hard-hat test set.

Dataset: keremberke/hard-hat-detection (Hugging Face Datasets), a Roboflow export of a
hard-hat/no-hard-hat detection set, COCO-format annotations, `test` split (2001 images).
This dataset only annotates two classes (hardhat, no-hardhat) — it does not label vest,
person, gloves, or glasses, so this benchmark covers helmet/no_helmet only. That scope
limitation is reported explicitly in the output rather than implied away.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path

from ultralytics import YOLO
from huggingface_hub import hf_hub_download

RANDOM_SEED = 20260909
SAMPLE_SIZE = 300
IOU_THRESHOLD = 0.5
CONF_THRESHOLD = 0.25

DATA_DIR = Path("data/test")
ANNOTATIONS_PATH = DATA_DIR / "_annotations.coco.json"

# Normalize the ground-truth categories to the application's shared label set (see
# backend/app/detection.py `_LABELS`).
GT_LABEL_MAP = {"hardhat": "helmet", "no-hardhat": "no_helmet"}

CANDIDATES: dict[str, dict[str, object]] = {
    "melihuzunoglu/ppe-detection": {
        "filename": "best.pt",
        "label_map": {"human": "person", "helmet": "helmet", "no-helmet": "no_helmet", "vest": "vest"},
    },
    "Hansung-Cho/yolov8-ppe-detection": {
        "filename": "best.pt",
        "label_map": {
            "hardhat": "helmet",
            "no-hardhat": "no_helmet",
            "safety vest": "vest",
            "no-safety vest": "no_vest",
            "person": "person",
        },
    },
}


def iou(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    """Return intersection-over-union for two (x1, y1, x2, y2) boxes."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def load_ground_truth() -> tuple[dict[int, dict], dict[int, list[dict]]]:
    """Load COCO images and per-image ground-truth boxes normalized to our label set."""
    data = json.loads(ANNOTATIONS_PATH.read_text())
    categories = {c["id"]: c["name"] for c in data["categories"]}
    images = {img["id"]: img for img in data["images"]}
    gt_by_image: dict[int, list[dict]] = defaultdict(list)
    for ann in data["annotations"]:
        raw_name = categories[ann["category_id"]]
        label = GT_LABEL_MAP.get(raw_name)
        if label is None:
            continue
        x, y, w, h = ann["bbox"]
        gt_by_image[ann["image_id"]].append({"label": label, "box": (x, y, x + w, y + h), "matched": False})
    return images, gt_by_image


def main() -> None:
    """Run the benchmark and print a class-level, non-aggregated report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", default="melihuzunoglu/ppe-detection", choices=list(CANDIDATES))
    args = parser.parse_args()
    candidate = CANDIDATES[args.repo_id]
    model_label_map: dict[str, str] = candidate["label_map"]  # type: ignore[assignment]

    images, gt_by_image = load_ground_truth()
    eligible_image_ids = [image_id for image_id in images if gt_by_image.get(image_id)]
    random.Random(RANDOM_SEED).shuffle(eligible_image_ids)
    sample_ids = eligible_image_ids[:SAMPLE_SIZE]

    print(f"Candidate: {args.repo_id}")
    model_path = hf_hub_download(repo_id=args.repo_id, filename=str(candidate["filename"]))
    model = YOLO(model_path)

    counts: dict[str, dict[str, int]] = {
        label: {"tp": 0, "fp": 0, "fn": 0} for label in ("helmet", "no_helmet")
    }
    tp_confidences: dict[str, list[float]] = defaultdict(list)
    fp_confidences: dict[str, list[float]] = defaultdict(list)
    unmapped_detections = 0
    total_detections = 0
    below_threshold_would_be = 0
    latencies_ms: list[float] = []
    failure_examples: list[str] = []

    for image_id in sample_ids:
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
            label = model_label_map.get(raw_label)
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

    print(f"Sampled {len(sample_ids)} of {len(eligible_image_ids)} eligible held-out test images")
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
    print(f"Unmapped-label detections (preserved as unknown_label, excluded from compliance calc): {unmapped_detections}")
    print(f"Detections below the {CONF_THRESHOLD} confidence threshold (excluded before matching): {below_threshold_would_be}")
    print(f"Latency: mean={sum(latencies_ms)/len(latencies_ms):.1f}ms  "
          f"min={min(latencies_ms):.1f}ms  max={max(latencies_ms):.1f}ms  (CPU inference, single image per call)")
    print()
    print("Representative failure-case filenames (false positive or missed detection present):")
    for filename in failure_examples:
        print(f"  {filename}")


if __name__ == "__main__":
    main()
