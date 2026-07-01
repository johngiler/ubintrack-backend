import os
import socket
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Obtener configuración del .env o usar valores por defecto
printer_ip = os.getenv('ZEBRA_PRINTER_IP', '190.122.189.156')
printer_port = int(os.getenv('ZEBRA_PRINTER_PORT', '9100'))


def print_to_zebra(zpl_data):
    """
    Envía datos ZPL a una impresora Zebra en red.

    Args:
        zpl_data (str): Código ZPL a imprimir

    Returns:
        bool: True si la impresión fue exitosa, False si hubo error
    """
    try:
        # Crear un socket TCP/IP
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Establecer un timeout de 5 segundos
        sock.settimeout(5)

        # Conectar al servidor
        sock.connect((printer_ip, printer_port))

        # Enviar los datos ZPL
        sock.send(zpl_data.encode('utf-8'))

        # Cerrar la conexión
        sock.close()

        return True

    except socket.timeout:
        print(
            f"Error: Timeout al conectar con la impresora {printer_ip}:{printer_port}")
        return False
    except ConnectionRefusedError:
        print(f"Error: Conexión rechazada por la impresora {printer_ip}:{printer_port}")
        return False
    except Exception as e:
        print(f"Error al imprimir: {str(e)}")
        return False
