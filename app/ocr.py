"""本地 OCR：图片 bytes → 文字（RapidOCR / 离线 / 中文强）。

模型在首次调用时懒加载并全局复用，因此第一次识别稍慢、之后很快。
"""

from __future__ import annotations

import io

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def image_to_text(data: bytes) -> str:
    """识别图片中的文字，按行拼接返回（识别不到返回空串）。"""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.array(img)[:, :, ::-1]  # RGB → BGR（RapidOCR 习惯）
    result, _ = _get_engine()(arr)
    if not result:
        return ""
    return "\n".join(line[1] for line in result)
