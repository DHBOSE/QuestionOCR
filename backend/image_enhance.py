# -*- coding: utf-8 -*-
r"""
image_enhance.py —— 识别前的图片优化管线（可选，按图开启）

三个独立步骤，分别绑定一家云服务商（全部为确定性增强 / 修复类 API，
不是生成式模型，不会改动题目内容）：

  dewm     去水印   → 阿里云视觉智能 RemoveImageWatermark（图像标志擦除）
  erase_hw 去手写   → 百度 文档去手写（主）/ TextIn 自动擦除手写文字（备）
  white    转白底   → TextIn crop_enhance_image（切边矫正增强：去阴影/灰底/歪斜）
  sharpen  清晰化   → 百度智能云 图像清晰度增强

设计原则：
- 每一步失败只记录日志并回退到上一步的结果，绝不中断识别任务；
- 背景已经足够白的图（截图类）自动跳过转白底，不花冤枉钱；
- 密钥由前端随请求传入（enhance_config），不落盘；
- 阿里云 SDK（alibabacloud_imageenhan20190930）为可选依赖，缺失时
  仅去水印步骤不可用，其余步骤不受影响。
"""

import base64
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

VALID_OPTS = ("dewm", "erase_hw", "white", "sharpen")  # 合法的优化项（前端传值约定）
TIMEOUT = 120

TEXTIN_URL = "https://api.textin.com/ai/service/v1/crop_enhance_image"
TEXTIN_ERASE_URL = "https://api.textin.com/ai/service/v1/handwritten_erase"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_ENHANCE_URL = "https://aip.baidubce.com/rest/2.0/image-process/v1/image_definition_enhance"
BAIDU_ERASE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/remove_handwriting"
BAIDU_MAX_EDGE = 4096        # 百度接口长边上限
BAIDU_MAX_BODY = 3_800_000   # urlencode 后不超过 4M，留余量
ALIYUN_ENDPOINT = "imageenhan.cn-shanghai.aliyuncs.com"

# ---------------------------------------------------------------------------
# 公共 HTTP 小工具（与 paddle_recognizer 同一风格：代理失败直连重试一次）
# ---------------------------------------------------------------------------

def _open(req, timeout=TIMEOUT):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError):
        if not urllib.request.getproxies():
            raise
        logger.info("经系统代理访问失败，改用直连重试……")
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return direct.open(req, timeout=timeout)


# ---------------------------------------------------------------------------
# 步骤一：去水印（阿里云视觉智能，图像标志擦除）
# ---------------------------------------------------------------------------

def _aliyun_remove_watermark(image_path: str, creds: dict) -> bytes:
    """RemoveImageWatermark（advance 通道支持本地文件直传）。返回处理后图片字节。"""
    ak, sk = creds.get("ali_ak", "").strip(), creds.get("ali_sk", "").strip()
    if not ak or not sk:
        raise RuntimeError("未填写阿里云 AccessKey（API 设置 → 图片优化服务）。")
    try:
        from alibabacloud_imageenhan20190930.client import Client
        from alibabacloud_imageenhan20190930 import models as en_models
        from alibabacloud_tea_openapi import models as open_models
        from alibabacloud_tea_util import models as util_models
    except ImportError:
        raise RuntimeError("后端缺少阿里云 SDK（alibabacloud_imageenhan20190930）。")

    client = Client(open_models.Config(
        access_key_id=ak, access_key_secret=sk, endpoint=ALIYUN_ENDPOINT,
    ))
    with open(image_path, "rb") as f:
        req = en_models.RemoveImageWatermarkAdvanceRequest(image_url_object=f)
        resp = client.remove_image_watermark_advance(req, util_models.RuntimeOptions())
    data = getattr(resp.body, "data", None)
    url = getattr(data, "image_url", None) if data else None
    if not url:
        raise RuntimeError(f"阿里云去水印返回异常：{str(resp.body)[:300]}")
    with _open(urllib.request.Request(url)) as r:
        return r.read()


# ---------------------------------------------------------------------------
# 步骤一·五：去手写（百度主用 / TextIn 备用，擦除学生笔记、还原空白卷面）
# ---------------------------------------------------------------------------

def _baidu_fit_payload(image_path: str) -> str:
    """
    百度接口要求 base64+urlencode 后 ≤4M、长边 ≤4096px。
    超限就转 JPEG 并逐步降质量/缩尺寸，返回 base64 字符串。
    """
    import io
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    if max(w, h) > BAIDU_MAX_EDGE:
        s = BAIDU_MAX_EDGE / max(w, h)
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    quality = 92
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        # urllib.parse.quote 安全字符不转义，base64 字符集内只有 / + = 会膨胀，
        # 用 quote_plus 估算最差情况（≈ 原始 1.06 倍），留足余量
        if len(urllib.parse.quote_plus(b64)) <= BAIDU_MAX_BODY:
            return b64
        if quality > 60:
            quality -= 10
        else:
            w, h = img.size
            img = img.resize((round(w * 0.85), round(h * 0.85)), Image.LANCZOS)
            quality = 92


def _baidu_remove_handwriting(image_path: str, creds: dict) -> bytes:
    """文档去手写：去除手写内容、保留印刷体。返回处理后图片字节。"""
    import json
    if not creds.get("baidu_ak", "").strip() or not creds.get("baidu_sk", "").strip():
        raise RuntimeError("未填写百度 API Key / Secret Key（API 设置 → 图片优化服务）。")
    token = _baidu_access_token(creds)
    data = urllib.parse.urlencode({"image": _baidu_fit_payload(image_path)}).encode("utf-8")
    req = urllib.request.Request(
        f"{BAIDU_ERASE_URL}?access_token={token}", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with _open(req) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"百度去手写失败（HTTP {e.code}）："
                           f"{e.read().decode('utf-8', errors='replace')[:300]}")
    if body.get("error_code"):
        raise RuntimeError(f"百度去手写失败（{body.get('error_code')}）："
                           f"{body.get('error_msg', '')}")
    if not body.get("image_processed"):
        raise RuntimeError("百度去手写返回结果为空。")
    return base64.b64decode(body["image_processed"])


def _textin_handwritten_erase(image_path: str, creds: dict) -> bytes:
    """自动擦除手写文字（区分印刷体/手写体，可处理笔迹与印刷交叉）。返回处理后图片字节。"""
    app_id = creds.get("textin_app_id", "").strip()
    secret = creds.get("textin_secret", "").strip()
    if not app_id or not secret:
        raise RuntimeError("未填写 TextIn 开发者信息（API 设置 → 图片优化服务）。")
    req = urllib.request.Request(
        TEXTIN_ERASE_URL, data=Path(image_path).read_bytes(), method="POST",
        headers={"x-ti-app-id": app_id, "x-ti-secret-code": secret,
                 "Content-Type": "application/octet-stream"},
    )
    import json
    try:
        with _open(req) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        if e.code in (401, 403):
            raise RuntimeError(f"TextIn 鉴权失败（HTTP {e.code}），请检查 x-app-id / x-secret-code。{detail}")
        raise RuntimeError(f"TextIn 去手写请求失败（HTTP {e.code}）：{detail}")
    code = body.get("code")
    if code != 200:
        msg = body.get("message", "")
        hints = {40101: "开发者信息为空", 40102: "开发者信息无效，请核对",
                 40003: "TextIn 余额不足，请充值", 40306: "QPS 超限，请稍后重试"}
        raise RuntimeError(f"TextIn 去手写处理失败（{code}）：{hints.get(code, msg)}")
    image_b64 = (body.get("result") or {}).get("image")
    if not image_b64:
        raise RuntimeError("TextIn 去手写返回结果为空。")
    return base64.b64decode(image_b64)


# ---------------------------------------------------------------------------
# 步骤二：转白底（TextIn 切边矫正增强）
# ---------------------------------------------------------------------------

def _textin_crop_enhance(image_path: str, creds: dict) -> bytes:
    """切边矫正增强：去灰底/阴影/歪斜，输出白底清晰图。返回处理后图片字节。"""
    app_id = creds.get("textin_app_id", "").strip()
    secret = creds.get("textin_secret", "").strip()
    if not app_id or not secret:
        raise RuntimeError("未填写 TextIn 开发者信息（API 设置 → 图片优化服务）。")
    req = urllib.request.Request(
        TEXTIN_URL, data=Path(image_path).read_bytes(), method="POST",
        headers={"x-ti-app-id": app_id, "x-ti-secret-code": secret,
                 "Content-Type": "application/octet-stream"},
    )
    import json
    try:
        with _open(req) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        if e.code in (401, 403):
            raise RuntimeError(f"TextIn 鉴权失败（HTTP {e.code}），请检查 x-app-id / x-secret-code。{detail}")
        raise RuntimeError(f"TextIn 请求失败（HTTP {e.code}）：{detail}")
    code = body.get("code")
    if code != 200:
        msg = body.get("message", "")
        hints = {40101: "开发者信息为空", 40102: "开发者信息无效，请核对",
                 40003: "TextIn 余额不足，请充值", 40306: "QPS 超限，请稍后重试"}
        raise RuntimeError(f"TextIn 处理失败（{code}）：{hints.get(code, msg)}")
    image_list = (body.get("result") or {}).get("image_list") or []
    if not image_list or not image_list[0].get("image"):
        raise RuntimeError("TextIn 返回结果为空。")
    return base64.b64decode(image_list[0]["image"])


# ---------------------------------------------------------------------------
# 步骤三：清晰化（百度图像清晰度增强）
# ---------------------------------------------------------------------------

_baidu_token_cache = {"token": None, "expires": 0.0}


def _baidu_access_token(creds: dict) -> str:
    """获取/复用百度 access_token（有效期 30 天，本进程内缓存）。"""
    ak, sk = creds.get("baidu_ak", "").strip(), creds.get("baidu_sk", "").strip()
    if not ak or not sk:
        raise RuntimeError("未填写百度 API Key / Secret Key（API 设置 → 图片优化服务）。")
    if _baidu_token_cache["token"] and time.time() < _baidu_token_cache["expires"]:
        return _baidu_token_cache["token"]
    import json
    url = (f"{BAIDU_TOKEN_URL}?grant_type=client_credentials"
           f"&client_id={urllib.parse.quote(ak)}&client_secret={urllib.parse.quote(sk)}")
    try:
        with _open(urllib.request.Request(url, method="POST", data=b"")) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"百度鉴权失败（HTTP {e.code}），请检查 API Key / Secret Key。")
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"百度鉴权失败，请检查 API Key / Secret Key：{str(body)[:300]}")
    _baidu_token_cache.update(
        token=token, expires=time.time() + int(body.get("expires_in", 2592000)) - 3600)
    return token


def _baidu_sharpen(image_path: str, creds: dict) -> bytes:
    """图像清晰度增强。返回处理后图片字节。"""
    import json
    token = _baidu_access_token(creds)
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    data = urllib.parse.urlencode({"image": b64}).encode("utf-8")
    req = urllib.request.Request(
        f"{BAIDU_ENHANCE_URL}?access_token={token}", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with _open(req) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"百度清晰度增强失败（HTTP {e.code}）："
                           f"{e.read().decode('utf-8', errors='replace')[:300]}")
    if body.get("error_code"):
        raise RuntimeError(f"百度清晰度增强失败（{body.get('error_code')}）："
                           f"{body.get('error_msg', '')}")
    if not body.get("image"):
        raise RuntimeError("百度清晰度增强返回结果为空。")
    return base64.b64decode(body["image"])


# ---------------------------------------------------------------------------
# 本地兜底：白底化（背景相除法，只提亮背景，不改题目内容）
# ---------------------------------------------------------------------------

def _local_whiten(image_path: str) -> bytes:
    """
    本地白底化（不调用云服务）：用大幅高斯模糊估计背景亮度分布，
    再用「原图 ÷ 背景 × 255」归一化——纸张灰底/阴影被拉到纯白，
    深色线条与文字保持深色，彩色内容色相基本不变。
    返回 JPEG 字节。
    """
    import io
    import cv2
    import numpy as np

    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("本地白底化：无法读取图片。")
    h, w = img.shape[:2]
    # 先在 1/8 分辨率上估背景再放大，速度快且天然平滑
    small = cv2.resize(img, (max(1, w // 8), max(1, h // 8)),
                       interpolation=cv2.INTER_AREA)
    sigma = max(10.0, min(h, w) / 64.0)
    bg = cv2.GaussianBlur(small, (0, 0), sigma)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    out = cv2.divide(img, bg, scale=255)
    # 轻微提升中间调，弥补相除后内容略暗的问题（不改变黑白关系）
    lut = np.array([min(255, round((i / 255.0) ** 0.9 * 255))
                    for i in range(256)], dtype=np.uint8)
    out = cv2.LUT(out, lut)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError("本地白底化：图片编码失败。")
    return buf.tobytes()


# ---------------------------------------------------------------------------
# 自动跳过：背景已经足够白的图不需要转白底
# ---------------------------------------------------------------------------

def _background_is_white(image_path: str) -> bool:
    """采样四周边缘像素，平均亮度 ≥ 235 视为白底（截图类），跳过转白底。"""
    try:
        img = Image.open(image_path).convert("L")
        w, h = img.size
        band = max(4, min(w, h) // 50)
        strips = [img.crop((0, 0, w, band)), img.crop((0, h - band, w, h)),
                  img.crop((0, 0, band, h)), img.crop((w - band, 0, w, h))]
        vals = []
        for s in strips:
            hist = s.histogram()
            total = sum(i * c for i, c in enumerate(hist))
            vals.append(total / max(1, sum(hist)))
        return sum(vals) / len(vals) >= 235
    except Exception:
        return False  # 判断不了就保守处理（照样转白底）


# ---------------------------------------------------------------------------
# 主编排
# ---------------------------------------------------------------------------

_STEP_LABELS = {"dewm": "去水印", "erase_hw": "去手写", "white": "转白底", "sharpen": "清晰化"}


def enhance_image(image_path: str, out_dir: str, opts: list, creds: dict,
                  log=logger.info) -> str:
    """
    按 opts 顺序执行图片优化（去水印 → 转白底 → 清晰化），返回最终图片路径。
    任一步失败回退到上一步结果并记录日志；全部失败返回原图路径。
    opts: ["dewm", "white", "sharpen"] 的子集；空列表直接返回原图。
    """
    opts = [o for o in (opts or []) if o in VALID_OPTS]
    if not opts:
        return image_path

    current = str(image_path)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = Path(image_path).stem
    step_no = 0

    def _save(data: bytes, tag: str) -> str:
        nonlocal step_no
        step_no += 1
        p = Path(out_dir) / f"{stem}_{step_no}_{tag}.jpg"
        p.write_bytes(data)
        return str(p)

    # 去水印（阿里云）
    if "dewm" in opts:
        try:
            current = _save(_aliyun_remove_watermark(current, creds), "dewm")
            log("  ✅ 去水印完成（阿里云）")
        except Exception as e:
            log(f"  ⚠️ 去水印失败，使用原图继续：{e}")

    # 去手写（百度主用，失败回退 TextIn；必须在清晰化之前，避免笔迹被锐化）
    if "erase_hw" in opts:
        has_baidu = bool(creds.get("baidu_ak", "").strip() and creds.get("baidu_sk", "").strip())
        has_textin = bool(creds.get("textin_app_id", "").strip() and creds.get("textin_secret", "").strip())
        if not has_baidu and not has_textin:
            log("  ⚠️ 去手写跳过：未填写百度或 TextIn 密钥（API 设置 → 图片优化服务）")
        else:
            done = False
            if has_baidu:
                try:
                    current = _save(_baidu_remove_handwriting(current, creds), "erasehw")
                    log("  ✅ 去手写完成（百度）")
                    done = True
                except Exception as e:
                    log(f"  ⚠️ 百度去手写失败{'，改用 TextIn 重试' if has_textin else '，使用上一步结果继续'}：{e}")
            if not done and has_textin:
                try:
                    current = _save(_textin_handwritten_erase(current, creds), "erasehw")
                    log("  ✅ 去手写完成（TextIn）")
                    done = True
                except Exception as e:
                    log(f"  ⚠️ TextIn 去手写也失败，使用上一步结果继续：{e}")

    # 转白底（TextIn；白底截图自动跳过；输出不够白时用本地白底化兜底）
    if "white" in opts:
        if _background_is_white(current):
            log("  背景已是白底，跳过转白底（未产生调用）")
        else:
            try:
                current = _save(_textin_crop_enhance(current, creds), "white")
                log("  ✅ 转白底完成（TextIn）")
            except Exception as e:
                log(f"  ⚠️ 转白底失败（TextIn），改用本地白底化兜底：{e}")
            # TextIn 的「增强」输出有时仍是灰底（拍照图），本地再兜底一次
            if not _background_is_white(current):
                try:
                    current = _save(_local_whiten(current), "whiten")
                    log("  ✅ 本地白底化兜底完成（背景相除法，未额外计费）")
                except Exception as e:
                    log(f"  ⚠️ 本地白底化也失败，保留当前结果：{e}")

    # 清晰化（百度）
    if "sharpen" in opts:
        try:
            current = _save(_baidu_sharpen(current, creds), "sharpen")
            log("  ✅ 清晰化完成（百度）")
        except Exception as e:
            log(f"  ⚠️ 清晰化失败，使用上一步结果继续：{e}")

    return current
