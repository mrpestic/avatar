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

# импортируем исходный handler из их проекта
# если у тебя основной обработчик в другом файле/имени, поправь импорт ниже
import handler as base_handler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("callback-handler")

def _http_post(url: str, payload: dict, headers: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")

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
    project_id = job_input.get("project_id")

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
                # 1) получаем URL видео совместимым способом: либо из файла, либо из base64
                upload_url = ""
                if video_path and os.path.exists(video_path):
                    upload_url = _rp_upload_file_or_bytes(file_path=video_path)
                elif video_b64:
                    data = base64.b64decode(video_b64)
                    upload_url = _rp_upload_file_or_bytes(data_bytes=data, filename="output.mp4")

                # 2) подготавливаем аудио в base64, если путь известен
                audio_b64 = None
                try:
                    if audio_path and os.path.exists(audio_path):
                        with open(audio_path, "rb") as af:
                            audio_b64 = base64.b64encode(af.read()).decode("utf-8")
                except Exception as e:
                    log.warning("Не удалось прочитать аудио для коллбэка: %s", e)

                if upload_url:
                    payload = _make_success_body(project_id, upload_url, audio_b64=audio_b64)
                else:
                    payload = _make_error_body(project_id, "Video upload failed")
                _http_post(callback_url, payload, headers=callback_headers)
                log.info("✅ callback posted to %s", callback_url)
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
                _http_post(callback_url, payload, headers=callback_headers)
                log.info("✅ callback ERROR posted to %s", callback_url)
            except Exception as ee:
                log.error("❌ callback error-post failed: %s", ee)
        elif callback_url and project_id is None:
            log.warning("⚠️ webhook_url задан, но project_id отсутствует — пропускаю коллбэк ошибки.")

        # и в сам ответ тоже вернём ошибку (как раньше делал ранпод)
        return {"error": err_msg, "status": "ERROR"}

# Регистрируем обработчик для RunPod Serverless
runpod.serverless.start({"handler": handler})