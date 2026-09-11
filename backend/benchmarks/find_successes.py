"""List images from the benchmark's 300-image sample where melihuzunoglu/ppe-detection
correctly matched a real helmet or no_helmet ground-truth box (a true positive) -- the
success cases, as a counterpart to the "representative failure cases" the main benchmark
script already reports.

Usage:
    cd backend
    . .venv/bin/activate
    cd benchmarks
    python find_successes.py
"""

from __future__ import annotations

import random

from huggingface_hub import hf_hub_download
from ultralytics import YOLO

from run_ppe_benchmark import CANDIDATES, CONF_THRESHOLD, IOU_THRESHOLD, RANDOM_SEED, SAMPLE_SIZE, DATA_DIR, iou, load_ground_truth


def main() -> None:
    candidate = CANDIDATES["melihuzunoglu/ppe-detection"]
    label_map = candidate["label_map"]

    images, gt_by_image = load_ground_truth()
    eligible_image_ids = [image_id for image_id in images if gt_by_image.get(image_id)]
    random.Random(RANDOM_SEED).shuffle(eligible_image_ids)
    sample_ids = eligible_image_ids[:SAMPLE_SIZE]

    model_path = hf_hub_download(repo_id="melihuzunoglu/ppe-detection", filename=str(candidate["filename"]))
    model = YOLO(model_path)

    clean_successes = []  # every ground-truth box in the image was correctly matched, zero FP/FN
    partial_successes = []  # at least one TP, but not a perfectly clean image

    for image_id in sample_ids:
        image_meta = images[image_id]
        image_path = DATA_DIR / image_meta["file_name"]
        if not image_path.is_file():
            continue

        result = model.predict(source=str(image_path), conf=CONF_THRESHOLD, verbose=False)[0]
        gt_boxes = [dict(item) for item in gt_by_image.get(image_id, [])]
        names = result.names

        predictions = []
        for box in result.boxes:
            raw_label = str(names[int(box.cls[0])]).lower()
            confidence = float(box.conf[0])
            label = label_map.get(raw_label)
            if label is None or confidence < CONF_THRESHOLD:
                continue
            predictions.append({"label": label, "box": tuple(box.xyxy[0].tolist()), "confidence": confidence})

        tp_details = []
        had_fp = False
        for prediction in predictions:
            if prediction["label"] not in ("helmet", "no_helmet"):
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
                tp_details.append(f"{prediction['label']} @ {prediction['confidence']:.2f}")
            else:
                had_fp = True

        had_fn = any(not gt["matched"] for gt in gt_boxes)

        if tp_details and not had_fp and not had_fn:
            clean_successes.append((image_meta["file_name"], tp_details))
        elif tp_details:
            partial_successes.append((image_meta["file_name"], tp_details))

    print(f"Clean successes (every ground-truth box matched, zero false positives/negatives): {len(clean_successes)}")
    for filename, details in clean_successes:
        print(f"  {filename}  ->  {', '.join(details)}")

    print(f"\nPartial successes (at least one correct match, but the image also had a miss or a wrong guess): {len(partial_successes)}")
    for filename, details in partial_successes[:10]:
        print(f"  {filename}  ->  {', '.join(details)}")


if __name__ == "__main__":
    main()
