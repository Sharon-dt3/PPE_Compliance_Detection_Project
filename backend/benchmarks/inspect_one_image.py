"""Diagnostic: run the configured PPE model against one image at a near-zero confidence
threshold, so every candidate box the model considered is visible -- not just the ones that
cleared the app's configured 0.25 cutoff. Use this to tell apart "the model saw something
plausible but it got filtered out" from "the model genuinely never proposed anything there".

Usage:
    cd backend
    . .venv/bin/activate
    python benchmarks/inspect_one_image.py <path-to-image> [--repo-id melihuzunoglu/ppe-detection]
"""

from __future__ import annotations

import argparse

from huggingface_hub import hf_hub_download
from ultralytics import YOLO

LOW_CONF = 0.01  # near-zero: show almost everything the model considered, not just what passed 0.25


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path")
    parser.add_argument("--repo-id", default="melihuzunoglu/ppe-detection")
    parser.add_argument("--filename", default="best.pt")
    args = parser.parse_args()

    model_path = hf_hub_download(repo_id=args.repo_id, filename=args.filename)
    model = YOLO(model_path)

    results = model.predict(source=args.image_path, conf=LOW_CONF, verbose=False)
    result = results[0]
    names = result.names

    print(f"\nImage: {args.image_path}")
    print(f"Model: {args.repo_id}/{args.filename}")
    print(f"App's real configured threshold: 0.25  |  Shown here: everything above {LOW_CONF}\n")

    if len(result.boxes) == 0:
        print("The model proposed ZERO boxes of any kind, at any confidence, above 0.01.")
        print("This is a genuine, total miss -- not a threshold issue. The model found nothing at all.")
        return

    print(f"{'Label':<12} {'Confidence':<12} {'Passes app 0.25 cutoff?':<25} Box (x1,y1,x2,y2)")
    print("-" * 90)
    rows = sorted(result.boxes, key=lambda b: float(b.conf[0]), reverse=True)
    for box in rows:
        label = str(names[int(box.cls[0])])
        conf = float(box.conf[0])
        passes = "YES" if conf >= 0.25 else "no (filtered out by the app)"
        coords = tuple(round(v, 1) for v in box.xyxy[0].tolist())
        print(f"{label:<12} {conf:<12.3f} {passes:<25} {coords}")

    annotated = result.plot()
    out_path = "benchmarks/last_inspection_annotated.jpg"
    import cv2

    cv2.imwrite(out_path, annotated)
    print(f"\nAnnotated image (all boxes drawn, any confidence) saved to: {out_path}")


if __name__ == "__main__":
    main()
