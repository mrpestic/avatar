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

# импортируем исходный handler из их проекта
# если у тебя основной обработчик в другом файле/имени, поправь импорт ниже
import handler as base_handler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("callback-handler")

def _http_post(url: str, payload: dict, headers: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {"Content-Type": "application/json"})
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")

def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

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

        # если заданы и callback_url, и project_id — отправим успешный коллбэк в новом формате
        if callback_url and project_id is not None:
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
                    # 1080p транскод перед отправкой
                    tx_path = _transcode_to_1080p(video_path) or video_path
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
        elif callback_url and project_id is None:
            log.warning("⚠️ webhook_url задан, но project_id отсутствует — пропускаю коллбэк.")

        # вернём обычный ответ как раньше
        return result

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

        # и в сам ответ тоже вернём ошибку (как раньше делал ранпод)
        return {"error": err_msg, "status": "ERROR"}

# Регистрируем обработчик для RunPod Serverless
runpod.serverless.start({"handler": handler})