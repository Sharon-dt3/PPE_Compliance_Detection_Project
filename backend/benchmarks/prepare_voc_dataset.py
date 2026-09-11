"""Convert a subset of Voxel51/hard-hat-detection (CC0 1.0, mirrored from the Kaggle
Safety Helmet Detection dataset) into the PASCAL VOC2007 layout YOLOX's VOCDetection
loader expects, for fine-tuning YOLOX-Nano as the licence-clean production baseline.

Uses a fixed-seed random subset (not the full 5,000 images) to keep both download size
and CPU training time tractable in this environment -- the same "representative sample"
principle already used for the detection benchmarks, applied here to training data.

Usage:
    cd backend/benchmarks
    python prepare_voc_dataset.py
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree

from huggingface_hub import hf_hub_download

RANDOM_SEED = 20260911
TRAIN_SIZE = 600
VAL_SIZE = 150

REPO_ID = "Voxel51/hard-hat-detection"
VOC_ROOT = Path("yolox_finetune/datasets/VOCdevkit/VOC2007")
LABEL_MAP = {"helmet": "helmet", "person": "person", "head": "head"}


def build_voc_xml(filename: str, width: int, height: int, detections: list[dict]) -> Element:
    """Build one PASCAL VOC annotation XML matching YOLOX's VOCDetection parser."""
    root = Element("annotation")
    SubElement(root, "folder").text = "VOC2007"
    SubElement(root, "filename").text = filename
    size = SubElement(root, "size")
    SubElement(size, "width").text = str(width)
    SubElement(size, "height").text = str(height)
    SubElement(size, "depth").text = "3"
    SubElement(root, "segmented").text = "0"

    for det in detections:
        label = LABEL_MAP.get(det["label"])
        if label is None:
            continue
        x, y, w, h = det["bounding_box"]
        xmin, ymin = x * width, y * height
        xmax, ymax = (x + w) * width, (y + h) * height
        xmin, ymin = max(0.0, xmin), max(0.0, ymin)
        xmax, ymax = min(float(width), xmax), min(float(height), ymax)
        if xmax <= xmin or ymax <= ymin:
            continue

        obj = SubElement(root, "object")
        SubElement(obj, "name").text = label
        SubElement(obj, "pose").text = "Unspecified"
        SubElement(obj, "truncated").text = str(int(det.get("truncated", 0)))
        SubElement(obj, "difficult").text = str(int(det.get("difficult", 0)))
        bnd = SubElement(obj, "bndbox")
        SubElement(bnd, "xmin").text = str(int(round(xmin)))
        SubElement(bnd, "ymin").text = str(int(round(ymin)))
        SubElement(bnd, "xmax").text = str(int(round(xmax)))
        SubElement(bnd, "ymax").text = str(int(round(ymax)))
    return root


def main() -> None:
    print("Downloading samples.json (annotations)...")
    samples_path = hf_hub_download(repo_id=REPO_ID, filename="samples.json", repo_type="dataset")
    samples = json.load(open(samples_path))["samples"]
    print(f"Total available: {len(samples)}")

    random.Random(RANDOM_SEED).shuffle(samples)
    train_samples = samples[:TRAIN_SIZE]
    val_samples = samples[TRAIN_SIZE:TRAIN_SIZE + VAL_SIZE]
    print(f"Using {len(train_samples)} for train, {len(val_samples)} for val (seed {RANDOM_SEED})")

    for d in ["JPEGImages", "Annotations"]:
        (VOC_ROOT / d).mkdir(parents=True, exist_ok=True)
    (VOC_ROOT / "ImageSets" / "Main").mkdir(parents=True, exist_ok=True)

    trainval_ids: list[str] = []
    test_ids: list[str] = []

    for split_name, split_samples, id_list in (
        ("train", train_samples, trainval_ids),
        ("val", val_samples, test_ids),
    ):
        for i, sample in enumerate(split_samples):
            src_filename = Path(sample["filepath"]).name  # e.g. hard_hat_workers123.png
            image_id = f"{split_name}_{i:05d}"
            local_path = hf_hub_download(
                repo_id=REPO_ID, filename=sample["filepath"], repo_type="dataset"
            )
            dest_jpg = VOC_ROOT / "JPEGImages" / f"{image_id}.jpg"
            if not dest_jpg.exists():
                shutil.copy(local_path, dest_jpg)

            width = sample["metadata"]["width"]
            height = sample["metadata"]["height"]
            xml_root = build_voc_xml(f"{image_id}.jpg", width, height, sample["ground_truth"]["detections"])
            ElementTree(xml_root).write(VOC_ROOT / "Annotations" / f"{image_id}.xml")
            id_list.append(image_id)

            if (i + 1) % 100 == 0:
                print(f"  {split_name}: {i + 1}/{len(split_samples)}")

    (VOC_ROOT / "ImageSets" / "Main" / "trainval.txt").write_text("\n".join(trainval_ids) + "\n")
    (VOC_ROOT / "ImageSets" / "Main" / "test.txt").write_text("\n".join(test_ids) + "\n")

    print(f"\nDone. {len(trainval_ids)} train images, {len(test_ids)} val images.")
    print(f"Classes present: {sorted(LABEL_MAP.values())}")


if __name__ == "__main__":
    main()
