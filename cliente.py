import socket
import threading
import time

from protocolo import enviar_mensaje, recibir_mensaje


# Configuración del servidor
# Se usa 127.0.0.1 y no "localhost": en Windows, "localhost" se resuelve primero
# a la dirección IPv6 ::1 y cada conexión espera unos 2 segundos antes de
# probar por IPv4.

HOST = "127.0.0.1"
PUERTO = 5000


def conectar_con_reintentos(intentos=3, espera=2):
    # Intenta conectarse al servidor varias veces antes de rendirse
    for intento in range(intentos):
        try:
            print(f"Conectando al servidor (intento {intento + 1})...")
            cliente = socket.create_connection((HOST, PUERTO), timeout=60)
            print(f"Conectado a {HOST}:{PUERTO}")
            return cliente

        except OSError as error:
            print(f"Error de conexión: {error}")
            time.sleep(espera)

    print("No se pudo establecer la conexión. Verificá que servidor.py esté ejecutándose.")
    return None


def enviar_tarea(cliente, archivo, tipo, datos):
    # Envía una tarea al servidor y espera el resultado.
    enviar_mensaje(cliente, {"tipo": tipo, "datos": datos})
    respuesta = recibir_mensaje(archivo)

    if respuesta is None:
        raise ConnectionError("El servidor cerró la conexión")

    return respuesta


def mostrar_quien_resolvio(respuesta):
    print(f"  (resuelta por {respuesta.get('worker', '-')}, {respuesta.get('hilo', '-')})")


def mostrar_resultado(respuesta):
    if respuesta["estado"] == "ok":
        print(f"  OK: {respuesta['resultado']}")
    else:
        print(f"  ERROR: {respuesta['resultado']}")
    mostrar_quien_resolvio(respuesta)


def pedir_credenciales():
    usuario = input("Usuario: ").strip()
    contrasena = input("Contraseña: ").strip()
    return {"usuario": usuario, "contraseña": contrasena}


# ---------------------------------------------------------------
# Opciones del menú
# ---------------------------------------------------------------

def registrarse(cliente, archivo):
    respuesta = enviar_tarea(cliente, archivo, "registro", pedir_credenciales())
    mostrar_resultado(respuesta)


def iniciar_sesion(cliente, archivo):
    # Devuelve el token de sesión si el login es correcto, o None.
    respuesta = enviar_tarea(cliente, archivo, "login", pedir_credenciales())

    if respuesta["estado"] == "ok":
        print(f"  OK: {respuesta['resultado']['mensaje']}")
        mostrar_quien_resolvio(respuesta)
        return respuesta["resultado"]["token"]

    mostrar_resultado(respuesta)
    return None


def crear_tarea(cliente, archivo, token):
    titulo = input("Título de la tarea: ").strip()
    respuesta = enviar_tarea(cliente, archivo, "crear_tarea", {"token": token, "titulo": titulo})
    mostrar_resultado(respuesta)


def ver_tareas(cliente, archivo, token):
    respuesta = enviar_tarea(cliente, archivo, "listar_tareas", {"token": token})

    if respuesta["estado"] != "ok":
        mostrar_resultado(respuesta)
        return

    tareas = respuesta["resultado"]
    if not tareas:
        print("  No tenés tareas cargadas.")

    for tarea in tareas:
        estado = "✔" if tarea["completada"] else " "
        print(f"  [{estado}] #{tarea['id']} {tarea['titulo']}  ({tarea['fecha']})")

    mostrar_quien_resolvio(respuesta)


def completar_tarea(cliente, archivo, token):
    id_tarea = input("Número de la tarea a completar: ").strip()
    respuesta = enviar_tarea(cliente, archivo, "completar_tarea", {"token": token, "id_tarea": id_tarea})
    mostrar_resultado(respuesta)


def generar_reporte(cliente, archivo, token):
    print("  Generando reporte (tarea pesada, tarda unos 2 segundos)...")
    respuesta = enviar_tarea(cliente, archivo, "generar_reporte", {"token": token})
    mostrar_resultado(respuesta)


def reportes_en_paralelo(token, cantidad=6):
    # Simula varios clientes pidiendo reportes al mismo tiempo.
    # Cada hilo abre su propia conexión, como si fuera otro dispositivo.
    print(f"\nPidiendo {cantidad} reportes en paralelo...")
    resultados = []

    def pedir_uno():
        try:
            with socket.create_connection((HOST, PUERTO), timeout=60) as conexion:
                archivo = conexion.makefile("r", encoding="utf-8")
                resultados.append(enviar_tarea(conexion, archivo, "generar_reporte", {"token": token}))
        except OSError as error:
            print(f"  Error de conexión: {error}")

    inicio = time.time()
    hilos = [threading.Thread(target=pedir_uno) for _ in range(cantidad)]

    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    duracion = time.time() - inicio

    for respuesta in sorted(resultados, key=lambda r: r["id"]):
        print(f"  Tarea {respuesta['id']} resuelta por {respuesta['worker']} ({respuesta['hilo']})")

    print(f"\nTiempo total: {duracion:.1f} segundos "
          f"(en forma secuencial habría tardado {cantidad * 2} segundos)")


def mostrar_menu(token):
    estado = "sesión iniciada" if token else "sin sesión"
    print(f"\n===== Sistema Distribuido de Gestión de Tareas ({estado}) =====")
    print("1. Registrarse")
    print("2. Iniciar sesión")
    print("3. Crear tarea")
    print("4. Ver mis tareas")
    print("5. Completar tarea")
    print("6. Generar reporte")
    print("7. Pedir 6 reportes en paralelo (prueba de distribución)")
    print("8. Salir")
    return input("Elegí una opción: ").strip()


# ****Función principal del cliente.****
def main():
    cliente = conectar_con_reintentos()

    if cliente is None:
        return

    archivo = cliente.makefile("r", encoding="utf-8")

    # Token de sesión: se obtiene al iniciar sesión y se envía en cada pedido
    token = None

    try:
        while True:
            opcion = mostrar_menu(token)

            if opcion == "1":
                registrarse(cliente, archivo)
            elif opcion == "2":
                token = iniciar_sesion(cliente, archivo)
            elif opcion == "8":
                break
            elif opcion in ("3", "4", "5", "6", "7"):
                if token is None:
                    print("Primero tenés que iniciar sesión (opción 2).")
                elif opcion == "3":
                    crear_tarea(cliente, archivo, token)
                elif opcion == "4":
                    ver_tareas(cliente, archivo, token)
                elif opcion == "5":
                    completar_tarea(cliente, archivo, token)
                elif opcion == "6":
                    generar_reporte(cliente, archivo, token)
                else:
                    reportes_en_paralelo(token)
            else:
                print("Opción inválida.")

    except (ConnectionError, socket.timeout) as error:
        print(f"Error de conexión: {error}")

    finally:
        # Cerrar la conexión
        cliente.close()
        print("Conexión cerrada.")


# Ejecutar el cliente
if __name__ == "__main__":
    main()
