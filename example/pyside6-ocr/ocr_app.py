import os
import sys
import re
import json
import base64
import importlib
import importlib.util
import threading
import tempfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from urllib import error as urllib_error, request as urllib_request

# Clear proxy environment variables to avoid Privoxy intercepting localhost requests
for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
            'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy'):
    os.environ.pop(key, None)

# IMPORTANT: Import torch BEFORE PySide6 to avoid DLL loading conflict on Windows.
import torch
import numpy as np

from transformers import PPDocLayoutV3ForObjectDetection, PPDocLayoutV3ImageProcessor

try:
    from dynamsoftservice import (
        JobStatus, ScannerController, ScannerServiceError, ScannerType
    )
except ImportError:
    JobStatus = None
    ScannerController = None
    ScannerServiceError = RuntimeError
    ScannerType = None

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QSplitter, QListWidget, QListWidgetItem,
    QLabel, QTextEdit, QStatusBar, QFileDialog, QMessageBox,
    QProgressBar, QScrollArea, QComboBox, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal, QSize, QRectF, QPointF, QTimer, QTime
from PySide6.QtGui import (
    QImage, QPixmap, QIcon, QAction, QDragEnterEvent, QDropEvent,
    QDragMoveEvent, QPalette, QColor, QPainter, QPen, QBrush,
    QMouseEvent, QResizeEvent, QFontMetrics
)
from PIL import Image


# Supported image extensions
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp'}

# OCR mode -> Ollama prompt mapping
OCR_MODE_PROMPTS = {
    "Text Recognition": "Text Recognition:",
    "Table Recognition": "Table Recognition:",
    "Figure Recognition": "Formula Recognition:",
}

OCR_ENGINE_OLLAMA = "ollama"
OCR_ENGINE_ONEOCR = "oneocr"
OCR_ENGINE_PADDLEOCR = "paddleocr"
OCR_ENGINE_LABELS = {
    OCR_ENGINE_OLLAMA: "Ollama (GLM-OCR)",
    OCR_ENGINE_ONEOCR: "OneOCR",
    OCR_ENGINE_PADDLEOCR: "PaddleOCR",
}
TEXT_ONLY_OCR_ENGINES = {OCR_ENGINE_ONEOCR, OCR_ENGINE_PADDLEOCR}
OCR_CLICKABLE_TASK_TYPES = {"text", "table", "formula"}

# Prompt for each task_type from layout detection
TASK_TYPE_PROMPTS = {
    "text": "Text Recognition:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
}

# Layout label -> task_type mapping
LABEL_TASK_MAPPING = {
    "text": "text", "content": "text", "title": "text", "doc_title": "text",
    "paragraph_title": "text", "figure_title": "text", "abstract": "text",
    "algorithm": "text", "formula_number": "text", "reference_content": "text",
    "seal": "text", "vertical_text": "text",
    "table": "table",
    "display_formula": "formula", "inline_formula": "formula",
    "chart": "skip", "image": "skip",
    "header": "abandon", "header_image": "abandon", "footer": "abandon",
    "footer_image": "abandon", "footnote": "abandon", "number": "abandon",
    "aside_text": "abandon", "reference": "abandon", "vision_footnote": "abandon",
}

# Ollama API settings
OLLAMA_MODEL = "glm-ocr:latest"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "120"))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "10m")
REGION_IMAGE_CACHE_SIZE = 2
REGION_OCR_MAX_SIDE = 1600


def _default_paddleocr_engine() -> str:
    """Prefer Paddle's native runtime when it is installed; fall back to transformers."""
    if importlib.util.find_spec("paddle") is not None:
        return "paddle_static"
    return "transformers"


PADDLEOCR_ENGINE = os.environ.get("PADDLEOCR_ENGINE", _default_paddleocr_engine())
PADDLEOCR_DEVICE = os.environ.get(
    "PADDLEOCR_DEVICE",
    "gpu" if torch.cuda.is_available() else "cpu",
)
PADDLEOCR_DET_MODEL = os.environ.get("PADDLEOCR_DET_MODEL", "PP-OCRv5_mobile_det")
PADDLEOCR_REC_MODEL = os.environ.get("PADDLEOCR_REC_MODEL", "PP-OCRv5_mobile_rec")
ONEOCR_RUNTIME_DIR = Path.home() / ".config" / "oneocr"
ONEOCR_RUNTIME_FILES = ("oneocr.dll", "oneocr.onemodel", "onnxruntime.dll")

# Dynamic Web TWAIN Service settings
DWT_HOST = os.environ.get("DWT_HOST", "http://127.0.0.1:18622")
DWT_LICENSE_KEY = os.environ.get("DWT_LICENSE_KEY", "DLS2eyJoYW5kc2hha2VDb2RlIjoiMjAwMDAxLTE2NDk4Mjk3OTI2MzUiLCJvcmdhbml6YXRpb25JRCI6IjIwMDAwMSIsInNlc3Npb25QYXNzd29yZCI6IndTcGR6Vm05WDJrcEQ5YUoifQ==")
DWT_SCAN_CONFIG = {
    "IfShowUI": False,
    "PixelType": 2,
    "Resolution": 200,
    "IfFeederEnabled": True,
    "IfDuplexEnabled": False,
}


def _clean_content(content: str) -> str:
    """Clean OCR content for display: strip markdown blocks, HTML tags, etc."""
    content = re.sub(r'```[a-z]*\n?', '', content)
    content = re.sub(r'```', '', content)
    content = re.sub(r'<[^>]+>', '', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()


def _join_text_lines(parts: List[str]) -> str:
    """Normalize OCR text chunks into a readable multi-line string."""
    return "\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip()).strip()


def _get_ocr_engine_label(engine_key: str) -> str:
    return OCR_ENGINE_LABELS.get(engine_key, engine_key)


def _backend_supports_prompt_modes(engine_key: str) -> bool:
    return engine_key == OCR_ENGINE_OLLAMA


def _engine_returns_text_coordinates(engine_key: str) -> bool:
    return engine_key in {OCR_ENGINE_ONEOCR, OCR_ENGINE_PADDLEOCR}


def _engine_uses_layout_model(engine_key: str) -> bool:
    return engine_key != OCR_ENGINE_ONEOCR


def _resolve_ocr_prompt(mode: str = "Text Recognition",
                        task_type: Optional[str] = None) -> str:
    if task_type is not None:
        return TASK_TYPE_PROMPTS.get(task_type, "Text Recognition:")
    return OCR_MODE_PROMPTS.get(mode, "Text Recognition:")


def _line_is_interactive(line: "OcrLine") -> bool:
    return line.task_type in ("text", "table", "formula")


def _ocr_chat(image_paths: List[str], prompt: str,
              model: str = OLLAMA_MODEL) -> str:
    """Send an OCR request to Ollama using the generate endpoint used by CLI flows."""
    image_b64_list = []
    for image_path in image_paths:
        image_b64_list.append(Path(image_path).read_bytes())

    return _ocr_generate_b64_list(image_b64_list, prompt, model=model)


def _ollama_generate_request(prompt: Optional[str] = None,
                             image_payloads: Optional[List[bytes]] = None,
                             model: str = OLLAMA_MODEL) -> Dict[str, Any]:
    """Send a request to Ollama's generate endpoint and return the raw payload."""
    request_payload: Dict[str, Any] = {
        "model": model,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }
    if prompt is not None:
        request_payload["prompt"] = prompt
    if image_payloads:
        request_payload["images"] = [
            base64.b64encode(payload).decode('utf-8') for payload in image_payloads
        ]
    request_body = json.dumps(request_payload).encode("utf-8")

    req = urllib_request.Request(
        f"{OLLAMA_HOST.rstrip('/')}/api/generate",
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {details}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(
            "Failed to connect to Ollama at {}. Start Ollama first, then run 'ollama pull {}'. "
            "Original error: {}".format(OLLAMA_HOST, model, exc)
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "Ollama OCR timed out after {} seconds. Increase OLLAMA_TIMEOUT_SECONDS if your model is still loading."
            .format(OLLAMA_TIMEOUT_SECONDS)
        ) from exc


def _ocr_generate_b64_list(image_payloads: List[bytes], prompt: str,
                           model: str = OLLAMA_MODEL) -> str:
    """Send OCR data to Ollama's generate endpoint using inline image bytes."""
    payload = _ollama_generate_request(prompt=prompt, image_payloads=image_payloads, model=model)
    return payload.get("response", "") or ""


def _preload_ollama_model(model: str = OLLAMA_MODEL) -> Dict[str, Any]:
    """Load the Ollama model into memory without doing OCR work."""
    return _ollama_generate_request(prompt=None, image_payloads=None, model=model)


_ONEOCR_ENGINE = None
_ONEOCR_ENGINE_LOCK = threading.Lock()
_PADDLEOCR_ENGINE = None
_PADDLEOCR_ENGINE_LOCK = threading.Lock()


def _load_oneocr_module():
    if not sys.platform.startswith("win"):
        raise RuntimeError("OneOCR is only supported on Windows.")
    try:
        return importlib.import_module("oneocr")
    except ImportError as exc:
        raise RuntimeError("OneOCR is not installed. Run: pip install oneocr") from exc


def _ensure_oneocr_runtime_files():
    missing_files = [
        file_name for file_name in ONEOCR_RUNTIME_FILES
        if not (ONEOCR_RUNTIME_DIR / file_name).exists()
    ]
    if missing_files:
        raise RuntimeError(
            "OneOCR requires Windows 11 Snipping Tool runtime files in {}: {}"
            .format(ONEOCR_RUNTIME_DIR, ", ".join(missing_files))
        )


def _ensure_oneocr_engine_loaded():
    global _ONEOCR_ENGINE
    if _ONEOCR_ENGINE is not None:
        return _ONEOCR_ENGINE
    with _ONEOCR_ENGINE_LOCK:
        if _ONEOCR_ENGINE is not None:
            return _ONEOCR_ENGINE
        oneocr_module = _load_oneocr_module()
        _ensure_oneocr_runtime_files()
        _ONEOCR_ENGINE = oneocr_module.OcrEngine()
        return _ONEOCR_ENGINE


def _extract_oneocr_text(result: Any) -> str:
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        line_texts = _join_text_lines([
            line.get("text", "") for line in result.get("lines", [])
            if isinstance(line, dict)
        ])
        if line_texts:
            return line_texts

        word_texts = _join_text_lines([
            word.get("text", "")
            for line in result.get("lines", []) if isinstance(line, dict)
            for word in line.get("words", []) if isinstance(word, dict)
        ])
        if word_texts:
            return word_texts

    return str(result).strip()


def _extract_oneocr_lines(result: Any, image_size: tuple[int, int]) -> List["OcrLine"]:
    if not isinstance(result, dict):
        return []

    width, height = image_size
    if width <= 0 or height <= 0:
        return []

    ocr_lines: List[OcrLine] = []
    for line in result.get("lines", []):
        if not isinstance(line, dict):
            continue
        rect = line.get("bounding_rect")
        if not isinstance(rect, dict):
            continue

        xs = [float(rect.get(f"x{i}", 0.0)) for i in range(1, 5)]
        ys = [float(rect.get(f"y{i}", 0.0)) for i in range(1, 5)]
        x1, x2 = max(0.0, min(xs)), min(float(width), max(xs))
        y1, y2 = max(0.0, min(ys)), min(float(height), max(ys))
        if x2 <= x1 or y2 <= y1:
            continue

        bbox_2d = [
            int(x1 / width * 1000),
            int(y1 / height * 1000),
            int(x2 / width * 1000),
            int(y2 / height * 1000),
        ]
        ocr_lines.append(OcrLine(
            text=str(line.get("text", "")).strip(),
            label="text",
            task_type="text",
            bbox_2d=bbox_2d,
            x=x1 / width,
            y=y1 / height,
            width=max((x2 - x1) / width, 0.003),
            height=max((y2 - y1) / height, 0.005),
            ocr_done=True,
        ))

    return ocr_lines


def _is_clickable_region(line: "OcrLine") -> bool:
    return line.task_type in OCR_CLICKABLE_TASK_TYPES


def _load_paddleocr_class():
    try:
        paddleocr_module = importlib.import_module("paddleocr")
    except ImportError as exc:
        raise RuntimeError("PaddleOCR is not installed. Run: pip install paddleocr") from exc
    paddleocr_class = getattr(paddleocr_module, "PaddleOCR", None)
    if paddleocr_class is None:
        raise RuntimeError("The installed paddleocr package does not expose PaddleOCR.")
    return paddleocr_class


def _ensure_paddleocr_engine_loaded():
    global _PADDLEOCR_ENGINE
    if _PADDLEOCR_ENGINE is not None:
        return _PADDLEOCR_ENGINE
    with _PADDLEOCR_ENGINE_LOCK:
        if _PADDLEOCR_ENGINE is not None:
            return _PADDLEOCR_ENGINE
        paddleocr_class = _load_paddleocr_class()
        _PADDLEOCR_ENGINE = paddleocr_class(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            engine=PADDLEOCR_ENGINE,
            device=PADDLEOCR_DEVICE,
            text_detection_model_name=PADDLEOCR_DET_MODEL,
            text_recognition_model_name=PADDLEOCR_REC_MODEL,
        )
        return _PADDLEOCR_ENGINE


def _coerce_paddle_result_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result.get("res") or result

    payload = getattr(result, "res", None)
    if payload is not None:
        return payload.get("res") or payload if isinstance(payload, dict) else payload

    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload.get("res") or payload

    json_payload = getattr(result, "json", None)
    if json_payload is not None:
        raw_json = json_payload() if callable(json_payload) else json_payload
        if isinstance(raw_json, str):
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                return payload.get("res") or payload

    return result


def _extract_paddleocr_text(results: Any) -> str:
    if results is None:
        return ""

    if not isinstance(results, list):
        results = [results]

    text_chunks: List[str] = []
    for result in results:
        payload = _coerce_paddle_result_payload(result)
        if isinstance(payload, dict):
            rec_texts = payload.get("rec_texts")
            if isinstance(rec_texts, list):
                text_chunks.extend(
                    text.strip() for text in rec_texts
                    if isinstance(text, str) and text.strip()
                )
                continue

            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                text_chunks.append(text.strip())

    return _join_text_lines(text_chunks)


def _predict_with_paddleocr(engine: Any,
                            image_path: Optional[str] = None,
                            pil_image: Optional[Image.Image] = None) -> Any:
    if pil_image is None:
        if image_path is None:
            raise RuntimeError("PaddleOCR needs an image path or PIL image.")
        return engine.predict(image_path)

    image_array = np.asarray(pil_image)
    try:
        return engine.predict(image_array)
    except Exception:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name
            pil_image.save(temp_path, format="PNG")
            return engine.predict(temp_path)
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)


def _ocr_with_engine(engine_key: str,
                     image_path: Optional[str] = None,
                     pil_image: Optional[Image.Image] = None,
                     mode: str = "Text Recognition",
                     task_type: Optional[str] = None) -> str:
    if engine_key == OCR_ENGINE_OLLAMA:
        prompt = _resolve_ocr_prompt(mode=mode, task_type=task_type)
        if pil_image is not None:
            return _ocr_generate_b64_list([_image_to_png_bytes(pil_image)], prompt)
        if image_path is None:
            raise RuntimeError("Ollama OCR needs an image path or PIL image.")
        return _ocr_chat([image_path], prompt)

    if engine_key == OCR_ENGINE_ONEOCR:
        if pil_image is None:
            if image_path is None:
                raise RuntimeError("OneOCR needs an image path or PIL image.")
            pil_image = _get_cached_rgb_image(image_path).copy()
        engine = _ensure_oneocr_engine_loaded()
        return _extract_oneocr_text(engine.recognize_pil(pil_image))

    if engine_key == OCR_ENGINE_PADDLEOCR:
        engine = _ensure_paddleocr_engine_loaded()
        return _extract_paddleocr_text(
            _predict_with_paddleocr(engine, image_path=image_path, pil_image=pil_image)
        )

    raise RuntimeError(f"Unsupported OCR engine: {engine_key}")


def _preload_ocr_backend(engine_key: str):
    if engine_key == OCR_ENGINE_OLLAMA:
        _preload_ollama_model()
        return
    if engine_key == OCR_ENGINE_ONEOCR:
        _ensure_oneocr_engine_loaded()
        return
    if engine_key == OCR_ENGINE_PADDLEOCR:
        _ensure_paddleocr_engine_loaded()
        return
    raise RuntimeError(f"Unsupported OCR engine: {engine_key}")


def _crop_region(img: Image.Image, bbox_2d: List[int]) -> Image.Image:
    """Crop a region from an image using bbox_2d [x1,y1,x2,y2] in 0-1000 range."""
    w, h = img.size
    x1 = int(bbox_2d[0] / 1000 * w)
    y1 = int(bbox_2d[1] / 1000 * h)
    x2 = int(bbox_2d[2] / 1000 * w)
    y2 = int(bbox_2d[3] / 1000 * h)
    # Clamp to image bounds
    x1 = max(0, min(x1, w))
    y1 = max(0, min(y1, h))
    x2 = max(x1 + 1, min(x2, w))
    y2 = max(y1 + 1, min(y2, h))
    return img.crop((x1, y1, x2, y2))


def _normalize_rect(x1: float, y1: float, x2: float, y2: float,
                    width: int, height: int):
    bbox_2d = [
        int(x1 / width * 1000),
        int(y1 / height * 1000),
        int(x2 / width * 1000),
        int(y2 / height * 1000),
    ]
    nx, ny = x1 / width, y1 / height
    nw, nh = (x2 - x1) / width, (y2 - y1) / height
    nw = max(nw, 0.003)
    nh = max(nh, 0.005)
    return bbox_2d, nx, ny, nw, nh


def _build_ocr_line(label: str, task_type: str,
                    x1: float, y1: float, x2: float, y2: float,
                    image_width: int, image_height: int,
                    text: str = "", ocr_done: bool = False) -> "OcrLine":
    bbox_2d, nx, ny, nw, nh = _normalize_rect(x1, y1, x2, y2, image_width, image_height)
    return OcrLine(
        text=text,
        label=label,
        task_type=task_type,
        bbox_2d=bbox_2d,
        x=nx,
        y=ny,
        width=nw,
        height=nh,
        ocr_done=ocr_done,
    )


def _build_oneocr_layout_result(result: Dict[str, Any], image_width: int,
                                image_height: int) -> "LayoutResult":
    lines: List[OcrLine] = []
    for line in result.get("lines", []):
        if not isinstance(line, dict):
            continue
        rect = line.get("bounding_rect")
        if not isinstance(rect, dict):
            continue
        xs = [rect.get(f"x{i}") for i in range(1, 5) if rect.get(f"x{i}") is not None]
        ys = [rect.get(f"y{i}") for i in range(1, 5) if rect.get(f"y{i}") is not None]
        if not xs or not ys:
            continue
        text = (line.get("text") or "").strip()
        lines.append(_build_ocr_line(
            label="text",
            task_type="text",
            x1=min(xs),
            y1=min(ys),
            x2=max(xs),
            y2=max(ys),
            image_width=image_width,
            image_height=image_height,
            text=text,
            ocr_done=bool(text),
        ))

    return LayoutResult(
        lines=lines,
        region_count=len(lines),
        has_coordinates=bool(lines),
        raw_response=str(len(lines)),
    )


def _build_paddleocr_layout_result(results: Any, image_width: int,
                                   image_height: int) -> "LayoutResult":
    if results is None:
        return LayoutResult()

    if not isinstance(results, list):
        results = [results]

    lines: List[OcrLine] = []
    for result in results:
        payload = _coerce_paddle_result_payload(result)
        if not isinstance(payload, dict):
            continue

        texts = payload.get("rec_texts") or []
        polygons = payload.get("rec_polys")
        if polygons is None:
            polygons = payload.get("dt_polys")

        if polygons is None and payload.get("rec_boxes") is not None:
            for index, rec_box in enumerate(np.asarray(payload.get("rec_boxes"))):
                if len(rec_box) < 4:
                    continue
                x1, y1, x2, y2 = [float(value) for value in rec_box[:4]]
                text = texts[index].strip() if index < len(texts) and isinstance(texts[index], str) else ""
                lines.append(_build_ocr_line(
                    label="text",
                    task_type="text",
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    image_width=image_width,
                    image_height=image_height,
                    text=text,
                    ocr_done=bool(text),
                ))
            continue

        if polygons is None:
            continue

        polygon_array = np.asarray(polygons)
        for index, polygon in enumerate(polygon_array):
            polygon = np.asarray(polygon)
            if polygon.ndim != 2 or polygon.shape[0] == 0 or polygon.shape[1] < 2:
                continue
            xs = polygon[:, 0].astype(float)
            ys = polygon[:, 1].astype(float)
            x1, x2 = float(xs.min()), float(xs.max())
            y1, y2 = float(ys.min()), float(ys.max())
            width = max(int(x2) + 1, 1)
            height = max(int(y2) + 1, 1)
            text = texts[index].strip() if index < len(texts) and isinstance(texts[index], str) else ""
            lines.append(_build_ocr_line(
                label="text",
                task_type="text",
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                image_width=image_width,
                image_height=image_height,
                text=text,
                ocr_done=bool(text),
            ))

    return LayoutResult(
        lines=lines,
        region_count=len(lines),
        has_coordinates=bool(lines),
        raw_response=str(len(lines)),
    )


def _image_to_png_bytes(img: Image.Image) -> bytes:
    """Convert a PIL Image to PNG bytes for Ollama image requests."""
    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


_REGION_IMAGE_CACHE: "OrderedDict[str, Image.Image]" = OrderedDict()
_REGION_IMAGE_CACHE_LOCK = threading.Lock()


def _get_cached_rgb_image(image_path: str) -> Image.Image:
    """Reuse decoded RGB images so region OCR does not reopen large scans on every click."""
    with _REGION_IMAGE_CACHE_LOCK:
        cached = _REGION_IMAGE_CACHE.get(image_path)
        if cached is not None:
            _REGION_IMAGE_CACHE.move_to_end(image_path)
            return cached

    with Image.open(image_path) as source_image:
        rgb_image = source_image.convert('RGB')

    with _REGION_IMAGE_CACHE_LOCK:
        cached = _REGION_IMAGE_CACHE.get(image_path)
        if cached is not None:
            _REGION_IMAGE_CACHE.move_to_end(image_path)
            return cached

        _REGION_IMAGE_CACHE[image_path] = rgb_image
        while len(_REGION_IMAGE_CACHE) > REGION_IMAGE_CACHE_SIZE:
            _REGION_IMAGE_CACHE.popitem(last=False)
        return rgb_image


def _prepare_region_image_bytes(image_path: str, bbox_2d: List[int]) -> bytes:
    """Crop a cached source image and encode it for image-based OCR backends."""
    return _image_to_png_bytes(_prepare_region_image(image_path, bbox_2d))


def _prepare_region_image(image_path: str, bbox_2d: List[int]) -> Image.Image:
    """Crop a cached source image and shrink oversized regions before OCR."""
    cached_image = _get_cached_rgb_image(image_path)
    with _REGION_IMAGE_CACHE_LOCK:
        cropped = _crop_region(cached_image, bbox_2d).copy()

    width, height = cropped.size
    longest_side = max(width, height)
    if longest_side > REGION_OCR_MAX_SIDE:
        scale = REGION_OCR_MAX_SIDE / float(longest_side)
        resampling = getattr(Image, 'Resampling', Image).LANCZOS
        cropped = cropped.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resampling,
        )

    return cropped


@dataclass
class OcrLine:
    """A layout region with its bounding box. Text is filled lazily on click."""
    text: str = ""           # empty until user clicks this region (lazy OCR)
    label: str = ""
    task_type: str = ""      # text, table, formula, skip, abandon
    bbox_2d: List[int] = field(default_factory=list)  # [x1,y1,x2,y2] in 0-1000
    x: float = 0.0          # normalized 0-1
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    ocr_done: bool = False   # whether this region's OCR has been completed


# Color scheme for overlay regions by label type
LABEL_COLORS = {
    "text":       (QColor(255, 193, 7), QColor(255, 235, 59, 40)),
    "table":      (QColor(76, 175, 80), QColor(200, 230, 201, 40)),
    "formula":    (QColor(156, 39, 176), QColor(225, 190, 231, 40)),
    "figure_title": (QColor(0, 188, 212), QColor(178, 235, 242, 40)),
    "doc_title":  (QColor(244, 67, 54), QColor(255, 205, 210, 40)),
    "image":      (QColor(121, 85, 72), QColor(215, 204, 200, 40)),
    "other":      (QColor(96, 125, 139), QColor(207, 216, 220, 55)),
    "default":    (QColor(255, 193, 7), QColor(255, 235, 59, 40)),
}
HOVER_COLOR = (QColor(33, 150, 243), QColor(33, 150, 243, 70))
# OCR'd region: thick bright green border, vivid green fill
OCR_DONE_PEN = QColor(0, 150, 0)
OCR_DONE_BRUSH = QColor(0, 200, 0, 80)
# Region being OCR'd: animated orange dashed border
OCR_PENDING_PEN = QColor(255, 120, 0)
OCR_PENDING_BRUSH = QColor(255, 180, 0, 60)
# Flash highlight after OCR completes (brief bright blue overlay)
OCR_FLASH_PEN = QColor(0, 200, 255)
OCR_FLASH_BRUSH = QColor(0, 200, 255, 150)


@dataclass
class OcrResult:
    """Full OCR result from the initial full-image OCR pass."""
    text: str = ""
    line_count: int = 0
    has_coordinates: bool = False
    raw_response: str = ""


@dataclass
class LayoutResult:
    """Result from layout detection: regions with bbox_2d but no text yet."""
    lines: List[OcrLine] = field(default_factory=list)
    region_count: int = 0
    has_coordinates: bool = False
    raw_response: str = ""


@dataclass
class ImageItem:
    """Item in the image list."""
    file_path: str = ""
    display_name: str = ""
    subtitle: str = ""
    pixmap: Optional[QPixmap] = None
    thumbnail: Optional[QPixmap] = None
    width: int = 0
    height: int = 0
    ocr_result: Optional[OcrResult] = None
    layout_result: Optional[LayoutResult] = None


# ---------------------------------------------------------------------------
# ImagePreviewWidget: paints image + overlay bounding boxes with rich feedback
# ---------------------------------------------------------------------------
class ImagePreviewWidget(QWidget):
    word_clicked = Signal(str)
    region_clicked = Signal(int)   # emits index of clicked region for lazy OCR

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: white;")

        self._pixmap: Optional[QPixmap] = None
        self._lines: List[OcrLine] = []
        self._hovered_index: int = -1
        self._draw_rect: QRectF = QRectF()

        # Region OCR state tracking
        self._ocr_pending_index: int = -1   # index of region currently being OCR'd
        self._ocr_pending_dash_offset: int = 0  # for animated dashed border
        self._flash_index: int = -1          # index of region that just finished OCR (flash)

        # Timer for animated dashed border while OCR is pending
        self._pending_timer = QTimer(self)
        self._pending_timer.timeout.connect(self._on_pending_tick)
        self._pending_timer.setInterval(150)  # animate every 150ms

        # Timer for flash animation after OCR completes (brief highlight then fade)
        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._on_flash_timeout)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(400)  # flash lasts 400ms

    def set_pixmap(self, pixmap: Optional[QPixmap]):
        self._pixmap = pixmap
        self._hovered_index = -1
        self._ocr_pending_index = -1
        self._pending_timer.stop()
        self._flash_index = -1
        self.update()

    def set_ocr_lines(self, lines: List[OcrLine]):
        self._lines = lines
        self._hovered_index = -1
        self._ocr_pending_index = -1
        self._pending_timer.stop()
        self._flash_index = -1
        self.update()

    def update_line_text(self, index: int, text: str):
        """Update the text for a specific line (after lazy region OCR)."""
        if 0 <= index < len(self._lines):
            self._lines[index].text = text
            self._lines[index].ocr_done = True
            # Start flash animation to make completion very visible
            self._flash_index = index
            self._flash_timer.start()
            self.update()

    def set_region_pending(self, index: int):
        """Mark a region as currently being OCR'd (animated dashed border)."""
        self._ocr_pending_index = index
        self._ocr_pending_dash_offset = 0
        self._pending_timer.start()
        self.update()

    def clear_region_pending(self):
        """Clear the pending state (OCR finished or cancelled)."""
        self._ocr_pending_index = -1
        self._pending_timer.stop()
        self.update()

    def clear_overlay(self):
        self._lines = []
        self._hovered_index = -1
        self._ocr_pending_index = -1
        self._pending_timer.stop()
        self._flash_index = -1
        self.update()

    def _on_pending_tick(self):
        """Animate the dashed border offset for the pending region."""
        self._ocr_pending_dash_offset += 4
        self.update()

    def _on_flash_timeout(self):
        """Flash animation ended — clear the flash highlight."""
        self._flash_index = -1
        self.update()

    def _compute_draw_rect(self) -> QRectF:
        if self._pixmap is None:
            return QRectF()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        if pw <= 0 or ph <= 0 or ww <= 0 or wh <= 0:
            return QRectF()
        scale = min(ww / pw, wh / ph)
        dw, dh = pw * scale, ph * scale
        dx, dy = (ww - dw) / 2, (wh - dh) / 2
        return QRectF(dx, dy, dw, dh)

    def _scale_line_to_widget(self, line: OcrLine) -> QRectF:
        dr = self._draw_rect
        return QRectF(
            dr.x() + line.x * dr.width(),
            dr.y() + line.y * dr.height(),
            line.width * dr.width(),
            line.height * dr.height(),
        )

    def _hit_test(self, pos: QPointF) -> int:
        for i, line in enumerate(self._lines):
            if self._scale_line_to_widget(line).contains(pos):
                return i
        return -1

    def _get_line_colors(self, line: OcrLine):
        if line.task_type == "table":
            return LABEL_COLORS["table"]
        if line.task_type == "formula":
            return LABEL_COLORS["formula"]
        if line.task_type == "text":
            return LABEL_COLORS.get(line.label, LABEL_COLORS["text"])
        return LABEL_COLORS["other"]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if self._pixmap and not self._pixmap.isNull():
            self._draw_rect = self._compute_draw_rect()
            source_rect = QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
            painter.drawPixmap(self._draw_rect, self._pixmap, source_rect)
        else:
            self._draw_rect = QRectF()

        for i, line in enumerate(self._lines):
            rect = self._scale_line_to_widget(line)

            # Determine visual style based on state
            if i == self._flash_index:
                # Just completed OCR — bright blue flash highlight
                pen = QPen(OCR_FLASH_PEN, 3.0)
                painter.setBrush(QBrush(OCR_FLASH_BRUSH))
                painter.setPen(pen)
                painter.drawRect(rect)
                # Draw "Copied!" label prominently
                self._draw_badge(painter, rect, "Copied!", OCR_FLASH_PEN)
            elif i == self._ocr_pending_index:
                # Currently being OCR'd — animated orange dashed border
                pen = QPen(OCR_PENDING_PEN, 2.5)
                pen.setDashOffset(self._ocr_pending_dash_offset)
                pen.setStyle(Qt.DashLine)
                painter.setBrush(QBrush(OCR_PENDING_BRUSH))
                painter.setPen(pen)
                painter.drawRect(rect)
                # Draw "OCR..." badge
                self._draw_badge(painter, rect, "OCR...", OCR_PENDING_PEN)
            elif i == self._hovered_index:
                pen_color, brush_color = HOVER_COLOR
                pen = QPen(pen_color, 2.0)
                painter.setBrush(QBrush(brush_color))
                painter.setPen(pen)
                painter.drawRect(rect)
                # Hover badge: show action hint only
                if line.ocr_done:
                    self._draw_badge(painter, rect, "Click to copy", pen_color)
                else:
                    self._draw_badge(painter, rect, "Click to OCR", pen_color)
            elif line.ocr_done:
                # Already OCR'd — thick solid green border, vivid green fill
                pen = QPen(OCR_DONE_PEN, 2.5)
                painter.setBrush(QBrush(OCR_DONE_BRUSH))
                painter.setPen(pen)
                painter.drawRect(rect)
                # No text label — green border is enough visual feedback
            else:
                # Default state — waiting for OCR click, just draw the box
                pen_color, brush_color = self._get_line_colors(line)
                pen = QPen(pen_color, 1.5)
                painter.setBrush(QBrush(brush_color))
                painter.setPen(pen)
                painter.drawRect(rect)
                if not _is_clickable_region(line):
                    self._draw_badge(
                        painter,
                        rect,
                        line.label.replace("_", " "),
                        pen_color,
                    )

        painter.end()

    def _draw_badge(self, painter: QPainter, rect: QRectF,
                    text: str, color: QColor):
        """Draw a small floating badge at the top-left of a region (only for interactive states)."""
        if rect.width() < 30 or rect.height() < 15:
            return
        font = painter.font()
        font_size = max(8, min(11, int(rect.height() / 6)))
        font.setPointSize(font_size)
        font.setBold(True)
        painter.setFont(font)
        new_fm = QFontMetrics(font)
        text_width = new_fm.horizontalAdvance(text) + 8
        text_height = font_size + 8
        label_rect = QRectF(rect.x() + 2, rect.y() + 2, text_width, text_height)
        painter.fillRect(label_rect, QColor(255, 255, 255, 200))
        painter.setPen(QPen(color))
        painter.drawText(label_rect, Qt.AlignCenter, text)

    def mouseMoveEvent(self, event: QMouseEvent):
        idx = self._hit_test(event.position())
        if idx != self._hovered_index:
            self._hovered_index = idx
            self.update()
            if idx >= 0:
                line = self._lines[idx]
                if idx == self._ocr_pending_index:
                    # Region being OCR'd — show wait cursor
                    self.setCursor(Qt.WaitCursor)
                    self.setToolTip("OCR in progress...")
                elif line.ocr_done:
                    # Already OCR'd — point hand cursor, show text preview
                    self.setCursor(Qt.PointingHandCursor)
                    preview = line.text[:80] + ("..." if len(line.text) > 80 else "")
                    self.setToolTip(f"{preview}\n\nClick to copy to clipboard")
                elif not _is_clickable_region(line):
                    self.setCursor(Qt.ArrowCursor)
                    self.setToolTip(f"Layout region: {line.label}")
                else:
                    # Not yet OCR'd — point hand cursor
                    self.setCursor(Qt.PointingHandCursor)
                    self.setToolTip("Click to OCR this region")
            else:
                self.setCursor(Qt.ArrowCursor)
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        idx = self._hit_test(event.position())
        if idx >= 0:
            line = self._lines[idx]
            if idx == self._ocr_pending_index:
                # Already being OCR'd — ignore
                pass
            elif line.ocr_done:
                # Already OCR'd — copy text to clipboard and flash
                QApplication.clipboard().setText(line.text)
                self.word_clicked.emit(line.text)
                # Brief flash to confirm copy action
                self._flash_index = idx
                self._flash_timer.start()
                self.update()
            elif not _is_clickable_region(line):
                pass
            else:
                # Not yet OCR'd — request lazy OCR for this region
                self.region_clicked.emit(idx)
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        self._hovered_index = -1
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.update()


# ---------------------------------------------------------------------------
# ImageListWidget
# ---------------------------------------------------------------------------
class ImageListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.NoDragDrop)
        self.setIconSize(QSize(48, 48))
        self.setSpacing(4)

    def add_image_item(self, item: ImageItem):
        list_item = QListWidgetItem()
        list_item.setData(Qt.UserRole, item)

        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        thumb_label = QLabel()
        if item.thumbnail:
            thumb_label.setPixmap(
                item.thumbnail.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        thumb_label.setFixedSize(48, 48)
        thumb_label.setAlignment(Qt.AlignCenter)

        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        name_label = QLabel(item.display_name)
        name_label.setStyleSheet("font-weight: bold;")
        name_label.setWordWrap(True)

        subtitle_label = QLabel(item.subtitle)
        subtitle_label.setStyleSheet("color: gray; font-size: 11px;")
        subtitle_label.setWordWrap(True)

        text_layout.addWidget(name_label)
        text_layout.addWidget(subtitle_label)
        layout.addWidget(thumb_label)
        layout.addWidget(text_widget, 1)

        list_item.setSizeHint(widget.sizeHint())
        self.addItem(list_item)
        self.setItemWidget(list_item, widget)
        return list_item


# ---------------------------------------------------------------------------
# OcrWorker: layout detection (HuggingFace transformers) + ONE full-image OCR
# ---------------------------------------------------------------------------
# Layout label -> task_type mapping (used to filter and classify detected regions)
LAYOUT_LABEL_TASK = {
    "text": "text", "content": "text", "title": "text", "doc_title": "text",
    "paragraph_title": "text", "figure_title": "text", "abstract": "text",
    "algorithm": "text", "formula_number": "text", "reference_content": "text",
    "seal": "text", "vertical_text": "text",
    "table": "table",
    "display_formula": "formula", "inline_formula": "formula", "formula": "formula",
    "chart": "skip", "image": "skip",
    "header": "abandon", "header_image": "abandon", "footer": "abandon",
    "footer_image": "abandon", "footnote": "abandon", "number": "abandon",
    "aside_text": "abandon", "reference": "abandon", "vision_footnote": "abandon",
}

# HuggingFace model ID for PP-DocLayoutV3
LAYOUT_MODEL_ID = "PaddlePaddle/PP-DocLayoutV3_safetensors"
LAYOUT_MODEL_DIR = os.environ.get("LAYOUT_MODEL_DIR", "").strip()
LAYOUT_LOCAL_ONLY = os.environ.get("LAYOUT_LOCAL_ONLY", "").strip().lower() in {
    "1", "true", "yes", "on"
}
LAYOUT_THRESHOLD = 0.3


class OcrWorker(QThread):
    """Worker thread: full-image OCR, optionally with OCR-native coordinates."""
    layout_ready = Signal(LayoutResult)   # emitted when OCR backend returns text coordinates
    ocr_done = Signal(OcrResult)          # emitted when full-image OCR completes
    error = Signal(str)
    progress = Signal(str)

    # Shared layout model & processor (loaded once in main thread before first use)
    _model = None
    _processor = None
    _id2label = None
    _device = None
    _model_lock = threading.Lock()

    def __init__(self, image_path: str, mode: str = "Text Recognition",
                 engine_key: str = OCR_ENGINE_OLLAMA, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.mode = mode
        self.engine_key = engine_key

    @classmethod
    def ensure_model_loaded(cls):
        """Load PP-DocLayoutV3 model in the calling thread (call from main thread
        before starting worker). Safe to call multiple times — loads once."""
        if cls._model is not None:
            return
        with cls._model_lock:
            if cls._model is not None:
                return
            model_source = LAYOUT_MODEL_DIR or LAYOUT_MODEL_ID
            if LAYOUT_MODEL_DIR and not Path(LAYOUT_MODEL_DIR).exists():
                raise RuntimeError(
                    "LAYOUT_MODEL_DIR does not exist: {}".format(LAYOUT_MODEL_DIR)
                )

            load_kwargs: Dict[str, Any] = {}
            if LAYOUT_LOCAL_ONLY or LAYOUT_MODEL_DIR:
                load_kwargs["local_files_only"] = True

            cls._processor = PPDocLayoutV3ImageProcessor.from_pretrained(
                model_source,
                **load_kwargs,
            )
            cls._model = PPDocLayoutV3ForObjectDetection.from_pretrained(
                model_source,
                **load_kwargs,
            )
            cls._model.eval()
            if torch.cuda.is_available():
                cls._device = "cuda"
            else:
                cls._device = "cpu"
            cls._model = cls._model.to(cls._device)
            cls._id2label = cls._model.config.id2label

    def run(self):
        try:
            img = Image.open(self.image_path).convert('RGB')

            if self.engine_key == OCR_ENGINE_ONEOCR:
                self.progress.emit("Running OneOCR...")
                engine = _ensure_oneocr_engine_loaded()
                raw_result = engine.recognize_pil(img)
                layout_result = _build_oneocr_layout_result(raw_result, img.size[0], img.size[1])
                if layout_result.has_coordinates:
                    self.layout_ready.emit(layout_result)
                full_text = _clean_content(_extract_oneocr_text(raw_result))
                self.ocr_done.emit(OcrResult(
                    text=full_text,
                    line_count=layout_result.region_count,
                    has_coordinates=layout_result.has_coordinates,
                    raw_response=full_text,
                ))
                return

            if self.engine_key == OCR_ENGINE_PADDLEOCR:
                self.progress.emit("Running PaddleOCR...")
                engine = _ensure_paddleocr_engine_loaded()
                raw_result = _predict_with_paddleocr(engine, image_path=self.image_path)
                layout_result = _build_paddleocr_layout_result(raw_result, img.size[0], img.size[1])
                if layout_result.has_coordinates:
                    self.layout_ready.emit(layout_result)
                full_text = _clean_content(_extract_paddleocr_text(raw_result))
                self.ocr_done.emit(OcrResult(
                    text=full_text,
                    line_count=layout_result.region_count,
                    has_coordinates=layout_result.has_coordinates,
                    raw_response=full_text,
                ))
                return

            self.progress.emit("Running full-image OCR...")

            full_text = _ocr_with_engine(
                self.engine_key,
                image_path=self.image_path,
                mode=self.mode,
            )
            full_text = _clean_content(full_text)

            ocr_result = OcrResult(
                text=full_text,
                line_count=1,
                has_coordinates=False,
                raw_response=full_text,
            )
            self.ocr_done.emit(ocr_result)

        except Exception as e:
            import traceback
            self.error.emit("{}\n\n{}".format(e, traceback.format_exc()))


class ModelPreloadWorker(QThread):
    """Background worker that preloads the layout and OCR models during app startup."""
    progress = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, engine_key: str, parent=None):
        super().__init__(parent)
        self.engine_key = engine_key

    def run(self):
        try:
            if not _engine_returns_text_coordinates(self.engine_key):
                self.progress.emit("Preloading layout model...")
                OcrWorker.ensure_model_loaded()
            self.progress.emit(f"Preloading {_get_ocr_engine_label(self.engine_key)} OCR backend...")
            _preload_ocr_backend(self.engine_key)
            self.done.emit(f"OCR models preloaded for {_get_ocr_engine_label(self.engine_key)}.")
        except Exception as exc:
            self.error.emit(str(exc))


class LayoutAnalysisWorker(QThread):
    """Worker thread: run PP-DocLayoutV3 only and update overlay without OCR."""
    layout_ready = Signal(LayoutResult)
    progress = Signal(str)
    error = Signal(str)

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path

    def run(self):
        try:
            self.progress.emit("Running layout analysis...")
            OcrWorker.ensure_model_loaded()
            img = Image.open(self.image_path).convert('RGB')
            iw, ih = img.size
            inputs = OcrWorker._processor(images=[img], return_tensors='pt')
            inputs = {key: value.to(OcrWorker._device) for key, value in inputs.items()}
            target_sizes = torch.tensor([img.size[::-1]], device=OcrWorker._device)

            with torch.no_grad():
                outputs = OcrWorker._model(**inputs)

            raw_results = OcrWorker._processor.post_process_object_detection(
                outputs, threshold=LAYOUT_THRESHOLD, target_sizes=target_sizes
            )[0]

            layout_lines: List[OcrLine] = []
            for index in range(len(raw_results['boxes'])):
                cls_id = int(raw_results['labels'][index])
                label = OcrWorker._id2label.get(cls_id, str(cls_id))
                x1, y1, x2, y2 = raw_results['boxes'][index].tolist()
                task_type = LAYOUT_LABEL_TASK.get(label, 'other')
                if task_type in ('abandon', 'skip'):
                    task_type = 'other'

                layout_lines.append(_build_ocr_line(
                    label=label,
                    task_type=task_type,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    image_width=iw,
                    image_height=ih,
                ))

            self.layout_ready.emit(LayoutResult(
                lines=layout_lines,
                region_count=len(layout_lines),
                has_coordinates=bool(layout_lines),
                raw_response=str(len(layout_lines)),
            ))
        except Exception as exc:
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# RegionOcrWorker: lazy per-region OCR triggered by mouse click
# ---------------------------------------------------------------------------
class RegionOcrWorker(QThread):
    """Worker thread: OCR a single cropped region on click."""
    region_ocr_done = Signal(int, str)  # (region_index, recognized_text)
    error = Signal(str)

    def __init__(self, image_path: str, region_index: int,
                 bbox_2d: List[int], label: str, task_type: str,
                 engine_key: str = OCR_ENGINE_OLLAMA,
                 parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.region_index = region_index
        self.bbox_2d = bbox_2d
        self.label = label
        self.task_type = task_type
        self.engine_key = engine_key

    def run(self):
        try:
            cropped_image = _prepare_region_image(self.image_path, self.bbox_2d)
            content = _ocr_with_engine(
                self.engine_key,
                pil_image=cropped_image,
                task_type=self.task_type,
            )
            display_text = _clean_content(content)

            self.region_ocr_done.emit(self.region_index, display_text)

        except Exception as e:
            import traceback
            self.error.emit(f"Region OCR failed: {e}\n\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# ScanWorker: scan documents via Dynamic Web TWAIN Service REST API
# ---------------------------------------------------------------------------
class ScanWorker(QThread):
    """Worker thread: scan pages and save them as image files for OCR."""
    page_scanned = Signal(str)
    scan_done = Signal(int)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, host: str, license_key: str, device: str, output_dir: str,
                 parent=None):
        super().__init__(parent)
        self.host = host
        self.license_key = license_key
        self.device = device
        self.output_dir = Path(output_dir)

    def run(self):
        if ScannerController is None or ScannerType is None or JobStatus is None:
            self.error.emit(
                "The twain-wia-sane-scanner package is not installed. "
                "Run: pip install twain-wia-sane-scanner")
            return

        controller = ScannerController(timeout=120, raise_errors=True)
        job_id = ""
        page_count = 0
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.progress.emit("Creating scan job...")
            job = controller.createJob(self.host, {
                "license": self.license_key,
                "device": self.device,
                "autoRun": False,
                "jobTimeout": 180,
                "scannerFailureTimeout": 90,
                "config": DWT_SCAN_CONFIG,
            })
            job_id = job.get("jobuid", "")
            if not job_id:
                raise RuntimeError("Dynamic Web TWAIN Service did not return a scan job ID.")

            controller.updateJob(self.host, job_id, {"status": JobStatus.RUNNING})
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            while True:
                self.progress.emit(f"Scanning page {page_count + 1}...")
                image_bytes = controller.getImageStream(self.host, job_id, imageType="image/png")
                if image_bytes is None:
                    break

                page_count += 1
                image_path = self.output_dir / f"scan_{timestamp}_{page_count:03d}.png"
                image_path.write_bytes(image_bytes)
                self.page_scanned.emit(str(image_path))

            self.scan_done.emit(page_count)
        except Exception as e:
            import traceback
            self.error.emit(f"Scan failed: {e}\n\n{traceback.format_exc()}")
        finally:
            if job_id:
                try:
                    controller.deleteJob(self.host, job_id)
                except Exception:
                    pass
            controller.close()


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class OcrApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.images: List[ImageItem] = []
        self.selected_item: Optional[ImageItem] = None
        self.ocr_result: Optional[OcrResult] = None
        self.layout_result: Optional[LayoutResult] = None
        self.worker: Optional[OcrWorker] = None
        self.layout_worker: Optional[LayoutAnalysisWorker] = None
        self.region_worker: Optional[RegionOcrWorker] = None
        self.scan_worker: Optional[ScanWorker] = None
        self.preload_worker: Optional[ModelPreloadWorker] = None
        self.scanners: List[Dict[str, Any]] = []
        self.selected_ocr_engine = OCR_ENGINE_OLLAMA

        self.setWindowTitle("PySide6 OCR")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 800)
        self.setAcceptDrops(True)

        self._init_ui()
        self._update_status("Ready. Preloading OCR models in background...")
        QTimer.singleShot(0, self._start_model_preload)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self._init_toolbar(main)

        splitter = QSplitter(Qt.Horizontal)
        main.addWidget(splitter, 1)
        splitter.addWidget(self._create_image_list_panel())
        splitter.addWidget(self._create_preview_panel())
        splitter.addWidget(self._create_result_panel())
        splitter.setSizes([260, 500, 400])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)

        self._init_statusbar()

    def _init_toolbar(self, layout):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setStyleSheet("""
            QToolBar { background-color: #E3F2FD; border: none; padding: 4px; spacing: 4px; }
            QToolButton { padding: 6px 12px; border: 1px solid transparent; border-radius: 4px; }
            QToolButton:hover { background-color: #BBDEFB; }
            QToolButton:disabled { opacity: 0.5; }
        """)

        self.open_action = QAction("Open", self)
        self.open_action.setIcon(QIcon.fromTheme("document-open"))
        self.open_action.triggered.connect(self._on_open_clicked)
        toolbar.addAction(self.open_action)

        self.paste_action = QAction("Paste", self)
        self.paste_action.setIcon(QIcon.fromTheme("edit-paste"))
        self.paste_action.triggered.connect(self._on_paste_clicked)
        toolbar.addAction(self.paste_action)

        toolbar.addSeparator()

        self.refresh_scanners_action = QAction("Refresh Scanners", self)
        self.refresh_scanners_action.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_scanners_action.triggered.connect(self._on_refresh_scanners_clicked)
        toolbar.addAction(self.refresh_scanners_action)

        self.scanner_combo = QComboBox()
        self.scanner_combo.setMinimumWidth(220)
        self.scanner_combo.setStyleSheet("""
            QComboBox { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; min-width: 220px; }
        """)
        toolbar.addWidget(self.scanner_combo)

        self.scan_action = QAction("Scan", self)
        self.scan_action.setIcon(QIcon.fromTheme("document-scan"))
        self.scan_action.triggered.connect(self._on_scan_clicked)
        toolbar.addAction(self.scan_action)

        toolbar.addSeparator()

        self.delete_action = QAction("Delete", self)
        self.delete_action.setIcon(QIcon.fromTheme("edit-delete"))
        self.delete_action.triggered.connect(self._on_delete_clicked)
        self.delete_action.setEnabled(False)
        toolbar.addAction(self.delete_action)

        self.clear_action = QAction("Clear", self)
        self.clear_action.setIcon(QIcon.fromTheme("edit-clear"))
        self.clear_action.triggered.connect(self._on_clear_clicked)
        toolbar.addAction(self.clear_action)

        toolbar.addSeparator()

        self.layout_action = QAction("Layout Analysis", self)
        self.layout_action.setIcon(QIcon.fromTheme("insert-object"))
        self.layout_action.triggered.connect(self._on_layout_clicked)
        self.layout_action.setEnabled(False)
        toolbar.addAction(self.layout_action)

        toolbar.addSeparator()

        self.ocr_action = QAction("Run OCR", self)
        self.ocr_action.setIcon(QIcon.fromTheme("find"))
        self.ocr_action.triggered.connect(self._on_ocr_clicked)
        self.ocr_action.setEnabled(False)
        toolbar.addAction(self.ocr_action)

        toolbar.addSeparator()

        self.copy_action = QAction("Copy", self)
        self.copy_action.setIcon(QIcon.fromTheme("edit-copy"))
        self.copy_action.triggered.connect(self._on_copy_clicked)
        toolbar.addAction(self.copy_action)

        self.save_action = QAction("Save", self)
        self.save_action.setIcon(QIcon.fromTheme("document-save"))
        self.save_action.triggered.connect(self._on_save_clicked)
        toolbar.addAction(self.save_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        engine_label = QLabel("Engine:")
        engine_label.setStyleSheet("padding: 0 4px; color: #333;")
        toolbar.addWidget(engine_label)

        self.ocr_engine_combo = QComboBox()
        self.ocr_engine_combo.setStyleSheet("""
            QComboBox { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; min-width: 170px; }
        """)
        self.ocr_engine_combo.currentIndexChanged.connect(self._on_ocr_engine_changed)
        toolbar.addWidget(self.ocr_engine_combo)

        self.ocr_mode_label = QLabel("Mode:")
        self.ocr_mode_label.setStyleSheet("padding: 0 4px; color: #333;")
        toolbar.addWidget(self.ocr_mode_label)

        self.ocr_mode_combo = QComboBox()
        self.ocr_mode_combo.addItems([
            "Text Recognition", "Table Recognition", "Figure Recognition",
        ])
        self.ocr_mode_combo.setStyleSheet("""
            QComboBox { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; min-width: 140px; }
        """)
        toolbar.addWidget(self.ocr_mode_combo)
        self._populate_ocr_engines()

        layout.addWidget(toolbar)

    def _create_image_list_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("QWidget{background-color:#F5F5F5;border:1px solid #E0E0E0;border-radius:8px;}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        header = QLabel("Images")
        header.setStyleSheet("font-weight:bold;font-size:14px;padding:4px;")
        layout.addWidget(header)

        self.image_list = ImageListWidget()
        self.image_list.currentItemChanged.connect(self._on_image_selected)
        layout.addWidget(self.image_list)

        self.empty_list_hint = QLabel(
            "No images loaded.\nOpen a file, paste from clipboard, or drag & drop images here.")
        self.empty_list_hint.setAlignment(Qt.AlignCenter)
        self.empty_list_hint.setStyleSheet("color:gray;padding:20px;")
        self.empty_list_hint.setWordWrap(True)
        layout.addWidget(self.empty_list_hint)
        return panel

    def _create_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("QWidget{background-color:#F5F5F5;border:1px solid #E0E0E0;border-radius:8px;}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.preview_widget = ImagePreviewWidget()
        self.preview_widget.word_clicked.connect(self._on_word_clicked)
        self.preview_widget.region_clicked.connect(self._on_region_clicked)
        layout.addWidget(self.preview_widget, 1)

        self.empty_preview_hint = QLabel("Select an image to preview")
        self.empty_preview_hint.setAlignment(Qt.AlignCenter)
        self.empty_preview_hint.setStyleSheet("color:gray;padding:20px;")
        layout.addWidget(self.empty_preview_hint)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)
        return panel

    def _create_result_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("QWidget{background-color:#F5F5F5;border:1px solid #E0E0E0;border-radius:8px;}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        header = QLabel("Recognized Text")
        header.setStyleSheet("font-weight:bold;font-size:14px;padding:4px;")
        layout.addWidget(header)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Recognized text will appear here...")
        self.result_text.setStyleSheet("""
            QTextEdit { background-color:white; border:1px solid #E0E0E0;
                        border-radius:4px; padding:8px;
                        font-family:Consolas,monospace; font-size:12px; }
        """)
        layout.addWidget(self.result_text)
        return panel

    def _init_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label, 1)

        self.progress_indicator = QProgressBar()
        self.progress_indicator.setMaximumWidth(100)
        self.progress_indicator.setVisible(False)
        self.progress_indicator.setRange(0, 0)
        self.status_bar.addPermanentWidget(self.progress_indicator)

        self.coord_status = QLabel("")
        self.coord_status.setStyleSheet("color:gray;font-style:italic;")
        self.status_bar.addWidget(self.coord_status)

    def _update_status(self, msg: str):
        if hasattr(self, "status_label"):
            self.status_label.setText(msg)

    def _set_busy(self, busy: bool):
        self.progress_indicator.setVisible(busy)
        self.progress_bar.setVisible(busy)
        self.ocr_action.setEnabled(not busy and self.selected_item is not None)
        self.layout_action.setEnabled(not busy and self.selected_item is not None)
        self.ocr_engine_combo.setEnabled(not busy)
        self._sync_ocr_mode_state()

    def _set_scan_busy(self, busy: bool):
        self.refresh_scanners_action.setEnabled(not busy)
        self.scan_action.setEnabled(not busy)
        self.scanner_combo.setEnabled(not busy)
        self.progress_indicator.setVisible(busy)
        self.progress_bar.setVisible(busy)

    def _populate_ocr_engines(self):
        self.ocr_engine_combo.blockSignals(True)
        self.ocr_engine_combo.clear()
        available_engines = [
            (OCR_ENGINE_OLLAMA, OCR_ENGINE_LABELS[OCR_ENGINE_OLLAMA]),
            (OCR_ENGINE_PADDLEOCR, OCR_ENGINE_LABELS[OCR_ENGINE_PADDLEOCR]),
        ]
        if sys.platform.startswith("win"):
            available_engines.insert(1, (OCR_ENGINE_ONEOCR, OCR_ENGINE_LABELS[OCR_ENGINE_ONEOCR]))

        for engine_key, label in available_engines:
            self.ocr_engine_combo.addItem(label, engine_key)

        self.selected_ocr_engine = self.ocr_engine_combo.currentData() or OCR_ENGINE_OLLAMA
        self.ocr_engine_combo.blockSignals(False)
        self._sync_ocr_mode_state()

    def _sync_ocr_mode_state(self):
        supports_modes = _backend_supports_prompt_modes(self.selected_ocr_engine)
        self.ocr_mode_label.setVisible(supports_modes)
        self.ocr_mode_combo.setVisible(supports_modes)
        self.ocr_mode_combo.setEnabled(
            supports_modes and self.worker is None and self.layout_worker is None and self.region_worker is None
        )
        self.ocr_mode_combo.setToolTip(
            "Prompt mode is only used by Ollama GLM-OCR."
            if not supports_modes else ""
        )

    def _on_ocr_engine_changed(self):
        selected = self.ocr_engine_combo.currentData()
        if not selected:
            return
        self.selected_ocr_engine = selected
        self._sync_ocr_mode_state()
        self._update_status(f"OCR engine set to {_get_ocr_engine_label(selected)}.")
        QTimer.singleShot(0, self._start_model_preload)

    def _has_running_worker(self) -> bool:
        return any(
            worker is not None and worker.isRunning()
            for worker in (self.worker, self.layout_worker, self.region_worker, self.scan_worker, self.preload_worker)
        )

    def _start_model_preload(self):
        if self.preload_worker is not None:
            return
        self.preload_worker = ModelPreloadWorker(self.selected_ocr_engine, self)
        self.preload_worker.progress.connect(self._update_status)
        self.preload_worker.done.connect(self._on_preload_done)
        self.preload_worker.error.connect(self._on_preload_error)
        self.preload_worker.finished.connect(self._on_preload_finished)
        self.preload_worker.start()

    def _on_preload_done(self, message: str):
        if self.worker is None and self.region_worker is None and self.scan_worker is None:
            self._update_status(message)

    def _on_preload_error(self, error: str):
        if self.worker is None and self.region_worker is None and self.scan_worker is None:
            self._update_status(f"Model preload skipped: {error}")

    def _on_preload_finished(self):
        self.preload_worker = None

    def closeEvent(self, event):
        """Avoid destroying worker threads while an OCR or scan request is still active."""
        if self._has_running_worker():
            self._update_status("Wait for the active OCR/scan task to finish before closing the app.")
            QMessageBox.information(
                self,
                "Task In Progress",
                "An OCR or scanning task is still running. Please wait for it to finish, or let it time out, before closing the window.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    # -- drag & drop --
    def dragEnterEvent(self, event: QDragEnterEvent):
        event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS:
                    self._add_image(url.toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()

    # -- toolbar handlers --
    def _on_open_clicked(self):
        file_filter = "Images (" + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS)) + ")"
        paths, _ = QFileDialog.getOpenFileNames(self, "Open Image", "", file_filter)
        for p in paths:
            self._add_image(p)

    def _on_paste_clicked(self):
        clipboard = QApplication.clipboard()
        image = clipboard.image()
        if not image.isNull():
            temp_dir = Path.home() / ".qoderworkcn" / "workspace" / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = temp_dir / f"clipboard_{Path.home().name}.png"
            image.save(str(temp_path))
            self._add_image(str(temp_path))
        else:
            QMessageBox.information(self, "Nothing to paste", "The clipboard does not contain an image.")

    def _on_refresh_scanners_clicked(self):
        if ScannerController is None or ScannerType is None:
            QMessageBox.critical(
                self,
                "Scanner Package Missing",
                "Install twain-wia-sane-scanner first:\n\npip install twain-wia-sane-scanner")
            return

        self._update_status("Searching for scanners...")
        try:
            controller = ScannerController(timeout=15, raise_errors=True)
            scanner_type = (
                ScannerType.TWAINSCANNER |
                ScannerType.TWAINX64SCANNER |
                ScannerType.WIASCANNER |
                ScannerType.WIATWAINSCANNER
            )
            self.scanners = controller.getDevices(DWT_HOST, scanner_type)
            controller.close()
        except Exception as e:
            QMessageBox.critical(self, "Scanner Error", str(e))
            self._update_status("Failed to enumerate scanners.")
            return

        self.scanner_combo.clear()
        for scanner in self.scanners:
            self.scanner_combo.addItem(scanner.get("name", "Unnamed scanner"))

        if self.scanners:
            self._update_status(f"Found {len(self.scanners)} scanner(s).")
        else:
            self._update_status("No scanners found. Check Dynamic Web TWAIN Service and scanner drivers.")

    def _on_scan_clicked(self):
        if self.scan_worker is not None:
            return
        if ScannerController is None:
            QMessageBox.critical(
                self,
                "Scanner Package Missing",
                "Install twain-wia-sane-scanner first:\n\npip install twain-wia-sane-scanner")
            return
        if not DWT_LICENSE_KEY:
            QMessageBox.warning(
                self,
                "License Key Required",
                "Set the DWT_LICENSE_KEY environment variable before scanning.")
            return
        if not self.scanners:
            self._on_refresh_scanners_clicked()
            if not self.scanners:
                return

        scanner = self.scanners[self.scanner_combo.currentIndex()]
        device = scanner.get("device", "")
        if not device:
            QMessageBox.warning(self, "Scanner Error", "The selected scanner has no device ID.")
            return

        output_dir = Path.home() / ".qoderworkcn" / "workspace" / "scans"
        self._set_scan_busy(True)
        self._update_status(f"Scanning with {scanner.get('name', 'selected scanner')}...")

        self.scan_worker = ScanWorker(DWT_HOST, DWT_LICENSE_KEY, device, str(output_dir))
        self.scan_worker.page_scanned.connect(self._on_page_scanned)
        self.scan_worker.scan_done.connect(self._on_scan_done)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.progress.connect(self._update_status)
        self.scan_worker.finished.connect(self._on_scan_worker_finished)
        self.scan_worker.start()

    def _on_delete_clicked(self):
        if self.selected_item is None: return
        self.images.remove(self.selected_item)
        self._refresh_image_list()
        self._update_status("Image removed.")

    def _on_clear_clicked(self):
        if not self.images: return
        self.images.clear()
        self.selected_item = None
        self.ocr_result = None
        self.layout_result = None
        self._refresh_image_list()
        self.result_text.clear()
        self.preview_widget.set_pixmap(None)
        self.preview_widget.clear_overlay()
        self.empty_preview_hint.setVisible(True)
        self.coord_status.setText("")
        self._update_status("Cleared all images and results.")

    def _on_layout_clicked(self):
        if self.selected_item is None or self.layout_worker is not None or self.worker is not None:
            return

        try:
            OcrWorker.ensure_model_loaded()
        except Exception as exc:
            QMessageBox.critical(self, "Layout Model Error", str(exc))
            self._update_status("Failed to load layout model.")
            return

        self._set_busy(True)
        self._update_status("Analyzing layout...")
        self.layout_worker = LayoutAnalysisWorker(self.selected_item.file_path)
        self.layout_worker.layout_ready.connect(self._on_layout_analysis_done)
        self.layout_worker.progress.connect(self._update_status)
        self.layout_worker.error.connect(self._on_layout_analysis_error)
        self.layout_worker.finished.connect(self._on_layout_worker_finished)
        self.layout_worker.start()

    def _on_ocr_clicked(self):
        if self.selected_item is None or self.worker is not None or self.layout_worker is not None:
            return
        mode = self.ocr_mode_combo.currentText()
        engine_label = _get_ocr_engine_label(self.selected_ocr_engine)
        self._update_status(f"Loading OCR pipeline for {engine_label}...")

        # Load model in main thread before starting worker (avoids QThread crash)
        try:
            if self.preload_worker is None:
                _preload_ocr_backend(self.selected_ocr_engine)
        except Exception as e:
            QMessageBox.critical(self, "Model Load Error", str(e))
            self._update_status("Failed to load OCR pipeline.")
            return

        self._set_busy(True)
        if _backend_supports_prompt_modes(self.selected_ocr_engine):
            self._update_status(f"Recognizing with {engine_label} ({mode})...")
        else:
            self._update_status(f"Recognizing with {engine_label}...")
        self.ocr_result = None
        self.result_text.clear()

        if self.selected_item is not None:
            self.selected_item.ocr_result = None
            if _engine_returns_text_coordinates(self.selected_ocr_engine):
                self.selected_item.layout_result = None

        self.worker = OcrWorker(
            self.selected_item.file_path,
            mode=mode,
            engine_key=self.selected_ocr_engine,
        )
        self.worker.layout_ready.connect(self._on_layout_ready)
        self.worker.ocr_done.connect(self._on_ocr_done)
        self.worker.error.connect(self._on_ocr_error)
        self.worker.progress.connect(self._update_status)
        self.worker.finished.connect(self._on_ocr_worker_finished)
        self.worker.start()

    def _on_layout_analysis_done(self, result: LayoutResult):
        self.layout_result = result
        if self.selected_item is not None:
            self.selected_item.layout_result = result
        self.preview_widget.set_ocr_lines(result.lines)
        self._set_busy(False)
        self._update_status(
            f"Layout analysis done. {result.region_count} regions shown. Text/table/formula regions are clickable."
        )
        self.coord_status.setText(f"[{result.region_count} regions]")

    def _on_layout_analysis_error(self, error: str):
        self._set_busy(False)
        self._update_status(f"Layout analysis failed: {error}")
        QMessageBox.critical(self, "Layout Analysis Error", error)

    def _on_layout_worker_finished(self):
        self.layout_worker = None

    def _on_layout_ready(self, result: LayoutResult):
        """Layout overlay is ready — draw it IMMEDIATELY on the preview."""
        self.layout_result = result
        if self.selected_item is not None:
            self.selected_item.layout_result = result
        self.preview_widget.set_ocr_lines(result.lines)
        finished_regions = sum(1 for line in result.lines if line.ocr_done)
        if finished_regions == result.region_count and result.region_count:
            self._update_status(
                f"Detected {result.region_count} OCR regions. Click any region to copy its text."
            )
        else:
            self._update_status(
                f"Layout detected: {result.region_count} regions. "
                "Click text regions to OCR them individually. Full OCR in progress..."
            )
        self.coord_status.setText(f"[{result.region_count} regions]")

    def _on_ocr_done(self, result: OcrResult):
        """Full-image OCR is done — show text in the result panel."""
        self.ocr_result = result
        if self.selected_item is not None:
            self.selected_item.ocr_result = result
        self.result_text.setText(result.text)
        self._set_busy(False)
        mode = self.ocr_mode_combo.currentText()

        if result.has_coordinates and self.layout_result:
            n_regions = self.layout_result.region_count
            n_done = sum(1 for l in self.layout_result.lines if l.ocr_done)
            if n_done == n_regions and n_regions:
                self._update_status(
                    f"Done. Full OCR text shown from {_get_ocr_engine_label(self.selected_ocr_engine)}. "
                    f"Click any region to copy its text."
                )
            else:
                self._update_status(
                    f"Done ({mode}). Full OCR text shown. {n_regions} layout regions displayed. "
                    f"Click text regions to get detailed OCR for them."
                )
            self.coord_status.setText(f"[{n_regions} regions, {n_done} OCR'd]")
        else:
            if self.layout_result and self.layout_result.has_coordinates:
                self._update_status(
                    f"Done. Full OCR text recognized by {_get_ocr_engine_label(self.selected_ocr_engine)}. Existing layout overlay kept."
                )
                n_regions = self.layout_result.region_count
                n_done = sum(1 for line in self.layout_result.lines if line.ocr_done)
                self.coord_status.setText(f"[{n_regions} regions, {n_done} OCR'd]")
            else:
                self._update_status(f"Done. Full OCR text recognized by {_get_ocr_engine_label(self.selected_ocr_engine)}.")
                self.coord_status.setText("[no coordinates]")

    def _on_ocr_error(self, error: str):
        self._set_busy(False)
        self._update_status(f"OCR failed: {error}")
        QMessageBox.critical(self, "OCR Error", f"OCR failed:\n{error}")

    def _on_ocr_worker_finished(self):
        self.worker = None

    def _on_region_clicked(self, region_index: int):
        """User clicked a region overlay — start lazy OCR for that specific region."""
        if self.layout_result is None or self.region_worker is not None:
            return
        if region_index < 0 or region_index >= len(self.layout_result.lines):
            return

        line = self.layout_result.lines[region_index]
        if line.ocr_done:
            # Already OCR'd — this shouldn't happen (click handler copies text instead)
            return

        if not _is_clickable_region(line):
            self._update_status(f"Region [{line.label}] is not a text OCR target.")
            return

        if not line.bbox_2d or len(line.bbox_2d) != 4:
            return

        if self.selected_item is None:
            return

        # Mark the region as pending in the preview widget (animated dashed border)
        self.preview_widget.set_region_pending(region_index)

        self._update_status(
            f"OCR region [{line.label}] ({region_index+1}/{len(self.layout_result.lines)})... "
            "Please wait, the orange dashed border will turn green when done.")

        self.region_worker = RegionOcrWorker(
            image_path=self.selected_item.file_path,
            region_index=region_index,
            bbox_2d=line.bbox_2d,
            label=line.label,
            task_type=line.task_type,
            engine_key=self.selected_ocr_engine,
        )
        self.region_worker.region_ocr_done.connect(self._on_region_ocr_done)
        self.region_worker.error.connect(self._on_region_ocr_error)
        self.region_worker.finished.connect(self._on_region_worker_finished)
        self.region_worker.start()

    def _on_region_ocr_done(self, region_index: int, text: str):
        """A single region's OCR is done — update the overlay and show text."""
        if self.layout_result is None:
            return

        line = self.layout_result.lines[region_index]
        line.text = text
        line.ocr_done = True

        if self.selected_item is not None:
            self.selected_item.layout_result = self.layout_result

        # Clear pending state and update overlay (flash animation will trigger)
        self.preview_widget.clear_region_pending()
        self.preview_widget.update_line_text(region_index, text)

        # Copy the region text to clipboard
        QApplication.clipboard().setText(text)

        n_done = sum(1 for l in self.layout_result.lines if l.ocr_done)
        n_total = len(self.layout_result.lines)
        snippet = text[:60] + ("..." if len(text) > 60 else "")
        self._update_status(
            f"Region OCR done [{line.label}]! Text copied to clipboard. "
            f"{n_done}/{n_total} regions OCR'd. "
            f"Content: {snippet}")
        self.coord_status.setText(f"[{n_total} regions, {n_done} OCR'd]")

    def _on_region_ocr_error(self, error: str):
        self.preview_widget.clear_region_pending()
        self._update_status(f"Region OCR failed: {error}")
        QMessageBox.warning(self, "Region OCR Error", error)

    def _on_region_worker_finished(self):
        self.region_worker = None

    def _on_page_scanned(self, image_path: str):
        self._add_image(image_path)
        self._update_status(f"Scanned page added: {Path(image_path).name}")

    def _on_scan_done(self, page_count: int):
        self._set_scan_busy(False)
        if page_count:
            self._update_status(f"Scan complete: {page_count} page(s) added. Select a scanned page and run OCR.")
        else:
            self._update_status("Scan complete: no pages were returned.")

    def _on_scan_error(self, error: str):
        self._set_scan_busy(False)
        self._update_status(f"Scan failed: {error}")
        QMessageBox.critical(self, "Scan Error", error)

    def _on_scan_worker_finished(self):
        self.scan_worker = None

    def _on_word_clicked(self, text: str):
        self.coord_status.setText(f'Copied: "{text[:50]}..."')

    def _on_copy_clicked(self):
        text = self.result_text.toPlainText()
        if not text:
            self._update_status("Nothing to copy.")
            return
        QApplication.clipboard().setText(text)
        self._update_status("Recognized text copied to clipboard.")

    def _on_save_clicked(self):
        text = self.result_text.toPlainText()
        if not text:
            self._update_status("Nothing to save.")
            return
        fp, _ = QFileDialog.getSaveFileName(self, "Save OCR Result", "ocr-result.txt", "Text Files (*.txt)")
        if fp:
            try:
                Path(fp).write_text(text, encoding='utf-8')
                self._update_status(f"Saved to {fp}")
            except Exception as e:
                QMessageBox.critical(self, "Save Failed", str(e))

    def _on_image_selected(self, current, previous):
        if current is None:
            self.selected_item = None
            self.ocr_result = None
            self.layout_result = None
            self.delete_action.setEnabled(False)
            self.layout_action.setEnabled(False)
            self.ocr_action.setEnabled(False)
            self.preview_widget.set_pixmap(None)
            self.preview_widget.clear_overlay()
            self.empty_preview_hint.setVisible(True)
            self.result_text.clear()
            self.coord_status.setText("")
            return

        item: ImageItem = current.data(Qt.UserRole)
        self.selected_item = item
        self.ocr_result = item.ocr_result
        self.layout_result = item.layout_result
        self.delete_action.setEnabled(True)
        self.layout_action.setEnabled(True)
        self.ocr_action.setEnabled(True)

        if item.pixmap:
            self.preview_widget.set_pixmap(item.pixmap)
            self.empty_preview_hint.setVisible(False)
            if item.layout_result and item.layout_result.has_coordinates:
                self.preview_widget.set_ocr_lines(item.layout_result.lines)
            else:
                self.preview_widget.clear_overlay()
        else:
            self.preview_widget.set_pixmap(None)
            self.preview_widget.clear_overlay()
            self.empty_preview_hint.setVisible(True)

        if item.ocr_result:
            self.result_text.setText(item.ocr_result.text)
        else:
            self.result_text.clear()

        if item.layout_result and item.layout_result.has_coordinates:
            n_regions = item.layout_result.region_count
            n_done = sum(1 for line in item.layout_result.lines if line.ocr_done)
            self.coord_status.setText(f"[{n_regions} regions, {n_done} OCR'd]")
        else:
            self.coord_status.setText("")

    def _add_image(self, file_path: str):
        try:
            path = Path(file_path)
            if not path.exists(): return
            pixmap = QPixmap(str(path))
            if pixmap.isNull(): return

            thumbnail = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)

            item = ImageItem(
                file_path=str(path), display_name=path.name,
                subtitle=f"{path.suffix[1:].upper()} - {path.stat().st_size / 1024:.1f} KB",
                pixmap=pixmap, thumbnail=thumbnail,
                width=pixmap.width(), height=pixmap.height(),
            )
            self.images.insert(0, item)

            # Select the new image without disturbing OCR results cached on other images.
            self.ocr_result = None
            self.layout_result = None
            self.preview_widget.clear_overlay()
            self.result_text.clear()

            self._refresh_image_list()
            if self.image_list.count() > 0:
                self.image_list.setCurrentRow(0)
            self._update_status(f"Added: {path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load image:\n{e}")

    def _refresh_image_list(self):
        self.image_list.clear()
        if not self.images:
            self.empty_list_hint.setVisible(True)
            return
        self.empty_list_hint.setVisible(False)
        for item in self.images:
            self.image_list.add_image_item(item)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Link, QColor(0, 0, 255))
    palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    window = OcrApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()