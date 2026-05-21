import base64

import cv2
import numpy as np

from cnn_enhance import enhance_for_iris

EYE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
if EYE_CASCADE.empty():
    raise RuntimeError("Unable to load OpenCV eye cascade classifier.")


def load_image_from_file(file_storage):
    """Read an uploaded file into a BGR NumPy array."""
    image_data = np.frombuffer(file_storage.read(), np.uint8)
    image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to read uploaded image.")
    return image


def decode_image_from_base64(data_url):
    """Decode a base64 camera capture string into a BGR NumPy image."""
    if "," in data_url:
        _, encoded = data_url.split(",", 1)
    else:
        encoded = data_url

    image_data = base64.b64decode(encoded)
    image_array = np.frombuffer(image_data, np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("Unable to decode the image from camera capture.")

    return image


def find_eye_region(image):
    """Detect the largest eye region in the image."""
    image = enhance_for_iris(image)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    eyes = EYE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
    if len(eyes) == 0:
        raise ValueError("No eye region detected in the image.")

    eyes = sorted(eyes, key=lambda rect: rect[2] * rect[3], reverse=True)
    x, y, w, h = eyes[0]
    margin = int(min(w, h) * 0.25)
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(image.shape[1], x + w + margin)
    y2 = min(image.shape[0], y + h + margin)
    return gray[y1:y2, x1:x2]


def extract_iris_template(image):
    """Extract a normalized iris template from an image."""
    eye_region = find_eye_region(image)
    resized = cv2.resize(eye_region, (128, 64), interpolation=cv2.INTER_AREA)
    equalized = cv2.equalizeHist(resized)
    template = equalized.astype(np.float32).flatten()

    mean = np.mean(template)
    std = np.std(template)
    if std < 1e-6:
        raise ValueError("Captured iris image has insufficient contrast.")

    normalized = (template - mean) / std
    norm = np.linalg.norm(normalized)
    if norm < 1e-6:
        raise ValueError("Captured iris image normalization failed.")

    return normalized / norm


def compare_templates(template_a, template_b):
    """Return a cosine similarity score between two normalized iris templates."""
    if template_a.shape != template_b.shape:
        raise ValueError("Iris templates must have the same shape for comparison.")

    return float(np.dot(template_a, template_b))
