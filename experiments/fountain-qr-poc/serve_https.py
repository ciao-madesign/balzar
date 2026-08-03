#!/usr/bin/env python3
"""Serve questa cartella in HTTPS con un certificato autofirmato, cosi'
un telefono sulla stessa rete puo' usare la fotocamera (i browser negano
getUserMedia su http per qualunque host diverso da localhost -- regola
della piattaforma, non aggirabile).

Uso:
    python3 serve_https.py [porta]

Poi sul telefono: https://<ip-di-questo-computer>:<porta>/receiver.html
(accetta l'avviso di certificato non riconosciuto al primo accesso --
e' autofirmato, non emesso da un'autorita' pubblica, ma la connessione
resta comunque cifrata).
"""
import http.server
import os
import ssl
import subprocess
import sys
import tempfile

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899


def ensure_cert(cert_path: str, key_path: str) -> None:
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "365", "-nodes",
            "-subj", "/CN=localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    cert_dir = tempfile.gettempdir()
    cert_path = os.path.join(cert_dir, "fountain_poc_cert.pem")
    key_path = os.path.join(cert_dir, "fountain_poc_key.pem")
    ensure_cert(cert_path, key_path)

    server = http.server.ThreadingHTTPServer(
        ("0.0.0.0", PORT), http.server.SimpleHTTPRequestHandler
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"https://localhost:{PORT}/sender.html   (mittente, sul computer)")
    print(f"https://<ip-di-questo-computer>:{PORT}/receiver.html   (ricevitore, sul telefono)")
    print("Ctrl+C per fermare.")
    server.serve_forever()


if __name__ == "__main__":
    main()
