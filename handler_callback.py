import os
import json
import base64
import logging
import traceback
import urllib.request
import urllib.error
import runpod
from runpod.serverless.utils import rp_upload

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

def _make_success_body(project_id: int | None, video_url: str, message: str = ""):
    return {"project_id": project_id, "video_url": video_url, "status": "success", "message": message}

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

        # извлечём видео как base64 и как локальный путь если есть
        video_b64 = None
        video_path = None
        if isinstance(result, dict):
            if "video" in result and isinstance(result["video"], str):
                video_b64 = result["video"]
            elif "video_base64" in result and isinstance(result["video_base64"], str):
                video_b64 = result["video_base64"]
            if "video_path" in result and isinstance(result["video_path"], str):
                video_path = result["video_path"]

        # если заданы и callback_url, и project_id — отправим успешный коллбэк в новом формате
        if callback_url and project_id is not None:
            try:
                # 1) получаем URL видео: если есть путь — зальём файл; иначе — декодим base64 и зальём как bytes
                upload_url = ""
                if video_path and os.path.exists(video_path):
                    upload_url = rp_upload.upload_file(video_path)
                elif video_b64:
                    data = base64.b64decode(video_b64)
                    upload_url = rp_upload.upload_bytes(data, "output.mp4")
                else:
                    upload_url = ""

                payload = _make_success_body(project_id, upload_url)
                _http_post(callback_url, payload, headers=callback_headers)
                log.info("✅ callback SUCCESS posted to %s", callback_url)
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