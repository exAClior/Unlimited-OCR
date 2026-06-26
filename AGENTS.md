# AGENTS.md — Unlimited-OCR

## What this repo is

Baidu's **Unlimited-OCR** — a vision-language model that converts document images and PDFs into **Markdown with inline LaTeX math**. Successor to DeepSeek-OCR. MIT licensed.

Primary use case here: **converting book PDFs into structured text with preserved equations**, for eventual LaTeX conversion.

## Repository layout

```
infer.py              # Concurrent SGLang batch inference (main script for book conversion)
wheel/                # Patched SGLang wheel (sglang-0.0.0.dev11416+g92e8bb79e)
assets/               # README images only — not code
Unlimited-OCR.pdf     # The paper
README.md             # Full usage docs with code examples
CONTRIBUTING.md       # PR guidelines
```

The model weights live on HuggingFace (`baidu/Unlimited-OCR`), not in this repo.

## Two inference backends

| Backend | When to use | Setup |
|---|---|---|
| **HuggingFace Transformers** | Quick single-page or few-page jobs | `pip install torch transformers pymupdf` — see README |
| **SGLang** (via `infer.py`) | **Book-scale batch processing** — concurrent requests, streaming | Install the local wheel: `uv pip install wheel/sglang-*.whl` + `kernels==0.11.7` + `pymupdf` |

For books, always use `infer.py` with SGLang. It auto-starts the server, converts PDF pages to images, and sends them concurrently.

## Book conversion workflow

### 1. Convert PDF pages

```bash
python infer.py \
    --pdf ./my_book.pdf \
    --output_dir ./outputs \
    --concurrency 8 \
    --image_mode gundam \
    --gpu 0
```

Each page produces a `.md` file in `--output_dir` (e.g., `my_book_page_0001.md`).

### 2. Two image modes — pick the right one

| Mode | `image_size` | `crop_mode` | Best for |
|---|---|---|---|
| `gundam` | 640 | True | **Single pages** with dense text/math; higher quality per page |
| `base` | 1024 | False | Multi-page parsing via `infer_multi` (Transformers API only) |

`infer.py` uses `gundam` by default (one request per page), which is correct for books.

### 3. Output format

Output is **Markdown** with `$...$` / `$$...$$` LaTeX math blocks. It is **not** a compilable `.tex` file. To get `.tex`:

```bash
# Concatenate all pages, then convert
cat outputs/my_book_page_*.md > full_book.md
pandoc full_book.md -o full_book.tex
```

Manual cleanup will be needed for: cross-references, bibliography, figure placement, and custom macros.

## Key parameters

| Parameter | Default | Notes |
|---|---|---|
| `max_length` | 32768 | Context length; increase if pages are very dense |
| `no_repeat_ngram_size` | 35 | Anti-repetition; do not disable |
| `ngram_window` | 128 (single) / 1024 (multi) | Window for ngram dedup |
| `PDF_DPI` | 300 | In `infer.py`; higher = better quality, slower |
| `REQUEST_TIMEOUT` | 1200s | Per-page timeout; increase for very complex pages |

## Environment setup (SGLang path)

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install wheel/sglang-0.0.0.dev11416+g92e8bb79e-py3-none-any.whl
uv pip install kernels==0.11.7 pymupdf==1.27.2.2
```

Requires an NVIDIA GPU with CUDA 12.9. Tested on Python 3.12.3.

## Environment setup (Transformers path)

```bash
pip install torch==2.10.0 torchvision==0.25.0 transformers==4.57.1 \
    Pillow==12.1.1 matplotlib==3.10.8 einops==0.8.2 \
    addict==2.4.0 easydict==1.13 pymupdf==1.27.2.2 psutil==7.2.2
```

## Code style

- **PEP 8**, 4-space indentation.
- Backend-specific code stays in backend-specific modules; general changes go in general modules.
- New code should have unit tests.
- CI runs via GitHub Actions — PRs must pass.

## Gotchas

- **Model download is large.** First run downloads weights from HuggingFace — plan for this.
- **`trust_remote_code=True` is required** for both `AutoModel` and `AutoTokenizer`.
- **`torch_dtype=torch.bfloat16`** — the model expects bfloat16; don't change this.
- **SGLang server startup takes time** (~minutes). `infer.py` waits up to 300s (`SERVER_TIMEOUT`).
- **Temp images from PDF conversion** go to system tmpdir and are not auto-cleaned. For large books, monitor disk space.
- **Figures are extracted as surrounding text descriptions**, not as images or TikZ. You'll need to re-insert figures manually.
- **The `outputs/` and `log/` directories** are gitignored.

<skill>
  <name>easy-ssh</name>
  <description>Use the easy-ssh CLI to run computations on a remote server using local project files. Trigger when the user asks to run code remotely, execute on a server, submit a job, sync files to a remote machine, pull results from a server, check remote job status, or mentions "easy-ssh", "remote run", "server-side", "GPU server", or "cluster". Also trigger when the user has a local project and wants to execute it somewhere with more compute (GPU, RAM, CPU cores).
</description>
  <location>/Users/exaclior/.pi/agent/skills/easy-ssh/SKILL.md</location>
</skill>
