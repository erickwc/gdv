"""
Proceso Python aparte (sidecar) que Electron controla por stdio. Protocolo:
JSON por linea.

Peticion (Electron -> Python), stdin:
    {"id": 1, "method": "set_template", "params": ["C:\\...png"]}

Respuesta (Python -> Electron), stdout:
    {"id": 1, "result": {...}}
    {"id": 1, "error": "mensaje"}

Evento sin solicitar (Python -> Electron), stdout -- reemplaza los
window.evaluate_js(...) que usaba la version pywebview:
    {"event": "onProgress", "data": 0.42}

Cada peticion se despacha en su propio hilo para que una llamada lenta
(por ejemplo, sondear un video) no bloquee al resto -- el mismo patron de
concurrencia que ya tenia la version pywebview (cada llamada de JS corre
independiente) y que Api.* ya maneja con sus propios locks/threads donde
hace falta (_texture_lock, threading.Thread en jobs largos).
"""

import json
import sys
import threading

from api import Api

_write_lock = threading.Lock()


def _write(obj):
    with _write_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _emit(event, data):
    _write({"event": event, "data": data})


def _handle(api, request):
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or []
    try:
        fn = getattr(api, method)
    except AttributeError:
        _write({"id": req_id, "error": f"Metodo desconocido: {method}"})
        return
    try:
        result = fn(*params)
    except Exception as exc:
        _write({"id": req_id, "error": str(exc)})
        return
    if req_id is not None:
        _write({"id": req_id, "result": result})


def main():
    # Electron habla utf-8 por las tuberias del proceso hijo -- se fuerza
    # explicito aca porque la consola de Windows no siempre lo trae por
    # default, y un caracter fuera de ascii (rutas, nombres de archivo)
    # rompe el protocolo si stdout/stdin quedan en cp1252.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    api = Api(emit=_emit)
    _write({"event": "onReady", "data": None})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        threading.Thread(target=_handle, args=(api, request), daemon=True).start()


if __name__ == "__main__":
    main()
