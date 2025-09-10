import cv2
import numpy as np
import pytesseract
from pathlib import Path
from PIL import Image

TARGET_DPI = 300

def get_png_dpi(png_path):
    """Read DPI from PNG metadata. Returns DPI or default 72."""
    try:
        with Image.open(png_path) as img:
            info = img.info
            if "dpi" in info:
                dpi_x, dpi_y = info["dpi"]
                return dpi_x
            elif "resolution" in info:
                return info["resolution"][0]
    except:
        pass
    return 72  # default fallback

def resample_to_dpi(img, current_dpi, target_dpi=TARGET_DPI):
    """Resample image to target DPI."""
    if current_dpi == target_dpi:
        return img
    scale = target_dpi / current_dpi
    new_w = int(img.shape[1] * scale)
    new_h = int(img.shape[0] * scale)
    img_resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    return img_resized

def process_image(img_path):
    # Load color image
    img_color = cv2.imread(str(img_path))
    img_gray = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)

    # --- 1. Autorotate ---
    try:
        osd = pytesseract.image_to_osd(img_gray)
        angle = int([line for line in osd.split("\n") if "Rotate:" in line][0].split(":")[1])
        if angle != 0:
            (h, w) = img_gray.shape
            M = cv2.getRotationMatrix2D((w//2, h//2), -angle, 1.0)
            img_gray = cv2.warpAffine(img_gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            img_color = cv2.warpAffine(img_color, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    except:
        pass

    # --- 2. Crop dark margins ---
    th = cv2.adaptiveThreshold(img_gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, 10)
    th_inv = 255 - th
    kernel = np.ones((5,5), np.uint8)
    closed = cv2.morphologyEx(th_inv, cv2.MORPH_CLOSE, kernel)
    coords = cv2.findNonZero(closed)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        img_gray = img_gray[y:y+h, x:x+w]
        img_color = img_color[y:y+h, x:x+w]

    # --- 3. Deskew ---
    coords = np.column_stack(np.where(closed > 0))
    if len(coords) > 0:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        (h, w) = img_gray.shape
        M = cv2.getRotationMatrix2D((w//2, h//2), angle, 1.0)
        img_gray = cv2.warpAffine(img_gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        img_color = cv2.warpAffine(img_color, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    # --- 4. Resample to target DPI ---
    current_dpi = get_png_dpi(img_path)
    img_color = resample_to_dpi(img_color, current_dpi=current_dpi, target_dpi=TARGET_DPI)

    return img_color

def save_with_dpi(img_cv, save_path, dpi=TARGET_DPI):
    """Convert OpenCV image to Pillow and save PNG with DPI metadata."""
    img_rgb = cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    pil_img.save(save_path, dpi=(dpi, dpi))

def process_img_folder(input_folder, output_folder):
    input_folder = Path(input_folder)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    for img_file in input_folder.glob("*.png"):
        processed_img = process_image(img_file)
        processed_img_path = output_folder / img_file.name
        save_with_dpi(processed_img, processed_img_path)
        print(f"Processed {img_file.name}")
