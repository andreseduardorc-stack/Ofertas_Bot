"""Servidor local para previsualizar el sitio web de ofertas."""

import http.server
import socketserver
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).parent.parent
DIRECTORIO_WEB = RAIZ / "web"
PUERTO = 8080


class ManejadorWeb(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORIO_WEB), **kwargs)


def iniciar():
    # Asegurar que existan los datos
    archivo_datos = DIRECTORIO_WEB / "data" / "ofertas.json"
    if not archivo_datos.exists():
        print("Generando web/data/ofertas.json inicial...")
        from herramientas.exportar_web import exportar_ofertas
        exportar_ofertas()

    print("=" * 65)
    print(f"Iniciando servidor web en http://localhost:{PUERTO}/")
    print(f"Sirviendo desde: {DIRECTORIO_WEB}")
    print("Presiona Ctrl+C para detener.")
    print("=" * 65)

    webbrowser.open(f"http://localhost:{PUERTO}/")

    with socketserver.TCPServer(("", PUERTO), ManejadorWeb) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")


if __name__ == "__main__":
    iniciar()
