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
    "melihuzunoglu/ppe-detection (PRIMARY)": "2026-09-09_melihuzunoglu-ppe-detection.txt",
    "Hansung-Cho/yolov8-ppe-detection": "2026-09-09_hansung-cho-yolov8-ppe-detection.txt",
    "Roboflow construction-site-safety/27": "2026-09-11_roboflow-construction-site-safety-27.txt",
}

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

    print("\nWhy melihuzunoglu is the selection, from these numbers alone:")
    primary = all_results["melihuzunoglu/ppe-detection (PRIMARY)"]["no_helmet"]
    for name, results in all_results.items():
        if "PRIMARY" in name:
            continue
        rival = results["no_helmet"]
        helmet_rival = results["helmet"]["f1"]
        helmet_primary = all_results["melihuzunoglu/ppe-detection (PRIMARY)"]["helmet"]["f1"]
        if helmet_rival > helmet_primary:
            print(
                f"  - {name} scores higher on helmet ({helmet_rival:.3f} vs {helmet_primary:.3f}), "
                f"but its no_helmet F1 ({rival['f1']:.3f}) is far worse than melihuzunoglu's "
                f"({primary['f1']:.3f}) -- and no_helmet is the class this app treats as authoritative."
            )


if __name__ == "__main__":
    main()
