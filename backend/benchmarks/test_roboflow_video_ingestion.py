"""End-to-end test of the video-ingestion path against Roboflow's hosted inference API --
the part of Section 5 ("Verified video sources") that had never been exercised this session
because it depends on Supabase auth to reach the live app's upload UI, and Supabase has been
down for hours (confirmed platform-side "Unresponsive Projects" incident).

This does not need Supabase, the FastAPI app, or the database at all: it exercises the exact
same video-decoding step our own pipeline uses (app/detection.py's `cv2.VideoCapture` +
frame-stride sampling) directly against a real video file, then sends each sampled frame to
Roboflow's hosted API the same way run_roboflow_benchmark.py already proved works -- so it is
a faithful test of "does a video file survive frame extraction and reach a real PPE detection
API and get parsed back into per-frame detections", which is what video ingestion actually is
in this application. It does not exercise the FastAPI upload endpoint, auth, or database
persistence layers -- those still need the live app once Supabase is reachable again.

Usage:
    cd backend
    . .venv/bin/activate
    set -a; source .env; set +a
    python benchmarks/test_roboflow_video_ingestion.py /tmp/ppe_video_test.mp4
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
from pathlib import Path

import cv2
import requests

MODEL_ID = "construction-site-safety/27"
API_BASE = "https://serverless.roboflow.com"
TARGET_SAMPLING_FPS = 2.0  # mirrors a typical ZonePolicy.sampling_fps value


def resolve_vid_stride(media_path: str, sampling_fps: float) -> int:
    """Same logic as app/detection.py's _resolve_vid_stride."""
    capture = cv2.VideoCapture(media_path)
    try:
        native_fps = capture.get(cv2.CAP_PROP_FPS)
    finally:
        capture.release()
    if not native_fps or native_fps <= 0:
        return 1
    return max(1, round(native_fps / sampling_fps))


def predict(image_bytes: bytes, api_key: str) -> dict:
    encoded = base64.b64encode(image_bytes)
    response = requests.post(
        f"{API_BASE}/{MODEL_ID}",
        data=encoded,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        raise SystemExit("ROBOFLOW_API_KEY is not set. Run: set -a; source .env; set +a")

    video_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ppe_video_test.mp4"
    if not Path(video_path).is_file():
        raise SystemExit(f"Video file not found: {video_path}")

    print(f"Video file: {video_path}")
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise SystemExit("cv2.VideoCapture failed to open the file -- codec or container unsupported")

    native_fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Decoded metadata: {frame_count} frames, {native_fps:.1f} native fps, {width}x{height}")

    stride = resolve_vid_stride(video_path, TARGET_SAMPLING_FPS)
    print(f"Sampling at {TARGET_SAMPLING_FPS} fps target -> stride={stride} (every {stride}th frame)")

    sampled_frame_indices = []
    api_results = []
    index = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % stride == 0:
                sampled_frame_indices.append(index)
                frame_path = Path(tmp_dir) / f"frame_{index:04d}.jpg"
                cv2.imwrite(str(frame_path), frame)

                start = time.perf_counter()
                try:
                    payload = predict(frame_path.read_bytes(), api_key)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    detections = payload.get("predictions", [])
                    print(f"  frame {index:4d}: API OK in {elapsed_ms:.0f}ms, {len(detections)} raw detection(s)")
                    api_results.append({"frame": index, "ok": True, "detections": len(detections)})
                except requests.RequestException as error:
                    print(f"  frame {index:4d}: API ERROR -- {error}")
                    api_results.append({"frame": index, "ok": False, "error": str(error)})
            index += 1
    capture.release()

    print()
    print(f"Sampled {len(sampled_frame_indices)} of {frame_count} decoded frames")
    successes = sum(1 for result in api_results if result["ok"])
    print(f"Roboflow API calls: {successes}/{len(api_results)} succeeded")
    total_detections = sum(result.get("detections", 0) for result in api_results if result["ok"])
    print(f"Total raw detections across all sampled frames: {total_detections}")
    if successes == len(api_results) and api_results:
        print(
            "\nPASS: video decode -> frame sampling -> hosted PPE inference API -> parsed response "
            "all completed end to end with zero errors."
        )
    else:
        print("\nFAIL: at least one sampled frame did not get a successful API response.")


if __name__ == "__main__":
    main()
