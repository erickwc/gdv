"use strict";

const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const { app } = require("electron");

// Sidecar de Python: protocolo JSON por linea sobre stdio (ver
// python/sidecar.py). Cada llamada manda {id, method, params} y espera
// {id, result} / {id, error}; los eventos sin solicitar llegan como
// {event, data} y se redistribuyen a quien se haya suscrito con onEvent().
class PythonBridge {
  constructor() {
    this.proc = null;
    this.nextId = 1;
    this.pending = new Map();
    this.eventListeners = new Set();
    this._buffer = "";
  }

  start() {
    let command;
    let args;
    let cwd;

    if (app.isPackaged) {
      // Empaquetado: nada de venv/interprete de sistema (el usuario no
      // tiene por que tener Python instalado) -- se usa el binario que
      // arma PyInstaller a partir de sidecar.py, copiado a
      // Contents/Resources/python-dist por electron-builder (ver
      // "extraResources" en package.json y "build:python" en package.json).
      // Ese binario YA ES el ejecutable final: se corre directo, sin
      // interprete ni argumentos.
      const sidecarDir = path.join(process.resourcesPath, "python-dist", "sidecar");
      const sidecarExe = process.platform === "win32"
        ? path.join(sidecarDir, "sidecar.exe")
        : path.join(sidecarDir, "sidecar");
      command = sidecarExe;
      args = [];
      cwd = sidecarDir;
    } else {
      const pythonDir = path.join(__dirname, "..", "..", "python");
      const venvPython = process.platform === "win32"
        ? path.join(pythonDir, "venv", "Scripts", "python.exe")
        : path.join(pythonDir, "venv", "bin", "python");
      const pythonExe = fs.existsSync(venvPython) ? venvPython : (process.platform === "win32" ? "python" : "python3");
      command = pythonExe;
      args = ["-u", "sidecar.py"];
      cwd = pythonDir;
    }

    // Empaquetada, la app puede quedar en una carpeta de solo lectura
    // (Program Files en Windows), asi que el sidecar NO puede guardar
    // config.json/plantillas/texturas junto a su ejecutable -- se le pasa la
    // carpeta de datos del usuario y engine.py la respeta (ver GDV_DATA_DIR
    // ahi). En desarrollo no se manda nada y todo queda junto al codigo.
    const env = app.isPackaged
      ? { ...process.env, GDV_DATA_DIR: app.getPath("userData") }
      : process.env;

    this.proc = spawn(command, args, {
      cwd,
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.proc.stdout.setEncoding("utf-8");
    this.proc.stdout.on("data", (chunk) => this._onData(chunk));

    this.proc.stderr.setEncoding("utf-8");
    this.proc.stderr.on("data", (chunk) => {
      console.error("[python]", chunk.toString());
    });

    this.proc.on("error", (err) => {
      console.error("[python] no se pudo iniciar el sidecar:", err);
      this._rejectAllPending(err);
    });

    this.proc.on("exit", (code, signal) => {
      console.error(`[python] sidecar termino (code=${code}, signal=${signal})`);
      this._rejectAllPending(new Error("El proceso Python terminó inesperadamente"));
    });

    return new Promise((resolve) => {
      const onReady = (event, data) => {
        if (event === "onReady") {
          this.eventListeners.delete(onReady);
          resolve();
        }
      };
      this.onEvent(onReady);
    });
  }

  _rejectAllPending(err) {
    for (const { reject } of this.pending.values()) reject(err);
    this.pending.clear();
  }

  _onData(chunk) {
    this._buffer += chunk;
    let idx;
    while ((idx = this._buffer.indexOf("\n")) >= 0) {
      const line = this._buffer.slice(0, idx).trim();
      this._buffer = this._buffer.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch (err) {
        console.error("[python] linea no-JSON (probablemente un print de depuracion):", line);
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(msg, "event")) {
        for (const listener of this.eventListeners) listener(msg.event, msg.data);
      } else if (Object.prototype.hasOwnProperty.call(msg, "id")) {
        const entry = this.pending.get(msg.id);
        if (!entry) continue;
        this.pending.delete(msg.id);
        if (Object.prototype.hasOwnProperty.call(msg, "error")) entry.reject(new Error(msg.error));
        else entry.resolve(msg.result);
      }
    }
  }

  call(method, params = []) {
    if (!this.proc) return Promise.reject(new Error("El sidecar de Python no esta corriendo"));
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending.set(id, { resolve, reject });
      this.proc.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    });
  }

  onEvent(listener) {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  stop() {
    if (this.proc) {
      const pid = this.proc.pid;
      if (process.platform === "win32") {
        // proc.kill() en Windows solo mata al sidecar -- NO a sus hijos
        // (ffmpeg, lanzado con subprocess.Popen dentro de Api). Ese ffmpeg
        // queda huerfano corriendo en segundo plano y termina trabado
        // escribiendo su progreso (-progress pipe:1) a una tuberia que ya
        // nadie lee. taskkill /T mata el arbol completo (sidecar + ffmpeg).
        spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"]);
      } else {
        this.proc.kill();
      }
      this.proc = null;
    }
  }
}

module.exports = { PythonBridge };
