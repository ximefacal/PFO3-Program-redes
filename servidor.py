import os
import socket
import threading
import queue
import itertools

from protocolo import enviar_mensaje, recibir_mensaje


# Configuración del servidor
# Escucha solo en 127.0.0.1 (la propia máquina) y no en 0.0.0.0 (todas las interfaces):
# así el servicio no queda expuesto a la red.
# Se usa 127.0.0.1 y no "localhost": en Windows, "localhost" se resuelve primero
# a la dirección IPv6 ::1 y cada conexión espera unos 2 segundos antes de
# probar por IPv4.
HOST = "127.0.0.1"
PUERTO = 5000

# Workers disponibles (cada uno corre en su propia terminal / máquina)
WORKERS = [
    ("127.0.0.1", 6001),
    ("127.0.0.1", 6002),
    ("127.0.0.1", 6003),
]

CANTIDAD_DESPACHADORES = 12    # Hilos que toman tareas de la cola (3 workers x 4 hilos)
TIEMPO_MAXIMO_TAREA = 30       # Segundos máximos de espera por un worker


# ---------------------------------------------------------------
# Cola de mensajes
# ---------------------------------------------------------------
# queue.Queue cumple el rol de RabbitMQ dentro de este prototipo:
# los hilos que atienden clientes PUBLICAN tareas en la cola y los
# despachadores las CONSUMEN para enviarlas a los workers.
# Es segura para usar desde varios hilos (no necesita Lock).
cola_tareas = queue.Queue()

# Reparto rotativo (round-robin) entre workers, como hace un balanceador
siguiente_worker = itertools.cycle(range(len(WORKERS)))
lock_reparto = threading.Lock()

# Numeración global de tareas
contador_tareas = itertools.count(1)


def elegir_worker():
    # Devuelve el índice del próximo worker en orden rotativo.
    with lock_reparto:
        return next(siguiente_worker)


def enviar_a_worker(tarea):
    # Envía la tarea a un worker y espera el resultado.
    # Si un worker no responde, prueba con el siguiente (tolerancia a fallos).
    for intento in range(len(WORKERS)):
        host, puerto = WORKERS[elegir_worker()]

        try:
            with socket.create_connection((host, puerto), timeout=TIEMPO_MAXIMO_TAREA) as conexion:
                enviar_mensaje(conexion, tarea)
                archivo = conexion.makefile("r", encoding="utf-8")
                respuesta = recibir_mensaje(archivo)

                if respuesta is not None:
                    return respuesta

        except (OSError, ValueError) as error:
            print(f"Worker {host}:{puerto} no disponible ({error}). Probando otro...")

    return {"id": tarea["id"], "estado": "error",
            "resultado": "No hay workers disponibles", "worker": "-", "hilo": "-"}


def despachador():
    # Consume tareas de la cola y las distribuye a los workers.
    while True:
        tarea, cola_respuesta = cola_tareas.get()
        respuesta = enviar_a_worker(tarea)
        cola_respuesta.put(respuesta)
        cola_tareas.task_done()


# ---------------------------------------------------------------
# Atención de clientes
# ---------------------------------------------------------------

def atender_cliente(conn, addr):
    # Recibe tareas de un cliente, las publica en la cola y le devuelve los resultados.
    print(f"Cliente conectado: {addr[0]}:{addr[1]}")

    try:
        with conn:
            archivo = conn.makefile("r", encoding="utf-8")

            while True:
                pedido = recibir_mensaje(archivo)

                # Si no hay datos, el cliente cerró la conexión
                if pedido is None:
                    break

                if "tipo" not in pedido or "datos" not in pedido:
                    enviar_mensaje(conn, {"estado": "error",
                                          "resultado": "La tarea debe tener 'tipo' y 'datos'"})
                    continue

                tarea = {"id": next(contador_tareas),
                         "tipo": pedido["tipo"],
                         "datos": pedido["datos"]}

                print(f"Tarea {tarea['id']} recibida de {addr[0]}: {tarea['tipo']}")

                # Cada tarea lleva su propia cola de respuesta, para que
                # el resultado vuelva al cliente que la pidió
                cola_respuesta = queue.Queue(maxsize=1)
                cola_tareas.put((tarea, cola_respuesta))

                respuesta = cola_respuesta.get()
                enviar_mensaje(conn, respuesta)

    except (ConnectionError, ValueError) as error:
        print(f"Error con el cliente {addr[0]}: {error}")

    print(f"Cliente desconectado: {addr[0]}:{addr[1]}")


def inicializar_socket():
    # Crea el socket TCP del servidor principal.
    try:
        # Configuración del socket TCP/IP
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Al cerrar un servidor, sus conexiones quedan un tiempo en estado
        # TIME_WAIT y el puerto no se puede reutilizar enseguida.
        # SO_REUSEADDR permite reiniciarlo sin esperar. 
        if os.name != "nt":
            servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        servidor.bind((HOST, PUERTO))
        servidor.listen(20)
        print(f"Servidor escuchando en {HOST}:{PUERTO}")
        return servidor

    except OSError as error:
        print(f"Error al iniciar el servidor: {error}")
        print("Es posible que el puerto esté ocupado.")
        return None


def iniciar_despachadores():
    # Crea los hilos que consumen la cola de tareas.
    for numero in range(CANTIDAD_DESPACHADORES):
        hilo = threading.Thread(target=despachador, name=f"despachador-{numero + 1}", daemon=True)
        hilo.start()
    print(f"{CANTIDAD_DESPACHADORES} despachadores esperando tareas en la cola")


# ****Función principal del servidor.****
def main():
    servidor = inicializar_socket()

    if servidor is None:
        return

    iniciar_despachadores()
    print(f"Workers configurados: {', '.join(f'{h}:{p}' for h, p in WORKERS)}")

    try:
        while True:
            # Un hilo por cliente
            conn, addr = servidor.accept()
            hilo = threading.Thread(target=atender_cliente, args=(conn, addr), daemon=True)
            hilo.start()

    except KeyboardInterrupt:
        print("\nServidor detenido por el usuario.")

    finally:
        servidor.close()
        print("Servidor cerrado.")


# Ejecutar el servidor
if __name__ == "__main__":
    main()
