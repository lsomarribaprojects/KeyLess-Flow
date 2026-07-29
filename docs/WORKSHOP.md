# KeyLess by Sinsajo — Guía rápida para el workshop

Dicta en cualquier app de tu PC (WhatsApp Web, correo, Word, el chat de una IA)
y transcribe también el audio que suena en tu computadora. Usás **tu propia
API key gratuita** — sin cuenta, sin pagos.

## 1. Instalar (2 minutos)

1. Descarga el instalador para Windows:
   **https://github.com/lsomarribaprojects/KeyLess-Flow/releases/latest/download/KeyLessFlow-Setup.exe**
2. Doble-clic → Siguiente → listo. La app queda en la bandeja del sistema
   (junto al reloj).
   - Si Windows muestra un aviso azul de SmartScreen: "Más información" →
     "Ejecutar de todas formas" (el instalador aún no está firmado).

## 2. Conseguir tu API key GRATIS (1 minuto)

1. Entra a **https://console.groq.com/keys** (crea cuenta con Google si no tienes).
2. Botón **Create API Key** → cópiala (empieza con `gsk_...`).
3. Al abrir KeyLess por primera vez, abajo del todo dice
   **"¿Workshop / tienes tu propia API key?"** → pégala → **Usar mi API key**.

La key queda guardada SOLO en tu máquina. Groq da un nivel gratuito generoso —
para dictado personal no vas a pagar nada.

## 3. Usar la app

| Atajo | Qué hace |
|---|---|
| **Ctrl + Alt** (mantener) | Dicta con el micrófono; al soltar, el texto se pega donde esté tu cursor |
| **Doble-tap Ctrl** | Dictado manos libres; un tap a Ctrl para terminar |
| **Ctrl + Shift** (mantener) | Transcribe lo que SUENA en tu PC (audio de WhatsApp, video, reunión) |
| **Triple-tap Ctrl** | Lo mismo, manos libres (reuniones largas); un tap para terminar |
| **Alt + 1..8** | Transforma el texto que tengas seleccionado (conciso, formal, traducir…) |

**Comandos de voz** (dilos mientras dictas): "nueva línea", "punto y aparte",
"coma", "dos puntos" — y "**dale enter**" al final para que envíe el mensaje solo.

**Hub** (clic en el ícono de la bandeja): historial de todo lo dictado,
diccionario personal (nombres que Whisper confunde), snippets, y el
**📝 Redactor** — escribís una idea en bruto y te la redacta lista en el
idioma/tono/largo que elijas, con Biblioteca para guardar tus textos.

La referencia completa está en la app: **Hub → Ajustes → "Ver comandos y atajos"**.

## ¿Problemas?

- **No pega el texto** → revisa que el cursor esté en un campo de texto.
- **"No se detectó audio"** con Ctrl+Shift → el audio debe salir por tu
  dispositivo de salida por defecto (el que suena de verdad).
- **La key no valida** → cópiala completa (empieza con `gsk_`), sin espacios.
