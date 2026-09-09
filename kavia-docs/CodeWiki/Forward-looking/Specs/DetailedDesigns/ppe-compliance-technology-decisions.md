[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — Technology Decisions

## Purpose

This record resolves two implementation choices required by the PPE Compliance Detection POC blueprint: mandatory face blurring for evidence and dashboard charting. These decisions apply to the POC unless a later approved architecture decision supersedes them.

## Decision 1 — Evidence face detection and blurring

### Chosen implementation

Use the OpenCV DNN face detector based on the ResNet SSD Caffe model:

- `deploy.prototxt`
- `res10_300x300_ssd_iter_140000_fp16.caffemodel`

The media worker must run this detector on every candidate evidence frame. It must apply a Gaussian blur to each detected face region before the frame is annotated, stored, displayed, downloaded, or made accessible through an API.

### Evidence-processing sequence

```text
Persistent PPE violation
  → select evidence frame
  → detect faces with OpenCV DNN ResNet SSD
  → validate face-detection execution
  → Gaussian-blur every detected face bounding box
  → draw PPE and compliance annotations
  → store private blurred evidence only
  → permit authorized evidence view
```

The system must not store or render an original, unblurred evidence frame in the evidence workflow.

### Failure behavior

Face blurring is a hard privacy gate, not a best-effort enhancement:

1. If the face detector cannot load, errors, or returns an invalid result, mark evidence generation as failed.
2. Do not create an accessible evidence object or evidence URL.
3. Preserve the non-identifying alert metadata and aggregate metric result.
4. Record an audit and operational error event.
5. Display a safe reviewer message stating that evidence is unavailable because privacy processing did not complete.
6. Make the media job or relevant event eligible for controlled retry after the fault is corrected.

A successful blur pipeline must set `evidence_snapshot.blurred = true`; the application must reject any evidence record where that value is not true.

### Configuration

Configure, rather than hard-code:

- model file locations;
- DNN confidence threshold;
- padding applied around face boxes;
- Gaussian blur kernel or derived blur strength;
- maximum evidence image dimensions;
- worker timeout and retry policy;
- private evidence retention period.

The model artifacts must be versioned and their source, checksum, and licence recorded in the model/dependency register before deployment.

### Limitations and Phase-2 review

This POC detector is intended to make privacy protection explicit and enforceable in the demonstrator. It must be validated against representative high-angle CCTV imagery before a live pilot. If its recall is insufficient for approved site footage, Phase 2 must select and validate a stronger face-detection model before enabling evidence collection.

## Decision 2 — Dashboard charting library

### Chosen implementation

Use **Apache ECharts** in the React and TypeScript frontend. Do not add Recharts to the project.

### Rationale

Apache ECharts is selected because it supports the POC’s dashboard requirements with one flexible visualization library:

- compliance-rate time-series trend lines;
- compliant, non-compliant, and unknown count breakdowns;
- stacked bars by camera, zone, shift, and PPE rule;
- alert-state and acknowledgement-time visuals;
- responsive resizing and accessible chart alternatives;
- future extension to more complex aggregate reporting without a chart-library migration.

### Required dashboard charts

| Dashboard need | Apache ECharts visualization |
|---|---|
| Compliance performance over time | Line chart with visible observation and unknown-count context. |
| PPE state by zone/source/shift | Stacked bar chart for compliant, non-compliant, and unknown observations. |
| PPE-rule composition | Bar or donut chart with numeric table alternative. |
| Alert volume and lifecycle state | Stacked bar chart by interval and event state. |
| Acknowledgement/resolution duration | Bar chart or percentile summary, depending on demonstrated data volume. |

Every chart must also provide a textual value/table alternative and must not convey status using color alone.

## Consequences for delivery

- **Day 1:** include Apache ECharts in the frontend foundation and load/verify the OpenCV DNN face model in the worker environment.
- **Day 4:** implement the privacy-gated evidence pipeline before enabling any evidence viewer, then build the dashboard with Apache ECharts.
- **Validation:** add a test that evidence generation fails closed when face processing fails and a UI test that the dashboard renders unknown observations alongside headline compliance rates.
