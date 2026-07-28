#!/usr/bin/env bash
#
# Crea un installer .dmg per macOS da dist/Balzar.app.
# Il .dmg si apre mostrando l'app + un alias di Applicazioni: l'utente
# trascina Balzar dentro Applicazioni -- il gesto "come Word" che tutti
# conoscono. Usa solo strumenti di sistema macOS (hdiutil), niente extra.
#
# Uso (dalla radice del repo, DOPO `pyinstaller balzar.spec`):
#   bash packaging/make_dmg.sh
#
set -euo pipefail

APP="dist/Balzar.app"
[ -d "$APP" ] || { echo "errore: $APP non trovato. Esegui prima: pyinstaller balzar.spec"; exit 1; }

# versione dal pacchetto (fallback se non importabile)
VERSION="$(python3 -c "from balzar import __version__; print(__version__)" 2>/dev/null || echo "0.9.0b1")"
DMG="dist/Balzar-${VERSION}.dmg"

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"   # alias per il drag-and-drop

rm -f "$DMG"
hdiutil create -volname "Balzar" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo "Creato: $DMG"
echo "I tester lo aprono e trascinano Balzar in Applicazioni."
echo "Primo avvio (app non firmata): clic destro su Balzar -> Apri -> Apri."
