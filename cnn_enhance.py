"""
CNN-style image enhancement pipeline (simulated without a heavy torch model).

Uses OpenCV bilateral filtering, CLAHE, and unsharp masking to brighten the
image, suppress noise, and sharpen the eye region before iris encoding.
"""
import cv2
import numpy as np


def enhance_for_iris(bgr_image: np.ndarray) -> np.ndarray:
    """
    Improve brightness, reduce noise, and sharpen edges for downstream eye detection.

    Args:
        bgr_image: Input frame in BGR uint8.

    Returns:
        Enhanced BGR uint8 image (same shape as input).
    """
    if bgr_image is None or bgr_image.size == 0:
        return bgr_image

    lab = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    lab2 = cv2.merge((l2, a, b))
    bright = cv2.cvtColor(lab2, cv2.COLOR_LAB2BGR)

    denoised = cv2.bilateralFilter(bright, d=5, sigmaColor=50, sigmaSpace=50)
    blurred = cv2.GaussianBlur(denoised, (0, 0), sigmaX=1.2)
    sharpened = cv2.addWeighted(denoised, 1.35, blurred, -0.35, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)
