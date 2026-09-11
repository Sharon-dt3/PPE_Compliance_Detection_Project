# PPE detection benchmarks

`run_ppe_benchmark.py` scores a candidate PPE detection model against a labeled, held-out
test set and prints class-level precision/recall/F1, confidence calibration, latency, and
unmapped-label counts — never a single headline accuracy number. See
[Model evaluation and licensing trail](../../kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-model-evaluation.md)
for the current results and how to read them.

## Running it

Requires the same dependencies as real inference (`ultralytics`, `huggingface_hub` — already
in `requirements.txt`).

```bash
cd backend
. .venv/bin/activate

python -c "
from huggingface_hub import hf_hub_download
import zipfile
path = hf_hub_download(repo_id='keremberke/hard-hat-detection', filename='data/test.zip', repo_type='dataset')
zipfile.ZipFile(path).extractall('benchmarks/data/test')
"

cd benchmarks
python run_ppe_benchmark.py --repo-id melihuzunoglu/ppe-detection
python run_ppe_benchmark.py --repo-id Hansung-Cho/yolov8-ppe-detection
```

`run_roboflow_benchmark.py` scores the Roboflow-hosted `construction-site-safety/27` model
the same way, against the same 300-image sample. It calls a live hosted inference API
instead of running weights locally, so it needs a Roboflow API key rather than more Python
dependencies:

```bash
cd backend
. .venv/bin/activate
set -a; source .env; set +a   # loads ROBOFLOW_API_KEY from backend/.env -- never commit this key
cd benchmarks
python run_roboflow_benchmark.py
```

`run_yolox_benchmark.py` scores the in-house fine-tuned YOLOX-Nano checkpoint (the
licence-clean production-route candidate) the same way, on the same 300-image sample:

```bash
cd backend/benchmarks
PYTHONPATH="$(pwd)/yolox_finetune" ../.venv/bin/python run_yolox_benchmark.py
```

Requires the fine-tuned checkpoint to already exist at
`yolox_finetune/YOLOX_outputs/ppe_nano/best_ckpt.pth` — see "Fine-tuning YOLOX" below to
produce one. Its classes are `helmet`/`person`/`head` (not `helmet`/`no_helmet`); `head` is
mapped to `no_helmet` for this comparison since it is the no-helmet signal, and `person` is
excluded (no ground truth available, same as every other candidate's non-helmet classes).

`run_workspace_safety_benchmark.py` scores `hafizqaim/Workspace-Safety-Detection-using-YOLOv8`
the same way. Its weights aren't published on Hugging Face Hub — they're attached to a GitHub
Release — so this script downloads and caches them directly rather than using
`hf_hub_download`:

```bash
cd backend/benchmarks
../.venv/bin/python run_workspace_safety_benchmark.py
```

`Hexmon/vyra-yolo-ppe-detection` is registered as another `--repo-id` choice in
`run_ppe_benchmark.py` (same Hugging-Face-hosted pattern as the other two candidates there):

```bash
cd backend/benchmarks
python run_ppe_benchmark.py --repo-id Hexmon/vyra-yolo-ppe-detection
```

`compare_candidates.py` reads all six candidates' saved result files and prints them side
by side, plus the actual selection logic applied — run it yourself to verify the reasoning
from real numbers rather than a written claim:

```bash
cd backend/benchmarks
python compare_candidates.py
```

Dated `.txt` files in this directory are raw output from a prior run, kept as evidence for
the evaluation record — they are not regenerated automatically and will go stale as models
or the dataset change. Re-run the script and add a new dated file rather than editing an
existing one.

## Fine-tuning YOLOX

`prepare_voc_dataset.py` downloads a fixed-seed subset of the CC0-licensed
[Voxel51/hard-hat-detection](https://huggingface.co/datasets/Voxel51/hard-hat-detection)
dataset and converts it to the PASCAL VOC layout YOLOX's training code expects:

```bash
cd backend/benchmarks
python prepare_voc_dataset.py
```

Then clone YOLOX, install it (no build isolation, so it can see the already-installed
torch), download the pretrained nano checkpoint, and train:

```bash
cd backend/benchmarks
git clone --depth 1 https://github.com/Megvii-BaseDetection/YOLOX.git yolox_finetune
cd yolox_finetune
../../.venv/bin/pip install -e . --no-deps --no-build-isolation
../../.venv/bin/pip install -q loguru thop ninja tabulate "pycocotools>=2.0.2" tensorboard
curl -sL -o yolox_nano.pth "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.pth"
PYTHONPATH="$(pwd)" ../../.venv/bin/python tools/train.py -f exps/ppe_nano.py -d 0 -b 8 -c yolox_nano.pth --logger tensorboard
```

Deliberately **not** installing `opencv_python` from YOLOX's own `requirements.txt` --
it conflicts with this project's pinned `opencv-python-headless` and will silently break
the mandatory face-blur privacy gate (see the top-level README's setup note). `yolox_finetune/`
is git-ignored (a full third-party clone plus checkpoints and training images, ~280MB) --
`exps/ppe_nano.py` is the actual training config and is worth keeping a copy of outside
that directory if you want to preserve it across a re-clone.

YOLOX's official training code assumes CUDA is always available -- on a CPU-only machine
this repo's `yolox/core/trainer.py`, `yolox/data/data_prefetcher.py`,
`yolox/exp/yolox_base.py`, `yolox/evaluators/voc_evaluator.py`,
`yolox/evaluators/coco_evaluator.py`, and `yolox/models/yolo_head.py` all needed patching to
fall back to CPU. `yolox/utils/model_utils.py`'s FLOPs logging needed a fallback too (`thop`
imports the `distutils` module Python 3.12 removed). `yolox/data/datasets/voc_classes.py`
needs its class list replaced with the dataset's real 3 classes (`helmet`, `person`, `head`)
-- it defaults to the 20-class Pascal VOC list. Mosaic augmentation
(`exps/ppe_nano.py`'s `mosaic_prob`) had to be disabled rather than patched further -- a
PyTorch-version type incompatibility in `MosaicDetection`'s output breaks batch collation.
`yolox/data/datasets/voc.py`'s `_write_voc_results_file` has a `dets == []` numpy comparison
bug that crashes on the first non-empty detection during periodic evaluation; fixed to
`len(dets) == 0`. None of this is specific to this dataset -- expect to hit the same issues
fine-tuning any VOC-format YOLOX experiment on a CPU-only machine.

## Known scope limits

- The `keremberke/hard-hat-detection` ground truth only labels `hardhat`/`no-hardhat`
  (mapped to `helmet`/`no_helmet`). It does not cover `vest`, `gloves`, or `glasses` — a
  candidate's accuracy on those classes is unvalidated by this benchmark.
- No camera-angle metadata exists in this dataset, so it cannot validate performance against
  Renewi's high-mounted, wide-angle CCTV specifically; that requires real site footage.
- `--repo-id` only accepts candidates registered in `CANDIDATES` in the script. Add an entry
  there (repo id, weights filename, raw-label-to-normalized-label map) to benchmark another
  Hugging-Face-hosted candidate.
- The Roboflow hosted API infers at a fixed internal resolution (640×640 for this model) and
  returns coordinates in that space; `run_roboflow_benchmark.py` rescales them back to each
  image's original pixel dimensions before IoU matching. Its reported latency is a network
  round trip, not comparable to the two locally-run candidates' CPU inference time. The
  Roboflow API also requires an explicit `Content-Type: application/x-www-form-urlencoded`
  header on the request body -- `requests` does not set this automatically for raw bytes the
  way `curl -d` does, and the API returns an otherwise-unhelpful 400 without it.
