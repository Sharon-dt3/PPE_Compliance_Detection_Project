"""Trace exactly why SafetyDecisionService assigns each detected person compliant/violation/
unknown for one image -- runs the real model to get real boxes, then feeds them straight
into the actual production matching code (app/decision.py), printing every (person,
candidate) containment score along the way so a mismatch is visible, not just its outcome.

Usage:
    cd backend
    . .venv/bin/activate
    python benchmarks/trace_matching.py <path-to-image>
"""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from huggingface_hub import hf_hub_download
from ultralytics import YOLO

from app.decision import SafetyDecisionService
from app.detection import BoundingBox, DetectedObject

CONF_THRESHOLD = 0.25
LABELS = {
    "human": "person",
    "person": "person",
    "helmet": "helmet",
    "no-helmet": "no_helmet",
    "vest": "vest",
    "no-vest": "no_vest",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path")
    args = parser.parse_args()

    model_path = hf_hub_download(repo_id="melihuzunoglu/ppe-detection", filename="best.pt")
    model = YOLO(model_path)
    result = model.predict(source=args.image_path, conf=CONF_THRESHOLD, verbose=False)[0]
    names = result.names

    objects: list[DetectedObject] = []
    for box in result.boxes:
        raw_label = str(names[int(box.cls[0])]).lower()
        label = LABELS.get(raw_label)
        if label is None:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        objects.append(DetectedObject(label, float(box.conf[0]), BoundingBox(x1, y1, x2, y2)))
    objects_t = tuple(objects)

    people = [o for o in objects if o.label == "person"]
    helmets = [o for o in objects if o.label == "helmet"]
    print(f"Real detections: {len(people)} person, {len(helmets)} helmet\n")

    policy = SimpleNamespace(
        helmet_required=True,
        vest_required=True,
        confidence_threshold=CONF_THRESHOLD,
        class_confidence_thresholds={},
    )

    # Show every (person, helmet) containment score the real matching code would compute --
    # this is the exact same private method the production decision logic uses internally.
    service = SafetyDecisionService()
    region = service._REGIONS["helmet"]
    print("Containment scores (person index -> helmet index): score, or None if ineligible")
    print(f"(helmet region = top {region[0]*100:.0f}%-{region[1]*100:.0f}% of person box height, "
          f"min containment {service._MIN_CONTAINMENT_RATIO}, min relative area {service._MIN_RELATIVE_PPE_AREA})\n")
    for pi, person in enumerate(people):
        for hi, helmet in enumerate(helmets):
            score = service._containment_score(person, helmet, region)
            flag = "" if score is None else (" <- MEETS 0.5 THRESHOLD" if score >= 0.5 else " (below threshold)")
            print(f"  person[{pi}] (conf={person.confidence:.2f}) vs helmet[{hi}] (conf={helmet.confidence:.2f}): "
                  f"{'None' if score is None else round(score, 3)}{flag}")

    print("\nFinal per-person state from the real decision engine (person_states_for_frame):")
    states = service.person_states_for_frame(objects_t, policy)
    for pi, state in enumerate(states):
        label = "COMPLIANT" if state.compliant is True else ("VIOLATION: " + str(state.failed_requirement) if state.compliant is False else "UNKNOWN")
        print(f"  person[{pi}] (conf={people[pi].confidence:.2f}): {label}")


if __name__ == "__main__":
    main()
