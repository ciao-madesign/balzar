; Script Inno Setup per creare Balzar-Setup.exe (Windows).
; Avvolge dist\balzar.exe (prodotto da `pyinstaller balzar.spec`) in un
; installer con voce nel menu Start -- l'esperienza "come Word".
;
; Uso: installa Inno Setup (https://jrsoftware.org/isdl.php), poi apri questo
; file con Inno Setup Compiler e premi Compile (oppure: ISCC.exe packaging\balzar.iss).
; Il setup.exe finisce in dist\.
;
; Nota: l'exe non e' firmato -> SmartScreen mostra un avviso al primo avvio
; ("Ulteriori informazioni" -> "Esegui comunque"). La firma e' rimandata
; oltre la beta (vedi ROADMAP.md).

#define AppVersion "0.9.0b1"

[Setup]
AppName=Balzar
AppVersion={#AppVersion}
AppPublisher=Michele Aldeni
DefaultDirName={autopf}\Balzar
DefaultGroupName=Balzar
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Balzar-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; icona dell'installer (rigenerata in blu, vedi assets/)
SetupIconFile=..\assets\balzar.ico

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un'icona sul desktop"; Flags: unchecked

[Files]
; onefile: PyInstaller produce un solo balzar.exe
Source: "..\dist\balzar.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Balzar"; Filename: "{app}\balzar.exe"
Name: "{autodesktop}\Balzar"; Filename: "{app}\balzar.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\balzar.exe"; Description: "Avvia Balzar"; Flags: nowait postinstall skipifsilent
