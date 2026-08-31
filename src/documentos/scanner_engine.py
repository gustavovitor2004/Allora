"""
documentos/scanner_engine.py

Pure OpenCV document-scanning pipeline for the "Digitalizar" sub-tab: turns
a raw phone-camera photo of a document into a clean, perspective-corrected,
enhanced scan - in color, grayscale, or a classic black-and-white
"scanner" look. No text extraction, no Qt dependency here on purpose - this
module is a plain, testable image-processing library; documentos/
tab_documentos.py is the only thing that knows about QImage/QPixmap.

Pipeline (process_document):
    load -> perspective warp (getPerspectiveTransform/warpPerspective)
         -> enhancement (CLAHE + sharpening [+ denoising, or +
            adaptive threshold depending on the chosen mode])
"""

import os

import cv2
import numpy as np

IMAGE_EXTS = ["jpg", "jpeg", "png", "bmp", "webp", "tiff"]

MODE_COLOR = "color"
MODE_GRAYSCALE = "grayscale"
MODE_BW = "bw"


def load_image(path: str) -> np.ndarray:
    """Read an image file into a BGR numpy array. Goes through
    imdecode/np.frombuffer rather than cv2.imread() directly, since
    cv2.imread() is unreliable with non-ASCII paths on Windows (a real risk
    here - `~/Documents/Digitalizados`-style paths always work, but a user
    could load a source photo from anywhere)."""
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Não foi possível abrir a imagem: {path}")
    return image


# ---------------------------------------------------------------------------
# Step 2 - automatic edge detection
# ---------------------------------------------------------------------------

def detect_document_corners(image_bgr: np.ndarray):
    """Try to automatically find the document's 4 corners in a photo.
    Returns a 4x2 array ordered [top-left, top-right, bottom-right,
    bottom-left], or None if no plausible 4-sided contour was found (the
    caller falls back to the full image bounds and asks the user to
    adjust manually)."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    dilated = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    image_area = image_bgr.shape[0] * image_bgr.shape[1]
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[:5]:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        # A tiny 4-sided blob (a logo, a stamp...) isn't the document -
        # require it to cover a meaningful chunk of the photo.
        if len(approx) == 4 and cv2.contourArea(approx) > image_area * 0.1:
            return order_points(approx.reshape(4, 2))

    return None


def order_points(points) -> np.ndarray:
    """Given 4 arbitrary-order (x, y) points, return them ordered as
    [top-left, top-right, bottom-right, bottom-left] - the standard
    "sum/difference" trick: top-left has the smallest x+y, bottom-right the
    largest x+y, top-right the smallest y-x, bottom-left the largest y-x."""
    pts = np.array(points, dtype="float32")
    rect = np.zeros((4, 2), dtype="float32")

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    return rect


def full_image_corners(image_bgr: np.ndarray):
    """The 4 corners of the whole image, used as a fallback when automatic
    detection doesn't find a plausible document boundary."""
    h, w = image_bgr.shape[:2]
    return [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]


# ---------------------------------------------------------------------------
# Step 4 - perspective correction
# ---------------------------------------------------------------------------

def warp_perspective(image_bgr: np.ndarray, corners) -> np.ndarray:
    """Straighten the document: compute the target rectangle from the
    corners' own side lengths (so the aspect ratio matches the physical
    document, not an arbitrary fixed size), then warp."""
    rect = order_points(corners)
    (tl, tr, br, bl) = rect

    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    max_width = max(int(width_top), int(width_bottom), 1)

    height_left = np.linalg.norm(bl - tl)
    height_right = np.linalg.norm(br - tr)
    max_height = max(int(height_left), int(height_right), 1)

    dst = np.array(
        [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image_bgr, matrix, (max_width, max_height))


# ---------------------------------------------------------------------------
# Step 5 - enhancement
# ---------------------------------------------------------------------------

def _sharpen(image: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    return cv2.filter2D(image, -1, kernel)


def enhance_color(image_bgr: np.ndarray) -> np.ndarray:
    """Full color: CLAHE on the L channel (LAB space, so contrast improves
    without shifting hues), then sharpen, then a light denoise."""
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    lab = cv2.merge((l_channel, a_channel, b_channel))
    result = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    result = _sharpen(result)
    result = cv2.fastNlMeansDenoisingColored(
        result, None, h=7, hColor=7, templateWindowSize=7, searchWindowSize=21,
    )
    return result


def enhance_grayscale(image_bgr: np.ndarray) -> np.ndarray:
    """Grayscale with CLAHE contrast + sharpening - good general-purpose
    document look, still returned as a 3-channel BGR image so every mode
    has a uniform shape for display/saving."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    gray = _sharpen(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def enhance_bw(image_bgr: np.ndarray) -> np.ndarray:
    """Classic scanner black & white: adaptive threshold, crisp black text
    on a white background.

    A phone photo carries far more paper-texture, grain and uneven-lighting
    noise than a real flatbed scan. The previous fixed 3x3 blur + a tiny
    11px threshold neighborhood was tuned for a small/low-res scan, not a
    modern multi-megapixel phone photo: on those, that tiny window reacted
    to individual paper-fiber and lighting variations as if they were text,
    producing a page that looked like static. Denoising more aggressively
    first, scaling the threshold's neighborhood to the image's own
    resolution instead of a fixed tiny window, and mopping up any leftover
    speckle with a median blur afterward gets back to a clean black-on-white
    "scanner" look regardless of the source photo's resolution."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)

    block_size = max(15, (min(gray.shape[:2]) // 30) | 1)  # odd, scales with resolution
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, 8,
    )
    bw = cv2.medianBlur(bw, 3)
    return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)


_ENHANCERS = {
    MODE_COLOR: enhance_color,
    MODE_GRAYSCALE: enhance_grayscale,
    MODE_BW: enhance_bw,
}


# ---------------------------------------------------------------------------
# Orchestrator - this is what ScannerWorker calls
# ---------------------------------------------------------------------------

def process_document(image_path: str, corners, mode: str) -> np.ndarray:
    """Full pipeline: load -> warp -> enhance. `corners` are 4 (x, y)
    points in the ORIGINAL image's pixel space (auto-detected or
    hand-adjusted by the user). Returns the final image as a BGR numpy
    array, ready to preview or save."""
    image = load_image(image_path)
    warped = warp_perspective(image, corners)
    enhancer = _ENHANCERS.get(mode, enhance_color)
    return enhancer(warped)


# ---------------------------------------------------------------------------
# Step 8 - save
# ---------------------------------------------------------------------------

def save_as_jpeg(image_bgr: np.ndarray, output_path: str) -> None:
    _write_encoded(image_bgr, output_path, ".jpg", [cv2.IMWRITE_JPEG_QUALITY, 95])


def save_as_png(image_bgr: np.ndarray, output_path: str) -> None:
    _write_encoded(image_bgr, output_path, ".png", [])


def save_as_pdf(image_bgr: np.ndarray, output_path: str) -> None:
    """Single-page PDF, the scanned image centered and fit to an A4 page -
    same reportlab-based approach the rest of the app already uses for
    image->PDF conversion."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from PIL import Image

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)

    page_w, page_h = A4
    img_w, img_h = pil_image.size
    scale = min(page_w / img_w, page_h / img_h)
    draw_w, draw_h = img_w * scale, img_h * scale
    x = (page_w - draw_w) / 2
    y = (page_h - draw_h) / 2

    c = canvas.Canvas(output_path, pagesize=A4)
    c.drawImage(ImageReader(pil_image), x, y, width=draw_w, height=draw_h)
    c.save()


def _write_encoded(image_bgr: np.ndarray, output_path: str, ext: str, params) -> None:
    """Encode in memory then write with plain Python file I/O - avoids
    cv2.imwrite()'s known unreliability with non-ASCII paths on Windows."""
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    ok, buffer = cv2.imencode(ext, image_bgr, params)
    if not ok:
        raise RuntimeError(f"Falha ao codificar a imagem ({ext}).")
    with open(output_path, "wb") as f:
        f.write(buffer.tobytes())
