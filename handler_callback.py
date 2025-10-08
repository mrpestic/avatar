import os
import json
import base64
import logging
import traceback
import urllib.request
import urllib.error
import runpod

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

def _make_success_body(video_b64: str | None, extra: dict | None = None):
    body = {"status": "SUCCESS"}
    if video_b64 is not None:
        body["video_base64"] = video_b64
    if extra:
        body["meta"] = extra
    return body

def _make_error_body(message: str, extra: dict | None = None):
    body = {"status": "ERROR", "message": message}
    if extra:
        body["meta"] = extra
    return body

def handler(job: dict):
    """
    Расширенный обработчик:
    - поддерживает input.callback_url (опционально)
    - всегда возвращает обычный ответ (для совместимости)
    - если задан callback_url, по завершении отдельно шлёт POST с SUCCESS/ERROR
    """
    job_input = job.get("input", {}) or {}
    # берём callback_url/callback_headers из входа или из переменных окружения (если не заданы в job.input)
    callback_url = job_input.get("callback_url") or os.getenv("CALLBACK_URL")
    env_headers = os.getenv("CALLBACK_HEADERS")
    callback_headers = job_input.get("callback_headers") or (json.loads(env_headers) if env_headers else None)  # опционально: {"Authorization":"Bearer ..."}
    meta = {"job_id": job.get("id")}

    try:
        # вызываем оригинальный handler из проекта
        result = base_handler.handler(job)

        # извлечём видео (у них обычно {"video": "<b64>"}), но оставим гибко
        video_b64 = None
        if isinstance(result, dict):
            if "video" in result and isinstance(result["video"], str):
                video_b64 = result["video"]
            elif "video_base64" in result and isinstance(result["video_base64"], str):
                video_b64 = result["video_base64"]

        # если задан callback_url — отправим успешный коллбэк
        if callback_url:
            try:
                payload = _make_success_body(video_b64, extra=meta | {"raw_result": result})
                _http_post(callback_url, payload, headers=callback_headers)
                log.info("✅ callback SUCCESS posted to %s", callback_url)
            except Exception as e:
                log.error("❌ callback post failed: %s", e)

        # вернём обычный ответ как раньше
        return result

    except Exception as e:
        err_msg = f"{e.__class__.__name__}: {e}"
        log.error("❌ handler error: %s", err_msg)
        log.debug("traceback:\n%s", traceback.format_exc())

        if callback_url:
            try:
                payload = _make_error_body(err_msg, extra=meta)
                _http_post(callback_url, payload, headers=callback_headers)
                log.info("✅ callback ERROR posted to %s", callback_url)
            except Exception as ee:
                log.error("❌ callback error-post failed: %s", ee)

        # и в сам ответ тоже вернём ошибку (как раньше делал ранпод)
        return {"error": err_msg, "status": "ERROR"}

# Регистрируем обработчик для RunPod Serverless
runpod.serverless.start({"handler": handler})