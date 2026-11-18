# Para el agente que prepare esto en otra PC

Este repo es un detector de códigos de canje que aparecen en un stream de Twitch. Leé `README.md` primero. Hablale a la persona en español y sin jerga.

## Qué está probado y qué no

- Probado en Linux contra el stream en vivo: detección de códigos de PlayStation (`XXXX-XXXX-XXXX`), copiar y avisar.
- Códigos de Xbox (5x5): leídos bien desde capturas. Su letra es más chica; si en vivo no los lee, subir `--escala` (por defecto 2).
- **Sin probar: todo lo de Windows.** `CAZAR.bat`, `--teclear`, `--ventanas` y `--listar-ventanas` se escribieron sin una PC con Windows a mano. Tomalos como borrador: probalos y arreglá lo que falle.

## Orden de trabajo

1. Instalar `uv` si falta y correr las dos pruebas con las imágenes de `muestras/` que figuran en el README. Tienen que dar los códigos indicados.
2. Correr `uv run cazar.py --sin-acciones` un minuto contra el vivo. Tiene que conectar y mostrar unas 10 o más lecturas por segundo. Si da menos, bajar `--fps` no ayuda; revisar uso de CPU.
3. Probar el pegado con `prueba_canje.html` y `--probar`, primero con `--teclear --enter` y después con `--ventanas "canje de prueba" --enter` abriendo la página en dos navegadores distintos. La página muestra qué recibió.
4. Recién entonces mirar las páginas de canje reales (ver abajo).
5. Dejar un comando final listo para correr y explicarle a la persona qué tiene que dejar abierto y enfocado.

## Las cuentas y los navegadores

La persona tiene varios navegadores con sesiones de PlayStation y Xbox de distintas regiones (cuentas de un grupo de amigos). Preguntale antes de tocar nada:

- qué navegadores y perfiles usar, y de qué región es cada cuenta;
- si quiere que el programa apriete Enter solo o que solo pegue.

Con eso, ayudala a dejar en cada navegador la página de canje abierta con el cursor en la casilla, y armá el `--ventanas` con un texto que aparezca en el título de esas ventanas (usá `--listar-ventanas`).

Reglas:

- No leas, copies ni muestres contraseñas, cookies ni tokens de sesión. No hace falta: el programa solo pega texto en ventanas.
- No cierres sesiones ni cambies datos de ninguna cuenta.
- No envíes códigos inventados a las páginas reales para "probar": cada intento fallido cuenta y la tienda puede bloquear el canje en esa cuenta. Para probar está `prueba_canje.html`.
- Un código sirve una vez y para una región. Avisale que pegar en muchas cuentas a la vez gasta un intento en cada una.

## Cosas que no sabemos de las páginas reales

- PlayStation: si la casilla acepta guiones. Por eso el programa pega los 12 caracteres sin guiones (`--guiones`). Si la página tiene tres casillas separadas en vez de una, hay que adaptar el pegado.
- PlayStation: después de pegar suele haber una segunda pantalla de confirmación. Fijate si un segundo Enter alcanza o hay que hacer clic.
- Xbox: `--abrir` usa `https://go.microsoft.com/fwlink/?LinkId=532540&mstoken=CODIGO`, que Microsoft documenta para códigos promocionales. Abre en el navegador por defecto; para otro navegador o perfil usá `--ejecutar` con `{url}`.

## Cómo está hecho

Un solo archivo, `cazar.py`:

- `EnVivo`: baja el stream con streamlink, lo decodifica con ffmpeg y guarda siempre el último cuadro.
- `leer_zona`: lee solo la zona donde sale el cartel (abajo a la derecha). Primero tal cual; si ve el cartel pero no un código confiable, agranda y relee.
- `barrido_completo`: cada segundo lee el cuadro entero buscando la palabra `CODIGO`; si el cartel cambia de lugar, mueve la zona.
- `extraer` / `_clasificar`: sacan el código del texto leído. El lector a veces lee `CODIG0` con cero: la etiqueta se busca con tolerancia.
- `Rastreador`: dispara cuando un código se lee igual dos veces seguidas (`--confirmar`).
- `Acciones`: copiar, pegar, abrir, avisar, anotar.
