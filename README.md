## Vail-CW Thetis

### Arranque de la aplicacion (Windows/PowerShell)

1. Abre una terminal en la raiz del proyecto:
- `cd C:\git\vail-cw-tethis`

2. Crea y activa un entorno virtual:
- `py -m venv .venv`
- `.\.venv\Scripts\Activate.ps1`

3. Instala dependencias:
- `python -m pip install --upgrade pip`
- `pip install -r requirements.txt`

4. Arranca la aplicacion:
- `python main.py`

Notas:
- La app es de escritorio (GUI) y abre su ventana al ejecutar `main.py`.
- La configuracion se guarda en `config.json`.
- Si `py` no existe en tu sistema, usa `python` en su lugar.

### Interfaz simplificada (modo CTRL)

La UI esta reducida para operar en modo teclado `CTRL`:
- Entrada fija en `CTRL` (sin seleccion de modo en pantalla).
- Audio principal solo de salida (`Audio Output`).
- `Vail MIDI Out` permite elegir explicitamente el dispositivo MIDI de hardware.
- `Sync Vail` envia al hardware el tipo de keyer y la velocidad actual (WPM).
- Sidetone con control unico `Sidetone On` + frecuencia + volumen.
- Se eliminan `Sidetone Route`, `Mix Mode` y `Local Volume`.
- Incluye `Decoded` para ver en tiempo real el texto CW decodificado, boton `Clear` y `Detach` para abrir una ventana desacoplada/redimensionable.
- `Decoded` permite ajustar `Font` (tamano de letra) en caliente.
- `Decode Audio In` permite decodificar desde una entrada de audio real (micro/line in) seleccionando `Decode Audio Input`.
- Ajusta `Decode Range (Hz)` (`Min/Max`) para cubrir el pitido CW de tu receptor y su posible deriva.
- Recomendacion inicial: rango de `150-300 Hz` de ancho (por ejemplo `600-800`).
- El decoder RX intenta autodetectar la velocidad de transmision y la muestra en `Decode WPM`.
- El texto decodificado desde audio de entrada se pinta en rojo para diferenciarlo del decode local por teclado.

### Modo Thetis DSP por puerto COM

Para usar keying CW con Thetis por puerto serie virtual:

1. En esta app:
- `PTT Method`: `THETIS_DSP`
- `CAT/DSP Port`: COM emparejado con el que usa Thetis (normalmente el otro extremo del par)
- `Thetis Key Line`: `DTR` (o `RTS` si asi lo configuras en Thetis)
- `Thetis PTT Line`: `None` (opcional `RTS`/`DTR` si quieres MOX/PTT separado)
- `TX Hang Time`: tiempo de cola de TX (aplica al PTT en modo Thetis DSP)

2. En Thetis (`Setup > DSP > CW`):
- `Connections > Secondary`: COM de Thetis (el opuesto al COM que abre esta app)
- `Connections > Key Line`: `DTR` (o `RTS`, debe coincidir con `Thetis Key Line`)
- `Connections > PTT Line`: `None` (o linea dedicada si la usas)

Notas:
- No abras el mismo COM en ambos programas; usa el par de puertos virtuales.
- Si hay polaridad invertida, activa `Invert Thetis Key` o `Invert Thetis PTT`.
