import base64
import os
import subprocess
import tempfile
import threading
import urllib.request
from io import BytesIO
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="Zava OSS Diffusers Worker")


class GenerateImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    width: int = Field(1024, ge=256, le=1024)
    height: int = Field(1024, ge=256, le=1024)
    seed: Optional[int] = None
    num_inference_steps: Optional[int] = Field(None, ge=1, le=50)
    guidance_scale: Optional[float] = Field(None, ge=0.0, le=20.0)
    negative_prompt: Optional[str] = None


class GenerateVideoRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    n_seconds: int = Field(3, ge=1, le=20)
    width: int = Field(512, ge=256, le=1024)
    height: int = Field(512, ge=256, le=1024)
    fps: int = Field(2, ge=1, le=6)
    seed: Optional[int] = None
    num_inference_steps: Optional[int] = Field(None, ge=1, le=50)
    guidance_scale: Optional[float] = Field(None, ge=0.0, le=20.0)
    negative_prompt: Optional[str] = None


class GenerateThumbnailRequest(BaseModel):
    image_b64: Optional[str] = None
    image_url: Optional[str] = None
    prompt: Optional[str] = None
    width: int = Field(1280, ge=256, le=1920)
    height: int = Field(720, ge=256, le=1080)
    output: str = Field("png")  # png | mp4
    seconds: int = Field(3, ge=1, le=10)
    fps: int = Field(30, ge=1, le=60)
    background: str = Field("solid")  # solid | transparent
    bg_color: str = Field("#0b1220")


_pipe_lock = threading.Lock()
_pipe = None
_pipe_model_id: Optional[str] = None
_pipe_device: Optional[str] = None


def _require_auth(authorization: Optional[str]) -> None:
    expected = (os.getenv("OSS_WORKER_AUTH_BEARER") or "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer ") :].strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def _round_to_8(n: int) -> int:
    n = int(n)
    n = max(256, min(1024, n))
    return max(256, n - (n % 8))


def _decode_image_input(image_b64: Optional[str], image_url: Optional[str]) -> bytes:
    if image_b64:
        try:
            return base64.b64decode(image_b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid image_b64: {e}")
    if image_url:
        try:
            with urllib.request.urlopen(image_url, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch image_url: {e}")
    raise HTTPException(status_code=400, detail="Provide image_b64 or image_url")


def _parse_hex_color(s: str):
    v = (s or "").strip()
    if not v:
        return (11, 18, 32)
    if v.startswith("#"):
        v = v[1:]
    if len(v) == 3:
        v = "".join([c * 2 for c in v])
    if len(v) != 6:
        return (11, 18, 32)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return (11, 18, 32)


def _resize_cover(img, *, width: int, height: int):
    from PIL import Image  # type: ignore

    w, h = img.size
    if w <= 0 or h <= 0:
        return img.resize((width, height), Image.LANCZOS)
    scale = max(width / w, height / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    img2 = img.resize((nw, nh), Image.LANCZOS)
    left = max(0, (nw - width) // 2)
    top = max(0, (nh - height) // 2)
    return img2.crop((left, top, left + width, top + height))


def _try_remove_bg_yolo(pil_img):
    """Best-effort foreground extraction using Ultralytics segmentation.

    This requires ultralytics + a local model weight file. If unavailable, raises.
    """
    use_yolo = (os.getenv("OSS_THUMBNAIL_USE_YOLO") or "false").strip().lower() in {"1", "true", "yes", "y", "on"}
    if not use_yolo:
        raise RuntimeError("YOLO disabled")

    model_path = (os.getenv("OSS_THUMBNAIL_YOLO_MODEL") or "").strip()
    if not model_path:
        raise RuntimeError("OSS_THUMBNAIL_YOLO_MODEL not set")

    from ultralytics import YOLO  # type: ignore
    import numpy as np  # type: ignore
    from PIL import Image  # type: ignore

    model = YOLO(model_path)
    im = pil_img.convert("RGB")
    arr = np.array(im)
    results = model.predict(source=arr, verbose=False)
    if not results:
        raise RuntimeError("No YOLO results")
    r0 = results[0]
    masks = getattr(r0, "masks", None)
    if masks is None or getattr(masks, "data", None) is None:
        raise RuntimeError("No segmentation masks")

    data = masks.data
    try:
        mask = data[0].detach().cpu().numpy()
    except Exception:
        mask = data[0]
    mask = (mask > 0.5).astype("uint8") * 255
    if mask.ndim != 2:
        raise RuntimeError("Unexpected mask shape")

    rgba = pil_img.convert("RGBA")
    out = np.array(rgba)
    out[:, :, 3] = mask
    return Image.fromarray(out)


def _try_remove_bg_rembg(pil_img):
    """Best-effort background removal using rembg (onnx)."""
    use_rembg = (os.getenv("OSS_THUMBNAIL_USE_REMBG") or "true").strip().lower() in {"1", "true", "yes", "y", "on"}
    if not use_rembg:
        raise RuntimeError("rembg disabled")

    from rembg import remove  # type: ignore

    buf = BytesIO()
    pil_img.convert("RGBA").save(buf, format="PNG")
    out_bytes = remove(buf.getvalue())
    return __import__("PIL.Image", fromlist=["Image"]).Image.open(BytesIO(out_bytes)).convert("RGBA")


def _remove_bg_fallback(pil_img):
    """CPU-safe fallback foreground extraction using OpenCV GrabCut."""
    try:
        import numpy as np  # type: ignore
        import cv2  # type: ignore
    except Exception:
        return pil_img.convert("RGBA")

    from PIL import Image  # type: ignore

    img = pil_img.convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]
    rect = (int(w * 0.08), int(h * 0.08), int(w * 0.84), int(h * 0.84))
    mask = np.zeros((h, w), np.uint8)
    bgdModel = np.zeros((1, 65), np.float64)
    fgdModel = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(arr, mask, rect, bgdModel, fgdModel, 3, cv2.GC_INIT_WITH_RECT)
        m2 = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")
    except Exception:
        return pil_img.convert("RGBA")

    rgba = pil_img.convert("RGBA")
    out = np.array(rgba)
    out[:, :, 3] = m2
    return Image.fromarray(out)


def _upscale_best_effort(pil_img, *, width: int, height: int):
    """Upscale/resize to target size. Uses Real-ESRGAN if installed and enabled, else Lanczos."""
    use_esrgan = (os.getenv("OSS_THUMBNAIL_USE_ESRGAN") or "false").strip().lower() in {"1", "true", "yes", "y", "on"}
    if use_esrgan:
        try:
            # Optional dependency; keep as best-effort.
            from realesrgan import RealESRGAN  # type: ignore
            import torch  # type: ignore

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            model = RealESRGAN(device, scale=2)
            weights = (os.getenv("OSS_THUMBNAIL_ESRGAN_WEIGHTS") or "").strip()
            if weights:
                model.load_weights(weights)
            # RealESRGAN expects RGB PIL
            out = model.predict(pil_img.convert("RGB"))
            pil_img = out.convert("RGBA") if pil_img.mode == "RGBA" else out
        except Exception:
            pass

    from PIL import Image  # type: ignore
    if pil_img.mode not in {"RGB", "RGBA"}:
        pil_img = pil_img.convert("RGBA")
    return _resize_cover(pil_img, width=width, height=height)


def _make_mp4_from_still_ffmpeg(pil_img, *, seconds: int, fps: int, width: int, height: int) -> bytes:
    """Create a short MP4 using ffmpeg zoompan if available; raises if ffmpeg fails."""
    from PIL import Image  # type: ignore

    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "thumb.png")
        out_path = os.path.join(td, "thumb.mp4")
        pil_img.convert("RGB").save(in_path, format="PNG")

        # Subtle zoom-in over time.
        frames = max(1, int(seconds) * int(fps))
        z_expr = "min(1.15,1.0+0.15*on/{})".format(frames)
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"

        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            in_path,
            "-vf",
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={width}x{height},fps={fps}",
            "-t",
            str(int(seconds)),
            "-pix_fmt",
            "yuv420p",
            out_path,
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            raise RuntimeError(f"ffmpeg failed: {e}")

        with open(out_path, "rb") as f:
            return f.read()


def _get_pipe(model_id: str, device: str, torch_dtype):
    global _pipe, _pipe_model_id, _pipe_device

    with _pipe_lock:
        if _pipe is not None and _pipe_model_id == model_id and _pipe_device == device:
            return _pipe

        from diffusers import DiffusionPipeline

        pipe = DiffusionPipeline.from_pretrained(
            model_id,
            torch_dtype=torch_dtype,
            safety_checker=None,
            requires_safety_checker=False,
        )
        pipe = pipe.to(device)
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
        try:
            pipe.enable_attention_slicing()
        except Exception:
            pass

        _pipe = pipe
        _pipe_model_id = model_id
        _pipe_device = device
        return pipe


@app.on_event("startup")
def _startup_preload():
    """Optionally preload the Diffusers pipeline so requests are fast.

    Controlled by OSS_WORKER_PRELOAD (default: true).
    """
    preload = (os.getenv("OSS_WORKER_PRELOAD") or "true").strip().lower() in {"1", "true", "yes", "y", "on"}
    if not preload:
        return

    model_id = (os.getenv("OSS_DIFFUSERS_MODEL_ID") or "").strip()
    if not model_id:
        return

    try:
        import torch
    except Exception:
        return

    device = (os.getenv("OSS_DIFFUSERS_DEVICE") or "").strip().lower()
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    torch_dtype = torch.float16 if device.startswith("cuda") else torch.float32

    # Best-effort preload. If it fails, requests will surface errors later.
    try:
        _get_pipe(model_id, device, torch_dtype=torch_dtype)
    except Exception:
        pass


@app.get("/healthz")
def healthz():
    return {"status": "healthy", "service": "oss-diffusers-worker"}


@app.post("/generate-image")
def generate_image(req: GenerateImageRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)

    model_id = (os.getenv("OSS_DIFFUSERS_MODEL_ID") or "").strip()
    if not model_id:
        raise HTTPException(status_code=500, detail="OSS_DIFFUSERS_MODEL_ID is not set")

    try:
        import torch
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"torch import failed: {e}")

    device = (os.getenv("OSS_DIFFUSERS_DEVICE") or "").strip().lower()
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    torch_dtype = torch.float16 if device.startswith("cuda") else torch.float32

    width = _round_to_8(req.width)
    height = _round_to_8(req.height)

    steps = req.num_inference_steps
    if steps is None:
        steps = int(os.getenv("OSS_DIFFUSERS_NUM_INFERENCE_STEPS", "25"))

    guidance = req.guidance_scale
    if guidance is None:
        guidance = float(os.getenv("OSS_DIFFUSERS_GUIDANCE_SCALE", "7.5"))

    negative = (req.negative_prompt or os.getenv("OSS_DIFFUSERS_NEGATIVE_PROMPT") or "").strip() or None

    gen = None
    if req.seed is None:
        seed_env = (os.getenv("OSS_DIFFUSERS_SEED") or "").strip()
        if seed_env:
            try:
                req.seed = int(seed_env)
            except Exception:
                req.seed = None

    if req.seed is not None:
        try:
            gen = torch.Generator(device=device).manual_seed(int(req.seed))
        except Exception:
            gen = None

    pipe = _get_pipe(model_id, device, torch_dtype=torch_dtype)

    try:
        with torch.inference_mode():
            result = pipe(
                prompt=req.prompt,
                negative_prompt=negative,
                num_inference_steps=int(steps),
                guidance_scale=float(guidance),
                width=width,
                height=height,
                generator=gen,
            )
            img = result.images[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Diffusers inference failed: {e}")

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return {
        "model": f"oss:aks:diffusers:{model_id}",
        "status": "success",
        "data": [{"b64_json": b64}],
        "diagnostics": {
            "diffusers": {
                "enabled": True,
                "model_id": model_id,
                "device": device,
                "num_inference_steps": int(steps),
                "guidance_scale": float(guidance),
                "seed": req.seed,
            }
        },
    }


@app.post("/generate-video")
def generate_video(req: GenerateVideoRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Generate a short baseline video using Diffusers frames stitched into an mp4.

    This is intended as an OSS comparison artifact, not a full-quality text-to-video model.
    """
    _require_auth(authorization)

    model_id = (os.getenv("OSS_DIFFUSERS_MODEL_ID") or "").strip()
    if not model_id:
        raise HTTPException(status_code=500, detail="OSS_DIFFUSERS_MODEL_ID is not set")

    try:
        import torch
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"torch import failed: {e}")

    try:
        import numpy as np  # type: ignore
        import cv2  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenCV/numpy import failed: {e}")

    device = (os.getenv("OSS_DIFFUSERS_DEVICE") or "").strip().lower()
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    torch_dtype = torch.float16 if device.startswith("cuda") else torch.float32

    width = _round_to_8(req.width)
    height = _round_to_8(req.height)

    max_seconds = int(os.getenv("OSS_WORKER_MAX_VIDEO_SECONDS", "8"))
    seconds = int(max(1, min(int(req.n_seconds), max_seconds)))

    max_frames = int(os.getenv("OSS_WORKER_MAX_VIDEO_FRAMES", "16"))
    fps = int(max(1, min(int(req.fps), 6)))
    frame_count = min(seconds * fps, max_frames)

    steps = req.num_inference_steps
    if steps is None:
        steps = int(os.getenv("OSS_DIFFUSERS_NUM_INFERENCE_STEPS", "12"))

    guidance = req.guidance_scale
    if guidance is None:
        guidance = float(os.getenv("OSS_DIFFUSERS_GUIDANCE_SCALE", "6.0"))

    negative = (req.negative_prompt or os.getenv("OSS_DIFFUSERS_NEGATIVE_PROMPT") or "").strip() or None


@app.post("/generate-thumbnail")
def generate_thumbnail(req: GenerateThumbnailRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Generate a video-ready thumbnail artifact.

    Pipeline (best-effort): YOLO-seg -> rembg -> GrabCut fallback -> upscale -> compose -> (png or mp4 via ffmpeg).
    """
    _require_auth(authorization)

    try:
        from PIL import Image, ImageEnhance, ImageFilter  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pillow import failed: {e}")

    raw = _decode_image_input(req.image_b64, req.image_url)
    try:
        base = Image.open(BytesIO(raw)).convert("RGBA")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image input: {e}")

    width = int(max(256, min(int(req.width), 1920)))
    height = int(max(256, min(int(req.height), 1080)))

    # 1) Foreground extraction
    fg = None
    bg_method = "none"
    for fn, name in [(_try_remove_bg_yolo, "yolo"), (_try_remove_bg_rembg, "rembg")]:
        try:
            fg = fn(base)
            bg_method = name
            break
        except Exception:
            fg = None
    if fg is None:
        fg = _remove_bg_fallback(base)
        bg_method = "grabcut"

    # 2) Resize/upscale
    fg = _upscale_best_effort(fg, width=width, height=height)

    # 3) Compose background
    if (req.background or "").strip().lower() == "transparent":
        composed = fg
    else:
        bg_rgb = _parse_hex_color(req.bg_color)
        bg = Image.new("RGBA", (width, height), color=(bg_rgb[0], bg_rgb[1], bg_rgb[2], 255))
        composed = Image.alpha_composite(bg, fg)

    # 4) Light enhancement
    try:
        composed_rgb = composed.convert("RGB")
        composed_rgb = ImageEnhance.Contrast(composed_rgb).enhance(1.06)
        composed_rgb = ImageEnhance.Color(composed_rgb).enhance(1.04)
        composed_rgb = composed_rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
        composed = composed_rgb.convert("RGBA")
    except Exception:
        pass

    out_kind = (req.output or "png").strip().lower()
    if out_kind == "mp4":
        seconds = int(max(1, min(int(req.seconds), 10)))
        fps = int(max(1, min(int(req.fps), 60)))
        try:
            mp4_bytes = _make_mp4_from_still_ffmpeg(composed, seconds=seconds, fps=fps, width=width, height=height)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {
            "model": "oss:worker:thumbnail",
            "status": "success",
            "video_b64": base64.b64encode(mp4_bytes).decode("utf-8"),
            "diagnostics": {"bg_remove": bg_method, "output": "mp4", "size": [width, height]},
        }

    buf = BytesIO()
    composed.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return {
        "model": "oss:worker:thumbnail",
        "status": "success",
        "data": [{"b64_json": b64}],
        "diagnostics": {"bg_remove": bg_method, "output": "png", "size": [width, height]},
    }

    base_seed = req.seed
    if base_seed is None:
        seed_env = (os.getenv("OSS_DIFFUSERS_SEED") or "").strip()
        if seed_env:
            try:
                base_seed = int(seed_env)
            except Exception:
                base_seed = None

    pipe = _get_pipe(model_id, device, torch_dtype=torch_dtype)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(tmp_path, fourcc, float(fps), (width, height))
        if not writer.isOpened():
            raise RuntimeError("OpenCV VideoWriter failed to open")

        for i in range(frame_count):
            gen = None
            if base_seed is not None:
                try:
                    gen = torch.Generator(device=device).manual_seed(int(base_seed) + i)
                except Exception:
                    gen = None

            with torch.inference_mode():
                result = pipe(
                    prompt=req.prompt,
                    negative_prompt=negative,
                    num_inference_steps=int(steps),
                    guidance_scale=float(guidance),
                    width=width,
                    height=height,
                    generator=gen,
                )
                img = result.images[0].convert("RGB")

            frame = np.array(img)
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            writer.write(frame_bgr)

        try:
            writer.release()
        except Exception:
            pass

        with open(tmp_path, "rb") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Video generation failed: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    b64_mp4 = base64.b64encode(content).decode("utf-8")

    return {
        "model": f"oss:aks:diffusers-video:{model_id}",
        "status": "success",
        "video_b64": b64_mp4,
        "content_type": "video/mp4",
        "diagnostics": {
            "diffusers": {
                "enabled": True,
                "model_id": model_id,
                "device": device,
                "num_inference_steps": int(steps),
                "guidance_scale": float(guidance),
                "seed": base_seed,
            },
            "video": {
                "seconds": int(seconds),
                "fps": int(fps),
                "frames": int(frame_count),
                "width": int(width),
                "height": int(height),
            },
        },
    }
