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

Dated `.txt` files in this directory are raw output from a prior run, kept as evidence for
the evaluation record — they are not regenerated automatically and will go stale as models
or the dataset change. Re-run the script and add a new dated file rather than editing an
existing one.

## Known scope limits

- The `keremberke/hard-hat-detection` ground truth only labels `hardhat`/`no-hardhat`
  (mapped to `helmet`/`no_helmet`). It does not cover `vest`, `gloves`, or `glasses` — a
  candidate's accuracy on those classes is unvalidated by this benchmark.
- No camera-angle metadata exists in this dataset, so it cannot validate performance against
  Renewi's high-mounted, wide-angle CCTV specifically; that requires real site footage.
- `--repo-id` only accepts candidates registered in `CANDIDATES` in the script. Add an entry
  there (repo id, weights filename, raw-label-to-normalized-label map) to benchmark another
  Hugging-Face-hosted candidate. A Roboflow-hosted candidate needs a different adapter (an
  HTTP call, not `hf_hub_download`+`YOLO`) — not implemented here.
