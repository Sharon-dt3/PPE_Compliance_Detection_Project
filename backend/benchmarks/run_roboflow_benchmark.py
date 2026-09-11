"""Benchmark the Roboflow-hosted "Construction Site Safety" model against the exact same
held-out test set, sampling, seed, and matching methodology as run_ppe_benchmark.py --
this is the specific cross-check the implementation plan names separately from "at least
one alternate hosted YOLO PPE model" (Hansung-Cho, already covered by the other script).

Unlike the other two candidates, this one is a Roboflow-hosted inference API, not a
downloadable weights file -- see backend/benchmarks/README.md's "Known scope limits" note
on why this needs a different adapter (an HTTP call, not hf_hub_download+YOLO).

Requires ROBOFLOW_API_KEY in the environment (see backend/.env; never commit this key).

Usage:
    cd backend
    . .venv/bin/activate
    set -a; source .env; set +a
    python benchmarks/run_roboflow_benchmark.py
"""

from __future__ import annotations

import base64
import os
import random
import time
from collections import defaultdict

import requests

from run_ppe_benchmark import CONF_THRESHOLD, IOU_THRESHOLD, RANDOM_SEED, SAMPLE_SIZE, DATA_DIR, iou, load_ground_truth

MODEL_ID = "construction-site-safety/27"
API_BASE = "https://serverless.roboflow.com"

# Normalizes this candidate's raw class names to the application's shared label set (the
# same normalization app/detection.py applies for the other two candidates). Every class not
# listed here (Mask, NO-Mask, and the construction-vehicle/site classes this model was also
# trained on) becomes unknown_label -- excluded from compliance the same way FR-DET-04
# requires live, never silently dropped.
LABEL_MAP = {
    "person": "person",
    "hardhat": "helmet",
    "no-hardhat": "no_helmet",
    "safety vest": "vest",
    "no-safety vest": "no_vest",
    "gloves": "gloves",
}


def predict(image_path, api_key: str, original_width: int, original_height: int) -> list[dict]:
    """Call the hosted inference API and return predictions rescaled to the source image size.

    The API infers at a fixed internal resolution (reported in the response's ``image``
    field) rather than the source image's own dimensions, so raw coordinates must be scaled
    back before they can be compared against ground-truth boxes recorded in original pixels.
    """
    with open(image_path, "rb") as handle:
        encoded = base64.b64encode(handle.read())

    response = requests.post(
        f"{API_BASE}/{MODEL_ID}",
        data=encoded,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    inferred_width = payload["image"]["width"]
    inferred_height = payload["image"]["height"]
    scale_x = original_width / inferred_width
    scale_y = original_height / inferred_height

    results = []
    for item in payload.get("predictions", []):
        cx, cy, w, h = item["x"] * scale_x, item["y"] * scale_y, item["width"] * scale_x, item["height"] * scale_y
        results.append(
            {
                "raw_label": str(item["class"]).lower(),
                "confidence": float(item["confidence"]),
                "box": (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
            }
        )
    return results


def main() -> None:
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        raise SystemExit("ROBOFLOW_API_KEY is not set. Run: set -a; source .env; set +a")

    images, gt_by_image = load_ground_truth()
    eligible_image_ids = [image_id for image_id in images if gt_by_image.get(image_id)]
    random.Random(RANDOM_SEED).shuffle(eligible_image_ids)
    sample_ids = eligible_image_ids[:SAMPLE_SIZE]

    print(f"Candidate: Roboflow {MODEL_ID} (hosted API)")

    counts: dict[str, dict[str, int]] = {label: {"tp": 0, "fp": 0, "fn": 0} for label in ("helmet", "no_helmet")}
    tp_confidences: dict[str, list[float]] = defaultdict(list)
    fp_confidences: dict[str, list[float]] = defaultdict(list)
    unmapped_detections = 0
    total_detections = 0
    below_threshold_would_be = 0
    latencies_ms: list[float] = []
    failure_examples: list[str] = []
    api_errors = 0

    for index, image_id in enumerate(sample_ids):
        image_meta = images[image_id]
        image_path = DATA_DIR / image_meta["file_name"]
        if not image_path.is_file():
            continue

        start = time.perf_counter()
        try:
            raw_predictions = predict(image_path, api_key, image_meta["width"], image_meta["height"])
        except requests.RequestException as error:
            api_errors += 1
            print(f"  [{index + 1}/{len(sample_ids)}] API error on {image_meta['file_name']}: {error}")
            continue
        latencies_ms.append((time.perf_counter() - start) * 1000)

        gt_boxes = [dict(item) for item in gt_by_image.get(image_id, [])]
        predictions = []
        for item in raw_predictions:
            total_detections += 1
            label = LABEL_MAP.get(item["raw_label"])
            if label is None:
                unmapped_detections += 1
                continue
            if item["confidence"] < CONF_THRESHOLD:
                below_threshold_would_be += 1
                continue
            predictions.append({"label": label, "box": item["box"], "confidence": item["confidence"]})

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

        if (index + 1) % 25 == 0:
            print(f"  ...{index + 1}/{len(sample_ids)} images scored")

    print(f"\nSampled {len(sample_ids)} of {len(eligible_image_ids)} eligible held-out test images")
    print(f"Random seed: {RANDOM_SEED}  IoU threshold: {IOU_THRESHOLD}  Confidence threshold: {CONF_THRESHOLD}")
    if api_errors:
        print(f"API errors (images skipped, not counted in results): {api_errors}")
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
    if latencies_ms:
        print(
            f"Latency: mean={sum(latencies_ms)/len(latencies_ms):.1f}ms  "
            f"min={min(latencies_ms):.1f}ms  max={max(latencies_ms):.1f}ms  (hosted API call, single image per request)"
        )
    print()
    print("Representative failure-case filenames (false positive or missed detection present):")
    for filename in failure_examples:
        print(f"  {filename}")


if __name__ == "__main__":
    main()
