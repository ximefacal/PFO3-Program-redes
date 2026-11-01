import os
import socket
import sqlite3
import secrets
import sys
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from werkzeug.security import generate_password_hash, check_password_hash

from protocolo import enviar_mensaje, recibir_mensaje


# Configuración del worker
# Escucha solo en 127.0.0.1 (la propia máquina) y no en 0.0.0.0 (todas las interfaces).
# En producción escucharía solo en la interfaz de la red privada.
# Se usa 127.0.0.1 y no "localhost": en Windows, "localhost" se resuelve primero
# a la dirección IPv6 ::1 y cada conexión espera unos 2 segundos antes de
# probar por IPv4.

HOST = "127.0.0.1"
PUERTO_BASE = 6000          # El worker N escucha en el puerto 6000 + N
HILOS_POR_WORKER = 4        # Tamaño del pool de hilos de cada worker

# Base de datos COMPARTIDA por todos los workers.
# En el diseño completo es PostgreSQL; en el prototipo, un archivo SQLite.
# Como cualquier worker puede atender cualquier pedido, los usuarios,
# las sesiones y las tareas no pueden guardarse en la memoria de un worker.
DATABASE = "tareas.db"


class ErrorTarea(Exception):
    # Error causado por los datos del usuario (se le informa al cliente).
    pass


# ---------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------

def conectar_db():
    # timeout: si otro worker está escribiendo, espera hasta 10 segundos
    return sqlite3.connect(DATABASE, timeout=10)


def inicializar_db():
    # Crea las tablas si todavía no existen.
    conexion = None
    try:
        conexion = conectar_db()
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT UNIQUE NOT NULL,
                contrasena_hash TEXT NOT NULL
            )
        """)

        # Las sesiones se guardan en la base para que cualquier worker
        # pueda reconocer a un usuario que inició sesión en otro worker
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sesiones (
                token TEXT PRIMARY KEY,
                usuario_id INTEGER NOT NULL,
                fecha_inicio TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tareas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                titulo TEXT NOT NULL,
                completada INTEGER NOT NULL DEFAULT 0,
                fecha_creacion TEXT NOT NULL
            )
        """)

        conexion.commit()
        return True

    except sqlite3.Error as error:
        print(f"Error al acceder a la base de datos: {error}")
        return False

    finally:
        if conexion is not None:
            conexion.close()


def obtener_usuario_id(cursor, token):
    # Devuelve el usuario dueño del token, o lanza un error si no es válido.
    cursor.execute("SELECT usuario_id FROM sesiones WHERE token = ?", (token,))
    fila = cursor.fetchone()

    if fila is None:
        raise ErrorTarea("Sesión inválida: primero tenés que iniciar sesión")

    return fila[0]


def obtener_texto(datos, campo):
    # Lee un campo de texto obligatorio de los datos recibidos.
    valor = datos.get(campo) if isinstance(datos, dict) else None

    if not valor or not str(valor).strip():
        raise ErrorTarea(f"Falta el campo '{campo}'")

    return str(valor).strip()


# ---------------------------------------------------------------
# Operaciones del Sistema de Gestión de Tareas
# ---------------------------------------------------------------

def registrar_usuario(datos):
    usuario = obtener_texto(datos, "usuario")
    contrasena = obtener_texto(datos, "contraseña")

    
    contrasena_hash = generate_password_hash(contrasena)

    conexion = conectar_db()
    try:
        conexion.execute(
            "INSERT INTO usuarios (usuario, contrasena_hash) VALUES (?, ?)",
            (usuario, contrasena_hash)
        )
        conexion.commit()

    except sqlite3.IntegrityError:
        raise ErrorTarea("El usuario ya existe")

    finally:
        # Cerrar siempre la conexión (si queda abierta, bloquea la base)
        conexion.close()

    return f"Usuario '{usuario}' registrado correctamente"


def iniciar_sesion(datos):
    usuario = obtener_texto(datos, "usuario")
    contrasena = obtener_texto(datos, "contraseña")

    conexion = conectar_db()
    try:
        cursor = conexion.cursor()
        cursor.execute(
            "SELECT id, contrasena_hash FROM usuarios WHERE usuario = ?",
            (usuario,)
        )
        fila = cursor.fetchone()

        # Mismo mensaje si el usuario no existe o la contraseña es incorrecta
        if fila is None or not check_password_hash(fila[1], contrasena):
            raise ErrorTarea("Credenciales inválidas")

        # Token aleatorio que el cliente envía en cada pedido siguiente
        token = secrets.token_hex(16)
        cursor.execute(
            "INSERT INTO sesiones (token, usuario_id, fecha_inicio) VALUES (?, ?, ?)",
            (token, fila[0], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conexion.commit()

    finally:
        conexion.close()

    return {"mensaje": f"Inicio de sesión exitoso. Bienvenido/a, {usuario}", "token": token}


def crear_tarea(datos):
    token = obtener_texto(datos, "token")
    titulo = obtener_texto(datos, "titulo")

    conexion = conectar_db()
    try:
        cursor = conexion.cursor()
        usuario_id = obtener_usuario_id(cursor, token)

        cursor.execute(
            "INSERT INTO tareas (usuario_id, titulo, fecha_creacion) VALUES (?, ?, ?)",
            (usuario_id, titulo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conexion.commit()
        id_tarea = cursor.lastrowid

    finally:
        conexion.close()

    return f"Tarea #{id_tarea} creada: {titulo}"


def listar_tareas(datos):
    token = obtener_texto(datos, "token")

    conexion = conectar_db()
    try:
        cursor = conexion.cursor()
        usuario_id = obtener_usuario_id(cursor, token)

        cursor.execute(
            "SELECT id, titulo, completada, fecha_creacion FROM tareas "
            "WHERE usuario_id = ? ORDER BY id",
            (usuario_id,)
        )
        filas = cursor.fetchall()

    finally:
        conexion.close()

    return [
        {"id": f[0], "titulo": f[1], "completada": bool(f[2]), "fecha": f[3]}
        for f in filas
    ]


def completar_tarea(datos):
    token = obtener_texto(datos, "token")
    id_tarea = obtener_texto(datos, "id_tarea")

    conexion = conectar_db()
    try:
        cursor = conexion.cursor()
        usuario_id = obtener_usuario_id(cursor, token)

        # Solo se puede completar una tarea propia
        cursor.execute(
            "UPDATE tareas SET completada = 1 WHERE id = ? AND usuario_id = ?",
            (id_tarea, usuario_id)
        )
        conexion.commit()
        modificadas = cursor.rowcount

    finally:
        conexion.close()

    if modificadas == 0:
        raise ErrorTarea(f"No existe la tarea #{id_tarea} entre tus tareas")

    return f"Tarea #{id_tarea} marcada como completada"


def generar_reporte(datos):
    # Tarea pesada: resume las tareas del usuario.
    # La espera simula el tiempo de armar el reporte y subirlo a S3.
    token = obtener_texto(datos, "token")

    conexion = conectar_db()
    try:
        cursor = conexion.cursor()
        usuario_id = obtener_usuario_id(cursor, token)

        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(completada), 0) FROM tareas WHERE usuario_id = ?",
            (usuario_id,)
        )
        total, completadas = cursor.fetchone()

    finally:
        conexion.close()

    time.sleep(2)

    return f"Reporte: {total} tareas en total, {completadas} completadas, {total - completadas} pendientes"


# Cada tipo de pedido se resuelve con su función
OPERACIONES = {
    "registro": registrar_usuario,
    "login": iniciar_sesion,
    "crear_tarea": crear_tarea,
    "listar_tareas": listar_tareas,
    "completar_tarea": completar_tarea,
    "generar_reporte": generar_reporte,
}


# ---------------------------------------------------------------
# Atención de pedidos
# ---------------------------------------------------------------

def atender_pedido(conn, nombre_worker):
    # Lo ejecuta un hilo del pool: recibe una tarea, la procesa y responde.
    try:
        with conn:
            archivo = conn.makefile("r", encoding="utf-8")
            tarea = recibir_mensaje(archivo)

            if tarea is None:
                return

            hilo = threading.current_thread().name
            print(f"[{nombre_worker}] Tarea {tarea['id']} ({tarea['tipo']}) en {hilo}")

            try:
                operacion = OPERACIONES.get(tarea["tipo"])
                if operacion is None:
                    raise ErrorTarea(f"Tipo de tarea desconocido: {tarea['tipo']}")

                resultado = operacion(tarea["datos"])
                respuesta = {"id": tarea["id"], "estado": "ok", "resultado": resultado}

            except ErrorTarea as error:
                respuesta = {"id": tarea["id"], "estado": "error", "resultado": str(error)}

            except sqlite3.Error as error:
                print(f"[{nombre_worker}] Error de base de datos: {error}")
                respuesta = {"id": tarea["id"], "estado": "error",
                             "resultado": "Error en la base de datos"}

            # Se agrega quién resolvió la tarea, para ver la distribución
            respuesta["worker"] = nombre_worker
            respuesta["hilo"] = hilo

            enviar_mensaje(conn, respuesta)

    except (ConnectionError, ValueError) as error:
        print(f"[{nombre_worker}] Error con el servidor: {error}")


def iniciar_worker(numero):
    # Crea el socket del worker y reparte cada pedido entre los hilos del pool.
    puerto = PUERTO_BASE + numero
    nombre_worker = f"worker-{numero}"

    # Si la base de datos no es accesible, el worker no se inicia
    if not inicializar_db():
        return

    try:
        # Configuración del socket TCP/IP
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Al cerrar un servidor, sus conexiones quedan un tiempo en estado
        # TIME_WAIT y el puerto no se puede reutilizar enseguida.
        # SO_REUSEADDR permite reiniciarlo sin esperar. 
        if os.name != "nt":
            servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        servidor.bind((HOST, puerto))
        servidor.listen(20)

    except OSError as error:
        print(f"No se pudo iniciar {nombre_worker} en el puerto {puerto}: {error}")
        return

    print(f"{nombre_worker} escuchando en {HOST}:{puerto} con {HILOS_POR_WORKER} hilos")

    # Pool de hilos: en lugar de crear un hilo nuevo por pedido,
    # se reutiliza una cantidad fija de hilos. Así se limita el uso
    # de recursos aunque lleguen muchos pedidos al mismo tiempo.
    pool = ThreadPoolExecutor(
        max_workers=HILOS_POR_WORKER,
        thread_name_prefix=f"{nombre_worker}-hilo"
    )

    try:
        while True:
            conn, addr = servidor.accept()
            # El pedido queda esperando hasta que un hilo del pool esté libre
            pool.submit(atender_pedido, conn, nombre_worker)

    except KeyboardInterrupt:
        print(f"\n{nombre_worker} detenido por el usuario.")

    finally:
        pool.shutdown(wait=False)
        servidor.close()


# Ejecutar el worker: python worker.py <número>
if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Uso: python worker.py <número de worker>   (ejemplo: python worker.py 1)")
    else:
        iniciar_worker(int(sys.argv[1]))
