import os
import json
import base64
import logging
import traceback
import urllib.request
import urllib.error
import runpod
from runpod.serverless.utils import rp_upload
import inspect
import os.path as osp
import mimetypes
import uuid
import tempfile
import subprocess
import time
from typing import Any

# импортируем исходный handler из их проекта
# если у тебя основной обработчик в другом файле/имени, поправь импорт ниже
import handler as base_handler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("callback-handler")

def _http_post(url: str, payload: dict, headers: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode("utf-8")
    # Add standard headers to bypass Cloudflare protection
    default_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=default_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            log.info("callback response: %s", body[:256])
            return body
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        log.error("callback HTTPError %s: %s", e.code, err_body[:512])
        raise

def _post_file_multipart(url: str, path: str, field_name: str = "file", filename: str | None = None, extra_fields: dict | None = None, timeout: int = 600):
    boundary = uuid.uuid4().hex
    body = bytearray()
    add = body.extend

    def _part_hdr(name, filename=None, ctype=None):
        add(f"--{boundary}\r\n".encode())
        disp = f'form-data; name="{name}"'
        if filename:
            disp += f'; filename="{filename}"'
        add(f"Content-Disposition: {disp}\r\n".encode())
        if ctype:
            add(f"Content-Type: {ctype}\r\n".encode())
        add(b"\r\n")

    # текстовые поля
    extra_fields = extra_fields or {}
    for k, v in extra_fields.items():
        _part_hdr(k)
        add(str(v).encode()); add(b"\r\n")

    # файл
    filename = filename or path.rsplit("/", 1)[-1]
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    _part_hdr(field_name, filename, ctype)
    with open(path, "rb") as f:
        add(f.read())
    add(b"\r\n")

    # финал
    add(f"--{boundary}--\r\n".encode())

    log.info(
        "multipart build: boundary=%s, body_size=%s bytes, filename=%s",
        boundary, len(body), filename,
    )
    req = urllib.request.Request(url, data=bytes(body))
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    req.add_header("Accept", "*/*")
    req.add_header("Connection", "keep-alive")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")

def _post_fields_multipart(url: str, fields: dict, timeout: int = 600):
    boundary = uuid.uuid4().hex
    body = bytearray()
    add = body.extend

    def _part_hdr(name, ctype=None):
        add(f"--{boundary}\r\n".encode())
        disp = f'form-data; name="{name}"'
        add(f"Content-Disposition: {disp}\r\n".encode())
        if ctype:
            add(f"Content-Type: {ctype}\r\n".encode())
        add(b"\r\n")

    for k, v in (fields or {}).items():
        _part_hdr(k)
        add(str(v).encode()); add(b"\r\n")

    add(f"--{boundary}--\r\n".encode())
    log.info("multipart(fields) build: boundary=%s, body_size=%s bytes, keys=%s", boundary, len(body), list((fields or {}).keys()))
    req = urllib.request.Request(url, data=bytes(body))
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    req.add_header("Accept", "*/*")
    req.add_header("Connection", "keep-alive")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")

def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return False

def _maybe_transcode_for_delivery(src_path: str, job_input: dict[str, Any]) -> str:
    """
    By default we DO NOT transcode (preserve original resolution/aspect).
    Enable only when explicitly requested:
      - input.transcode_1080p = true  OR env TRANSCODE_1080P=true
    """
    if not src_path or not osp.exists(src_path):
        return src_path
    enabled = _as_bool(job_input.get("transcode_1080p")) or _as_bool(os.getenv("TRANSCODE_1080P"))
    if not enabled:
        return src_path
    return _transcode_to_1080p(src_path) or src_path

def _transcode_to_1080p(src_path: str) -> str | None:
    """Транскодирует видео в 1920x1080 H.264 yuv420p. Возвращает путь к новому файлу или None."""
    if not osp.exists(src_path):
        return None
    if not _have_ffmpeg():
        log.warning("ffmpeg not found; skip 1080p transcode")
        return None
    dst_path = osp.splitext(src_path)[0] + "_1080p.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-vf", "scale=1920:1080:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        dst_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if osp.exists(dst_path) and osp.getsize(dst_path) > 0:
            log.info("Transcoded to 1080p: %s", dst_path)
            return dst_path
    except Exception as e:
        log.error("ffmpeg transcode failed: %s", e)
    return None

def _rp_upload_file_or_bytes(file_path: str | None = None,
                             data_bytes: bytes | None = None,
                             filename: str = "output.mp4") -> str:
    """
    Надёжная обёртка над runpod.serverless.utils.rp_upload.*
    Перебирает валидные сигнатуры и возвращает URL либо "".
    """
    bucket = os.getenv("RUNPOD_UPLOAD_BUCKET") or os.getenv("RP_UPLOAD_BUCKET")
    url = ""

    def _sig(fn):
        try:
            return str(inspect.signature(fn))
        except Exception:
            return "(signature unavailable)"

    # 1) Если есть путь к файлу — пробуем file-аплоад
    if file_path and osp.exists(file_path):
        base = osp.basename(file_path) or filename

        # a) upload / upload_file
        for name in ("upload", "upload_file"):
            if hasattr(rp_upload, name):
                fn = getattr(rp_upload, name)
                try:
                    log.info("rp_upload.%s%s", name, _sig(fn))
                    # самый безопасный вызов — просто передать путь
                    url = fn(file_path)
                    if isinstance(url, str) and url:
                        return url
                except TypeError:
                    # попробуем именованные, вдруг требуется file_name+file_location
                    try:
                        url = fn(file_name=base, file_location=file_path)  # некоторые версии так принимают
                        if isinstance(url, str) and url:
                            return url
                    except Exception as e:
                        log.info("rp_upload.%s failed: %s", name, e)
                except Exception as e:
                    log.info("rp_upload.%s failed: %s", name, e)

        # b) upload_file_to_bucket — перебираем все реальные порядки аргументов
        if hasattr(rp_upload, "upload_file_to_bucket"):
            fn = getattr(rp_upload, "upload_file_to_bucket")
            log.info("rp_upload.upload_file_to_bucket%s", _sig(fn))
            attempts = []
            # (file_name, file_location)
            attempts.append(((), dict(file_name=base, file_location=file_path)))
            attempts.append(((base, file_path), {}))
            # (bucket, file_name, file_location)
            if bucket:
                attempts.append(((bucket, base, file_path), {}))
                attempts.append(((), dict(bucket=bucket, file_name=base, file_location=file_path)))
                # (bucket, file_location)
                attempts.append(((bucket, file_path), {}))
                attempts.append(((), dict(bucket=bucket, file_location=file_path)))

            for args, kwargs in attempts:
                try:
                    url = fn(*args, **kwargs)
                    if isinstance(url, str) and url:
                        return url
                except Exception as e:
                    log.info("rp_upload.upload_file_to_bucket failed (%s %s): %s", args, kwargs, e)

    # 2) Если передали байты — пробуем bytes-аплоад
    if data_bytes is not None:
        base = filename

        if hasattr(rp_upload, "upload_bytes"):
            fn = getattr(rp_upload, "upload_bytes")
            log.info("rp_upload.upload_bytes%s", _sig(fn))
            attempts = [
                ((data_bytes, base), {}),         # (data_bytes, file_name)
                ((base, data_bytes), {}),         # (file_name, data_bytes)
                ((), dict(data_bytes=data_bytes, file_name=base))
            ]
            for args, kwargs in attempts:
                try:
                    url = fn(*args, **kwargs)
                    if isinstance(url, str) and url:
                        return url
                except Exception as e:
                    log.info("rp_upload.upload_bytes failed (%s %s): %s", args, kwargs, e)

        if hasattr(rp_upload, "upload_bytes_to_bucket") and bucket:
            fn = getattr(rp_upload, "upload_bytes_to_bucket")
            log.info("rp_upload.upload_bytes_to_bucket%s", _sig(fn))
            attempts = [
                ((bucket, base, data_bytes), {}),
                ((bucket, data_bytes, base), {}),
                ((), dict(bucket=bucket, file_name=base, data_bytes=data_bytes)),
                ((), dict(bucket=bucket, data_bytes=data_bytes, file_name=base)),
            ]
            for args, kwargs in attempts:
                try:
                    url = fn(*args, **kwargs)
                    if isinstance(url, str) and url:
                        return url
                except Exception as e:
                    log.info("rp_upload.upload_bytes_to_bucket failed (%s %s): %s", args, kwargs, e)

    return url or ""

def _make_success_body(project_id: int | None, video_url: str, message: str = "", audio_b64: str | None = None):
    body = {"project_id": project_id, "video_url": video_url, "status": "success", "message": message}
    if audio_b64 is not None:
        body["audio"] = audio_b64
    return body

def _make_error_body(project_id: int | None, message: str):
    return {"project_id": project_id, "video_url": "", "status": "failed", "message": message}

def _load_json_file(path: str) -> dict | None:
    try:
        if not path:
            return None
        if not osp.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception as e:
        log.warning("Failed to read json file %s: %s", path, e)
        return None

def _get_s3_credentials(job_input: dict[str, Any]) -> dict[str, str]:
    """
    Returns credentials/config for S3 client.
    Priority:
      1) job_input fields
      2) env vars
      3) local credentials file (job_input.s3_credentials_file / env S3_CREDENTIALS_FILE)
    File format (json):
      {
        "aws_access_key_id": "...",
        "aws_secret_access_key": "...",
        "aws_session_token": "... (optional)",
        "s3_endpoint_url": "https://...",
        "s3_region": "eu-ro-1",
        "s3_bucket": "bucket-name"
      }
    """
    # job_input overrides
    creds: dict[str, str] = {}
    for k_in, k_out in (
        ("aws_access_key_id", "aws_access_key_id"),
        ("aws_secret_access_key", "aws_secret_access_key"),
        ("aws_session_token", "aws_session_token"),
        ("s3_endpoint_url", "s3_endpoint_url"),
        ("s3_region", "s3_region"),
        ("s3_bucket", "s3_bucket"),
    ):
        v = job_input.get(k_in)
        if isinstance(v, str) and v:
            creds[k_out] = v

    # env
    creds.setdefault("aws_access_key_id", os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID") or "")
    creds.setdefault("aws_secret_access_key", os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_ACCESS_KEY") or "")
    creds.setdefault("aws_session_token", os.getenv("AWS_SESSION_TOKEN") or os.getenv("S3_SESSION_TOKEN") or "")
    creds.setdefault("s3_endpoint_url", os.getenv("S3_ENDPOINT_URL") or "")
    creds.setdefault("s3_region", os.getenv("S3_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "")
    creds.setdefault("s3_bucket", os.getenv("S3_BUCKET") or "")

    # local file (only fill missing)
    cred_file = job_input.get("s3_credentials_file") or os.getenv("S3_CREDENTIALS_FILE") or ""
    if isinstance(cred_file, str) and cred_file:
        obj = _load_json_file(cred_file) or {}
        if isinstance(obj, dict):
            for key in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "s3_endpoint_url", "s3_region", "s3_bucket"):
                if not creds.get(key) and isinstance(obj.get(key), str) and obj.get(key):
                    creds[key] = obj[key]

    # normalize empties
    return {k: v for k, v in creds.items() if isinstance(v, str)}

def _get_r2_config(job_input: dict[str, Any]) -> dict[str, str]:
    """
    Cloudflare R2 config (S3-compatible).
    Environment variables (primary):
      EFFECTS_R2_ACCOUNT_ID
      EFFECTS_BUCKET_NAME
      EFFECTS_ACCESS_KEY
      EFFECTS_SECRET_KEY
      EFFECTS_PUBLIC_URL (optional; if set we return public URL instead of presigned)
      EFFECTS_PREFIX (optional; default "infinitetalk")
      EFFECTS_EXPIRES_IN (optional; default 86400, for presigned)

    job_input overrides (optional):
      effects_r2_account_id, effects_bucket_name, effects_access_key, effects_secret_key,
      effects_public_url, effects_prefix, effects_expires_in, effects_url_mode ("public"|"presigned")
    """
    def _get(name_in: str, env: str) -> str:
        v = job_input.get(name_in)
        if isinstance(v, str) and v:
            return v
        return os.getenv(env) or ""

    account_id = _get("effects_r2_account_id", "EFFECTS_R2_ACCOUNT_ID")
    bucket = _get("effects_bucket_name", "EFFECTS_BUCKET_NAME")
    access_key = _get("effects_access_key", "EFFECTS_ACCESS_KEY")
    secret_key = _get("effects_secret_key", "EFFECTS_SECRET_KEY")
    public_url = _get("effects_public_url", "EFFECTS_PUBLIC_URL").rstrip("/")
    prefix = (_get("effects_prefix", "EFFECTS_PREFIX") or "infinitetalk").strip("/")
    expires_in = str(job_input.get("effects_expires_in") or os.getenv("EFFECTS_EXPIRES_IN") or "86400")
    url_mode = _get("effects_url_mode", "EFFECTS_URL_MODE").lower()  # "public"|"presigned"|"" (auto)

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com" if account_id else ""
    return {
        "endpoint_url": endpoint_url,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "public_url": public_url,
        "prefix": prefix,
        "expires_in": expires_in,
        "url_mode": url_mode,
    }

# Upload result video to external storage and return an URL.
# Preferred: Cloudflare R2 bucket (EFFECTS_*). Fallback: RunPod S3 / rp_upload.
def _upload_video_and_get_url(
    *,
    video_path: str | None,
    video_b64: str | None,
    project_id: int | str | None,
    job_input: dict[str, Any],
) -> str:
    # 0) Try Cloudflare R2 (S3-compatible) first
    r2 = _get_r2_config(job_input)
    if r2.get("endpoint_url") and r2.get("bucket") and r2.get("access_key") and r2.get("secret_key"):
        try:
            try:
                import boto3  # type: ignore
                from botocore.config import Config  # type: ignore
            except Exception as e:
                log.warning("boto3 not available, skip R2 upload: %s", e)
                boto3 = None  # type: ignore
                Config = None  # type: ignore

            if boto3 and Config:
                cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"})
                s3 = boto3.client(
                    "s3",
                    endpoint_url=r2["endpoint_url"],
                    region_name="auto",
                    aws_access_key_id=r2["access_key"],
                    aws_secret_access_key=r2["secret_key"],
                    config=cfg,
                )

                pid = str(project_id) if project_id is not None else "no_project"
                key = f"{r2['prefix']}/{pid}/{uuid.uuid4().hex}.mp4"

                upload_path = None
                if video_path and os.path.exists(video_path):
                    upload_path = _maybe_transcode_for_delivery(video_path, job_input)
                elif video_b64:
                    tmp_dir = tempfile.mkdtemp(prefix="infinitetalk_r2_")
                    upload_path = osp.join(tmp_dir, "output.mp4")
                    with open(upload_path, "wb") as f:
                        f.write(base64.b64decode(video_b64))

                if upload_path and osp.exists(upload_path):
                    extra_args = {"ContentType": "video/mp4"}
                    s3.upload_file(upload_path, r2["bucket"], key, ExtraArgs=extra_args)

                    mode = r2.get("url_mode") or ""
                    if (mode == "public") or (mode == "" and r2.get("public_url")):
                        public_base = r2.get("public_url", "").rstrip("/")
                        if public_base:
                            return f"{public_base}/{key}"

                    # presigned fallback
                    try:
                        exp = int(r2.get("expires_in") or "86400")
                    except Exception:
                        exp = 86400
                    url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": r2["bucket"], "Key": key},
                        ExpiresIn=exp,
                    )
                    if isinstance(url, str) and url:
                        return url
        except Exception as e:
            log.warning("R2 upload failed, fallback to S3/rp_upload: %s", e)

    # 1) Try S3-compatible bucket (RunPod S3 API access)
    s3_prefix = (job_input.get("s3_prefix") or os.getenv("S3_PREFIX") or "infinitetalk").strip("/")
    s3_expires_in = int(job_input.get("s3_expires_in") or os.getenv("S3_PRESIGN_EXPIRES_IN") or "86400")

    s3_creds = _get_s3_credentials(job_input)
    s3_bucket = s3_creds.get("s3_bucket") or ""
    s3_endpoint_url = s3_creds.get("s3_endpoint_url") or ""
    s3_region = s3_creds.get("s3_region") or ""
    aws_access_key_id = s3_creds.get("aws_access_key_id") or ""
    aws_secret_access_key = s3_creds.get("aws_secret_access_key") or ""
    aws_session_token = s3_creds.get("aws_session_token") or ""

    if s3_bucket and s3_endpoint_url and aws_access_key_id and aws_secret_access_key:
        try:
            try:
                import boto3  # type: ignore
                from botocore.config import Config  # type: ignore
            except Exception as e:
                log.warning("boto3 not available, skip S3 upload: %s", e)
                boto3 = None  # type: ignore
                Config = None  # type: ignore

            if boto3 and Config:
                # Path-style addressing works best with custom endpoints like runpod.io
                cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"})
                s3 = boto3.client(
                    "s3",
                    endpoint_url=s3_endpoint_url,
                    region_name=s3_region,
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    aws_session_token=aws_session_token,
                    config=cfg,
                )

                pid = str(project_id) if project_id is not None else "no_project"
                key = f"{s3_prefix}/{pid}/{uuid.uuid4().hex}.mp4"

                upload_path = None
                if video_path and os.path.exists(video_path):
                    upload_path = _maybe_transcode_for_delivery(video_path, job_input)
                elif video_b64:
                    # materialize bytes to a temp file for reliable multipart upload
                    tmp_dir = tempfile.mkdtemp(prefix="infinitetalk_s3_")
                    upload_path = osp.join(tmp_dir, "output.mp4")
                    with open(upload_path, "wb") as f:
                        f.write(base64.b64decode(video_b64))

                if upload_path and osp.exists(upload_path):
                    extra_args = {"ContentType": "video/mp4"}
                    s3.upload_file(upload_path, s3_bucket, key, ExtraArgs=extra_args)
                    url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": s3_bucket, "Key": key},
                        ExpiresIn=s3_expires_in,
                    )
                    if isinstance(url, str) and url:
                        log.info("S3 upload OK: bucket=%s key=%s expires_in=%s endpoint=%s", s3_bucket, key, s3_expires_in, s3_endpoint_url)
                        return url
        except Exception as e:
            log.warning("S3 upload failed, fallback to rp_upload: %s", e)

    # 2) Fallback to rp_upload (RunPod helper) if S3 is not configured/failed
    try:
        if video_path and os.path.exists(video_path):
            tx_path = _maybe_transcode_for_delivery(video_path, job_input)
            url = _rp_upload_file_or_bytes(file_path=tx_path, filename="output.mp4")
            return url or ""
    except Exception as e:
        log.warning("upload via file_path failed: %s", e)

    try:
        if video_b64:
            data = base64.b64decode(video_b64)
            url = _rp_upload_file_or_bytes(data_bytes=data, filename="output.mp4")
            return url or ""
    except Exception as e:
        log.warning("upload via base64 bytes failed: %s", e)

    return ""

# RunPod POST /job-done реджектит слишком большой JSON (400 Bad Request).
# Видео уходит через колбек (multipart на твой сервер) пока воркер жив.
# В ответ RunPod кладём только маленький JSON — без base64.
def _sanitize_result_for_runpod(result: dict) -> dict:
    """
    RunPod result should be small and stable.
    We return ONLY:
      - status: "success" | "failed"
      - video_url: string (may be empty on failure)
    """
    if not isinstance(result, dict):
        return {"status": "failed", "video_url": ""}

    if result.get("error"):
        return {"status": "failed", "video_url": ""}

    video_url = result.get("video_url")
    if isinstance(video_url, str) and video_url:
        return {"status": "success", "video_url": video_url}

    # Generation might have succeeded but upload didn't; for /status consumers it's a failure.
    return {"status": "failed", "video_url": ""}

def handler(job: dict):
    """
    Расширенный обработчик:
    - поддерживает input.callback_url (опционально)
    - всегда возвращает обычный ответ (для совместимости)
    - если задан callback_url, по завершении отдельно шлёт POST с SUCCESS/ERROR
    """
    job_input = job.get("input", {}) or {}
    # webhook_url и project_id НЕ обязательны; если оба заданы — отправим коллбэк
    callback_url = job_input.get("webhook_url") or job_input.get("callback_url") or os.getenv("CALLBACK_URL")
    disable_callback = bool(job_input.get("disable_callback", False))
    upload_result = bool(job_input.get("upload_result", True))
    env_headers = os.getenv("CALLBACK_HEADERS")
    callback_headers = job_input.get("callback_headers") or (json.loads(env_headers) if env_headers else None)  # опционально: {"Authorization":"Bearer ..."}
    # приводим project_id к int при наличии
    raw_project_id = job_input.get("project_id")
    try:
        project_id = int(raw_project_id) if raw_project_id is not None else None
    except Exception:
        project_id = raw_project_id

    try:
        # вызываем оригинальный handler из проекта
        result = base_handler.handler(job)

        # извлечём видео/аудио: base64 и локальные пути если есть
        video_b64 = None
        video_path = None
        audio_path = None
        if isinstance(result, dict):
            if "video" in result and isinstance(result["video"], str):
                video_b64 = result["video"]
            elif "video_base64" in result and isinstance(result["video_base64"], str):
                video_b64 = result["video_base64"]
            if "video_path" in result and isinstance(result["video_path"], str):
                video_path = result["video_path"]
            if "audio_path" in result and isinstance(result["audio_path"], str):
                audio_path = result["audio_path"]

        # 0) Preferred delivery: upload the result and return a URL in RunPod output,
        # so your backend can expose it from /status without needing webhook delivery.
        if upload_result and isinstance(result, dict):
            try:
                video_url = _upload_video_and_get_url(
                    video_path=video_path,
                    video_b64=video_b64,
                    project_id=project_id,
                    job_input=job_input,
                )
                if video_url:
                    result["video_url"] = video_url
                    log.info("✅ result uploaded, video_url=%s...", str(video_url)[:64])
                else:
                    log.warning("⚠️ upload_result enabled but video_url is empty (no file/base64 or upload failed)")
            except Exception as e:
                log.warning("⚠️ upload_result failed: %s", e)

        # если заданы и callback_url, и project_id — отправим успешный коллбэк в новом формате
        if (not disable_callback) and callback_url and project_id is not None:
            try:
                # 0) подготовим аудио base64 (для extra_fields)
                audio_b64 = None
                try:
                    if audio_path and os.path.exists(audio_path):
                        with open(audio_path, "rb") as af:
                            audio_b64 = base64.b64encode(af.read()).decode("utf-8")
                except Exception as e:
                    log.warning("Не удалось прочитать аудио для коллбэка: %s", e)

                # 1) Пытаемся отправить как multipart/form-data, если у нас есть реальный файл
                if video_path and os.path.exists(video_path):
                    tx_path = _maybe_transcode_for_delivery(video_path, job_input)
                    meta = {"project_id": project_id, "status": "success", "message": ""}
                    if audio_b64:
                        meta["audio"] = audio_b64
                    try:
                        ctype = mimetypes.guess_type("result.mp4")[0] or "application/octet-stream"
                    except Exception:
                        ctype = "application/octet-stream"
                    try:
                        fsize = os.path.getsize(tx_path)
                    except Exception:
                        fsize = None
                    log.info(
                        "Webhook multipart: url=%s, headers_keys=%s, field=file, filename=result.mp4, ctype=%s, file_size=%s, extra_fields=%s",
                        callback_url,
                        list((callback_headers or {}).keys()),
                        ctype,
                        fsize,
                        list(meta.keys()),
                    )
                    resp = _post_file_multipart(callback_url, tx_path, field_name="file", filename="result.mp4", extra_fields=meta)
                    log.info("Webhook upload OK: %s", resp[:500])
                else:
                    # 2) fallback: пробуем через rp_upload / data URL и JSON
                    upload_url = ""
                    if video_b64:
                        data = base64.b64decode(video_b64)
                        upload_url = _rp_upload_file_or_bytes(data_bytes=data, filename="output.mp4")
                    if (not upload_url or not str(upload_url).startswith(("http://", "https://"))) and video_b64:
                        upload_url = f"data:video/mp4;base64,{video_b64}"

                    if upload_url:
                        payload = _make_success_body(project_id, upload_url, audio_b64=audio_b64)
                    else:
                        payload = _make_error_body(project_id, "Video upload failed")
                    log.info(
                        "Webhook JSON: url=%s, headers_keys=%s, keys=%s, video_url_prefix=%s, video_url_len=%s, audio_len=%s, project_id=%s",
                        callback_url,
                        list((callback_headers or {}).keys()),
                        list(payload.keys()),
                        str(payload.get("video_url", ""))[:32],
                        len(payload.get("video_url", "")) if isinstance(payload.get("video_url", ""), str) else 0,
                        len(payload.get("audio", "")) if isinstance(payload.get("audio", ""), str) else 0,
                        payload.get("project_id"),
                    )
                    try:
                        _http_post(callback_url, payload, headers=callback_headers)
                        log.info("✅ callback posted to %s (url len=%s, audio len=%s)", callback_url, len(upload_url) if upload_url else 0, len(audio_b64) if audio_b64 else 0)
                    except urllib.error.HTTPError as e:
                        if e.code == 422:
                            # сервер ожидает multipart form (без файла)
                            resp = _post_fields_multipart(callback_url, payload)
                            log.info("✅ callback (multipart fields) posted to %s: %s", callback_url, resp[:200])
                        else:
                            raise
            except Exception as e:
                log.error("❌ callback post failed: %s", e)
        elif (not disable_callback) and callback_url and project_id is None:
            log.warning("⚠️ webhook_url задан, но project_id отсутствует — пропускаю коллбэк.")

        # RunPod /job-done: лимит размера JSON — убираем гигантский base64, отдаём только video_url
        return _sanitize_result_for_runpod(result)

    except Exception as e:
        err_msg = f"{e.__class__.__name__}: {e}"
        log.error("❌ handler error: %s", err_msg)
        log.debug("traceback:\n%s", traceback.format_exc())

        if callback_url and project_id is not None:
            try:
                payload = _make_error_body(project_id, err_msg)
                try:
                    _http_post(callback_url, payload, headers=callback_headers)
                    log.info("✅ callback ERROR posted to %s", callback_url)
                except urllib.error.HTTPError as e:
                    if e.code == 422:
                        resp = _post_fields_multipart(callback_url, payload)
                        log.info("✅ callback ERROR (multipart fields) posted to %s: %s", callback_url, resp[:200])
                    else:
                        raise
            except Exception as ee:
                log.error("❌ callback error-post failed: %s", ee)
        elif callback_url and project_id is None:
            log.warning("⚠️ webhook_url задан, но project_id отсутствует — пропускаю коллбэк ошибки.")

        # Keep RunPod result minimal (no error text, no large payloads)
        return {"status": "failed", "video_url": ""}

# Регистрируем обработчик для RunPod Serverless
runpod.serverless.start({"handler": handler})