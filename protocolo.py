import json


# ---------------------------------------------------------------
# Protocolo de mensajes compartido por cliente, servidor y workers
# ---------------------------------------------------------------
# TCP es un flujo continuo de bytes: no sabe dónde termina un mensaje
# y empieza el siguiente. Por eso cada mensaje se envía como un JSON
# en una sola línea, terminado en "\n".


def enviar_mensaje(sock, mensaje):
    # Convierte el diccionario a JSON, agrega el separador y lo envía completo.
    datos = json.dumps(mensaje) + "\n"
    sock.sendall(datos.encode("utf-8"))


def recibir_mensaje(archivo):
    # Lee una línea del socket (usando makefile) y la convierte en diccionario.
    # Devuelve None si la otra parte cerró la conexión.
    linea = archivo.readline()

    if not linea:
        return None

    return json.loads(linea)
