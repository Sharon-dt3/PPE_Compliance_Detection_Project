# Face-detector model — source, licence, and checksums

This directory bundles the mandatory OpenCV DNN face-detector model used by the fail-closed
evidence privacy gate (see [Technology Decisions §1](../../kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-technology-decisions.md)
and `app/evidence.py`). Recorded here per that decision's requirement that model artifacts be
versioned with their source, checksum, and licence in the model/dependency register before
deployment.

## Model

SSD (Single Shot Detector) object detector with a ResNet-10 backbone, trained for face
detection, released by the OpenCV project on 2018-02-05 (fp16-quantized Caffe weights). This
is the same reference face detector used throughout OpenCV's own DNN sample documentation
and is one of the two models OpenCV's own sample pipeline can select (fp16 Caffe or uint8
TensorFlow) — this project uses the fp16 Caffe variant to match the exact
`cv2.dnn.readNetFromCaffe` code path the technology decision specifies.

## Source

| File | Upstream location |
| --- | --- |
| `deploy.prototxt` | [github.com/opencv/opencv/blob/master/samples/dnn/face_detector/deploy.prototxt](https://github.com/opencv/opencv/blob/master/samples/dnn/face_detector/deploy.prototxt) |
| `res10_300x300_ssd_iter_140000_fp16.caffemodel` | [github.com/opencv/opencv_3rdparty/blob/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel](https://github.com/opencv/opencv_3rdparty/blob/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel) |

Both files are referenced as the canonical download targets by OpenCV's own
[`samples/dnn/face_detector/weights.meta4`](https://github.com/opencv/opencv/blob/master/samples/dnn/face_detector/weights.meta4)
metalink manifest, which is how this checksum below was obtained and cross-checked — not
asserted independently of the upstream project.

## Licence

The main `opencv/opencv` repository (which owns `deploy.prototxt` and the reference
pipeline that selects these weights) is licensed **Apache License 2.0**. The weights
themselves live in the separate `opencv/opencv_3rdparty` repository, which does not carry
its own distinct licence file recognized by GitHub's licence detector; it is an official,
OpenCV-project-maintained artifact repository whose sole purpose is serving the exact binary
weights the Apache-2.0-licensed main repository's own samples and metalink manifest
reference and download. Treat the weights as bound to the same terms as the pipeline that
publishes and distributes them (Apache License 2.0) unless OpenCV states otherwise.

## Checksums (verified against OpenCV's own manifest)

| File | Algorithm | Checksum |
| --- | --- | --- |
| `res10_300x300_ssd_iter_140000_fp16.caffemodel` | SHA-1 (OpenCV's own, from `weights.meta4`) | `31fc22bfdd907567a04bb45b7cfad29966caddc1` |
| `res10_300x300_ssd_iter_140000_fp16.caffemodel` | SHA-256 (computed here, for future re-verification) | `510ffd2471bd81e3fcc88a5beb4eae4fb445ccf8333ebc54e7302b83f4158a76` |
| `deploy.prototxt` | SHA-256 (computed here; byte-identical to the current upstream file) | `dcd661dc48fc9de0a341db1f666a2164ea63a67265c7f779bc12d6b3f2fa67e9` |

Verified 2026-09-11: the bundled `.caffemodel`'s SHA-1 matches OpenCV's own published
checksum exactly, and `deploy.prototxt` is byte-for-byte identical to the current upstream
file at the URL above.

```bash
shasum -a 1 res10_300x300_ssd_iter_140000_fp16.caffemodel    # matches OpenCV's weights.meta4
shasum -a 256 res10_300x300_ssd_iter_140000_fp16.caffemodel
shasum -a 256 deploy.prototxt
```

## Known limitation (already tracked)

Per the technology decision, this detector's recall against representative high-angle CCTV
imagery is unvalidated and must be checked before any live pilot; a stronger face detector
must be selected in Phase 2 if this one's recall proves insufficient on real site footage.
