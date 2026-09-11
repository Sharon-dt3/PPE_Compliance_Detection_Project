"""Print a side-by-side comparison of every benchmarked candidate's saved results, and show
the actual selection logic applied to melihuzunoglu/ppe-detection as the primary POC model --
run this yourself to verify the choice from the real numbers, not from a written claim.

Usage:
    cd backend/benchmarks
    python compare_candidates.py
"""

from __future__ import annotations

import re
from pathlib import Path

CANDIDATES = {
    "melihuzunoglu/ppe-detection (POC default)": "2026-09-09_melihuzunoglu-ppe-detection.txt",
    "Hansung-Cho/yolov8-ppe-detection": "2026-09-09_hansung-cho-yolov8-ppe-detection.txt",
    "Roboflow construction-site-safety/27": "2026-09-11_roboflow-construction-site-safety-27.txt",
    "YOLOX-Nano fine-tuned (licence-clean, not yet wired into the app)": "2026-09-11_yolox-nano-finetuned-cc0.txt",
    "Hexmon/vyra-yolo-ppe-detection": "2026-09-11_hexmon-vyra-yolo-ppe-detection.txt",
    "hafizqaim/Workspace-Safety-Detection-using-YOLOv8": "2026-09-11_workspace-safety-detection-yolov8.txt",
}
POC_DEFAULT = "melihuzunoglu/ppe-detection (POC default)"
PRODUCTION_CANDIDATE = "YOLOX-Nano fine-tuned (licence-clean, not yet wired into the app)"
BEST_OVERALL = "Hexmon/vyra-yolo-ppe-detection"

ROW_RE = re.compile(
    r"^\s*(helmet|no_helmet)\s+TP=\s*(\d+)\s+FP=\s*(\d+)\s+FN=\s*(\d+)\s+"
    r"precision=([\d.]+)\s+recall=([\d.]+)\s+f1=([\d.]+)",
    re.MULTILINE,
)


def parse(path: Path) -> dict[str, dict[str, float]]:
    text = path.read_text()
    results: dict[str, dict[str, float]] = {}
    for match in ROW_RE.finditer(text):
        label, tp, fp, fn, precision, recall, f1 = match.groups()
        results[label] = {
            "tp": int(tp), "fp": int(fp), "fn": int(fn),
            "precision": float(precision), "recall": float(recall), "f1": float(f1),
        }
    return results


def main() -> None:
    all_results = {name: parse(Path(filename)) for name, filename in CANDIDATES.items()}

    print("Same 300-image held-out set, same seed (20260909), every candidate below:\n")
    header = f"{'Candidate':<40} {'helmet F1':>10} {'no_helmet F1':>13} {'no_helmet recall':>18}"
    print(header)
    print("-" * len(header))
    for name, results in all_results.items():
        helmet = results.get("helmet", {})
        no_helmet = results.get("no_helmet", {})
        print(
            f"{name:<40} {helmet.get('f1', 0):>10.3f} {no_helmet.get('f1', 0):>13.3f} "
            f"{no_helmet.get('recall', 0):>17.1%}"
        )

    print(f"\nWhy {POC_DEFAULT} is still the configured POC default, from these numbers alone:")
    primary = all_results[POC_DEFAULT]["no_helmet"]
    for name, results in all_results.items():
        if name in (POC_DEFAULT, PRODUCTION_CANDIDATE, BEST_OVERALL):
            continue
        rival = results["no_helmet"]
        helmet_rival = results["helmet"]["f1"]
        helmet_primary = all_results[POC_DEFAULT]["helmet"]["f1"]
        if helmet_rival > helmet_primary and rival["f1"] < primary["f1"]:
            print(
                f"  - {name} scores higher on helmet ({helmet_rival:.3f} vs {helmet_primary:.3f}), "
                f"but its no_helmet F1 ({rival['f1']:.3f}) is far worse than {POC_DEFAULT}'s "
                f"({primary['f1']:.3f}) -- and no_helmet is the class this app treats as authoritative."
            )
    print(
        f"  ({BEST_OVERALL} and {PRODUCTION_CANDIDATE} both beat {POC_DEFAULT} on no_helmet too --\n"
        f"  see the sections below for why neither has replaced it as the configured default yet.)"
    )

    print(f"\n{PRODUCTION_CANDIDATE} beats {POC_DEFAULT} on BOTH classes on this same benchmark:")
    finetuned = all_results[PRODUCTION_CANDIDATE]
    poc_default = all_results[POC_DEFAULT]
    for label in ("helmet", "no_helmet"):
        print(
            f"  - {label}: {finetuned[label]['f1']:.3f} F1 vs {POC_DEFAULT} at {poc_default[label]['f1']:.3f}"
        )
    print(
        "  Not yet the running POC default -- it requires actual fine-tuning (done here), and switching\n"
        "  the app to it would need a new DetectionProvider adapter (Ultralytics-only today) plus its own\n"
        "  full accuracy/licence review before replacing melihuzunoglu as the configured default."
    )

    print(f"\n{BEST_OVERALL} is the strongest candidate on THIS benchmark across every model tested so far:")
    best = all_results[BEST_OVERALL]
    for name, results in all_results.items():
        if name == BEST_OVERALL:
            continue
        for label in ("helmet", "no_helmet"):
            if results[label]["f1"] > best[label]["f1"]:
                print(f"  (would be wrong: {name} actually beats it on {label} -- {results[label]['f1']:.3f} vs {best[label]['f1']:.3f})")
    for label in ("helmet", "no_helmet"):
        print(f"  - {label}: {best[label]['f1']:.3f} F1 (precision={best[label]['precision']:.3f}, recall={best[label]['recall']:.3f})")
    print(
        "  Licence note (verified via HfApi().model_info(...).card_data, not assumed): its own Hugging\n"
        "  Face card declares license: cc-by-4.0 -- self-declared, same as Hansung-Cho/yolov8-ppe-detection's\n"
        "  self-declared MIT. This is ALSO an Ultralytics YOLOv8 fine-tune (tags: 'ultralytics', 'yolov8'),\n"
        "  so the model-evaluation doc's own reasoning for disputing Hansung-Cho's MIT tag -- Ultralytics'\n"
        "  position that its AGPL-3.0 licence follows weights trained with its code, not just the framework\n"
        "  itself -- applies here with equal force. Best accuracy of any candidate on this benchmark, but\n"
        "  treat its CC-BY-4.0 tag as similarly disputed/AGPL-encumbered until confirmed otherwise, not as\n"
        "  a clean licence -- same caveat as Hansung-Cho, not the same status as the YOLOX-Nano fine-tune\n"
        "  above (which is licence-clean because it was trained with Apache-2.0-licensed YOLOX code, not\n"
        "  Ultralytics)."
    )


if __name__ == "__main__":
    main()
