# PySide6 Document Scanner and OCR App

This sample is a Windows desktop OCR application built with PySide6. It combines local document layout detection from PP-DocLayoutV3, multiple OCR backends, and document acquisition from Dynamic Web TWAIN Service.

https://github.com/user-attachments/assets/2c9e03e8-94d6-403a-8faa-af5a5f5a7289

## Features

- Open images from disk, paste from the clipboard, or drag and drop files into the app.
- Detect document layout first and draw overlay regions before full-page OCR finishes.
- Run full-page OCR with one of three backends from the toolbar: `glm-ocr:latest` through Ollama, Windows `oneocr`, or `PaddleOCR`.
- Run PP-DocLayoutV3 layout analysis separately from OCR with the `Layout Analysis` button.
- Click any detected region to trigger lazy OCR for that block only.
- Enumerate TWAIN and WIA scanners through `twain-wia-sane-scanner` and import scanned pages directly into the OCR workflow.
- The `Mode` drop-down is only used by `Ollama (GLM-OCR)` and is hidden for the other OCR engines.

## Architecture

1. `Layout Analysis` runs PP-DocLayoutV3 locally and draws layout boxes without OCR.
2. `Run OCR` sends the full image to the selected OCR engine.
3. `OneOCR` and `PaddleOCR` can replace the current overlay with OCR-native text coordinates.
4. If an OCR engine does not return coordinates, the existing layout overlay stays unchanged.
5. Users can click interactive text/table/formula regions to OCR only that cropped block.
6. Scanner pages are saved as images and added to the same OCR queue.

## Prerequisites

- Windows with Python 3.11 or later.
- Ollama installed and running locally if you want to use the `Ollama (GLM-OCR)` backend.
- The `glm-ocr:latest` model pulled into Ollama if you want to use the Ollama backend.
- Dynamic Web TWAIN Service installed and running on `http://127.0.0.1:18622`.
- A valid [Dynamic Web TWAIN license key](https://www.dynamsoft.com/customer/license/trialLicense/?product=dcv&package=cross-platform) if you want to scan from a physical scanner.

## Install

```powershell
python -m pip install -r requirements.txt
ollama pull glm-ocr:latest
```

If you want layout analysis to work fully offline, pre-download the PP-DocLayoutV3 model once and point the app at the local directory:

```powershell
huggingface-cli download PaddlePaddle/PP-DocLayoutV3_safetensors --local-dir D:\models\PP-DocLayoutV3_safetensors

$env:LAYOUT_MODEL_DIR="D:\models\PP-DocLayoutV3_safetensors"
$env:LAYOUT_LOCAL_ONLY="1"
```

## Run

```powershell
python .\ocr_app.py
```

Optional environment variables for the layout model:

- `LAYOUT_MODEL_DIR`: path to a pre-downloaded `PaddlePaddle/PP-DocLayoutV3_safetensors` directory.
- `LAYOUT_LOCAL_ONLY=1`: only read local files or local Hugging Face cache for the layout model.
- `HF_ENDPOINT`: optional mirror endpoint if you still want online model download through a mirror.

## Scanner Workflow

1. Click `Refresh Scanners` to enumerate TWAIN and WIA devices.
2. Select a scanner from the drop-down list.
3. Click `Scan` to acquire pages through Dynamic Web TWAIN Service.
4. Select the scanned page and click `Run OCR`.

## OCR Workflow

1. Add an image from disk, clipboard, drag and drop, or scanner input.
2. Choose an OCR engine from the `Engine` drop-down.
3. Click `Layout Analysis` if you want PP-DocLayoutV3 regions before OCR.
4. Click `Run OCR`.
5. If you selected `Ollama (GLM-OCR)`, you can also choose `Text Recognition`, `Table Recognition`, or `Figure Recognition` from the `Mode` drop-down.
6. If you selected `OneOCR` or `PaddleOCR`, OCR can replace the current overlay with text coordinates returned by that engine.
7. For layout analysis, non-text regions are still shown in the overlay with separate labeling, but only text/table/formula regions are clickable OCR targets.
8. Read the full OCR result in the right panel.
9. Click any highlighted interactive region to OCR and copy only that block.

## Layout Model Download Behavior

The app preloads the PP-DocLayoutV3 layout model during startup when the selected OCR engine depends on layout analysis. That does not mean it must download the model on every launch.

- If the model is already available in the local Hugging Face cache, it can be reused.
- If `LAYOUT_MODEL_DIR` is set, the app loads directly from that local directory.
- If `LAYOUT_LOCAL_ONLY=1` is set, the app will not try to fetch missing files from the network.
- If neither local source exists, `transformers` may try to resolve the model online.

## Blog
[Build a Windows Desktop Document Scanner with OCR and Layout Analysis in Python](https://www.dynamsoft.com/codepool/build-windows-desktop-document-ocr-scanner.html)
