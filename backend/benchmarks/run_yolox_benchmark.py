"""Benchmark the fine-tuned YOLOX-Nano checkpoint (licence-clean production baseline)
against the exact same held-out test set, seed, and matching methodology as the other
three candidates -- the direct, apples-to-apples comparison, not the VOC-style AP this
model was already evaluated with during training.

The fine-tuned model's own classes are helmet/person/head (not helmet/no_helmet); "head"
is the no-helmet signal per the plan's own description ("head functions as the no-helmet
signal"), so it is mapped to no_helmet here for comparison purposes. "person" has no
ground truth in this benchmark dataset either, same as every other candidate, and is
excluded from scoring.

Usage:
    cd backend/benchmarks
    . ../.venv/bin/activate
    PYTHONPATH="$(pwd)/yolox_finetune" python run_yolox_benchmark.py
"""

from __future__ import annotations

import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent / "yolox_finetune"))

from run_ppe_benchmark import CONF_THRESHOLD, IOU_THRESHOLD, RANDOM_SEED, SAMPLE_SIZE, DATA_DIR, iou, load_ground_truth

CHECKPOINT = Path(__file__).parent / "yolox_finetune" / "YOLOX_outputs" / "ppe_nano" / "best_ckpt.pth"
TEST_SIZE = (416, 416)

# helmet=0, person=1, head=2, per yolox_finetune/yolox/data/datasets/voc_classes.py
LABEL_MAP = {0: "helmet", 2: "no_helmet"}  # "head" (1) mapped to no_helmet; "person" excluded


def load_model():
    from exps.ppe_nano import Exp

    exp = Exp()
    model = exp.get_model()
    # weights_only=False is safe here: this is our own checkpoint from the training run
    # in this same session, not a downloaded third-party file.
    ckpt = torch.load(CHECKPOINT, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return model


def infer(model, image_path: Path) -> list[dict]:
    """Run inference and return detections rescaled to the original image's pixel size."""
    from yolox.data.data_augment import ValTransform
    from yolox.utils import postprocess

    img = cv2.imread(str(image_path))
    height, width = img.shape[:2]
    ratio = min(TEST_SIZE[0] / height, TEST_SIZE[1] / width)

    preproc = ValTransform(legacy=False)
    processed, _ = preproc(img, None, TEST_SIZE)
    tensor = torch.from_numpy(processed).unsqueeze(0).float()

    with torch.no_grad():
        outputs = model(tensor)
        outputs = postprocess(outputs, num_classes=3, conf_thre=0.01, nms_thre=0.65, class_agnostic=True)

    results = []
    output = outputs[0]
    if output is None:
        return results
    for det in output.cpu().numpy():
        x1, y1, x2, y2, obj_conf, cls_conf, cls_idx = det
        results.append(
            {
                "cls_idx": int(cls_idx),
                "confidence": float(obj_conf * cls_conf),
                "box": (x1 / ratio, y1 / ratio, x2 / ratio, y2 / ratio),
            }
        )
    return results


def main() -> None:
    print("Candidate: YOLOX-Nano fine-tuned (licence-clean production baseline)")
    model = load_model()

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
        raw_detections = infer(model, image_path)
        latencies_ms.append((time.perf_counter() - start) * 1000)

        gt_boxes = [dict(item) for item in gt_by_image.get(image_id, [])]
        predictions = []
        for item in raw_detections:
            total_detections += 1
            label = LABEL_MAP.get(item["cls_idx"])
            if label is None:
                unmapped_detections += 1
                continue
            if item["confidence"] < CONF_THRESHOLD:
                below_threshold_would_be += 1
                continue
            predictions.append({"label": label, "box": item["box"], "confidence": item["confidence"]})

        image_had_failure = False
        for prediction in predictions:
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
    print(f"Unmapped-label detections (person class; excluded, no ground truth available): {unmapped_detections}")
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
