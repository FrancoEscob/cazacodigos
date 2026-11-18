# Cazacódigos

Mira un stream de Twitch y, apenas aparece un código de canje en pantalla, lo copia al portapapeles, suena una alarma y, si se lo pedís, lo pega solo en el navegador o abre la página de canje.

Reconoce dos formas:

| Plataforma | Forma |
| --- | --- |
| Xbox / Microsoft | `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX` |
| PlayStation | `XXXX-XXXX-XXXX` |

Está pensado para los carteles que dicen `CODIGO: ...` en el stream de Rubius, pero sirve para cualquier canal que muestre códigos con esa forma.

## Qué tan rápido es

Medido en vivo: lee 15 cuadros por segundo y dispara unos 100 a 300 ms después de que el código aparece en el video. El cartel queda en pantalla unos 10 segundos, así que la carrera se gana en el canje, no en la lectura.

## Instalar y correr

Hace falta [uv](https://docs.astral.sh/uv/). Lo demás (Python, lector de texto, ffmpeg) se baja solo la primera vez.

**Windows:** doble clic en `CAZAR.bat` (instala uv si falta).

**Linux / Mac:**

```
uv run cazar.py
```

Cuando dice `Vigilando…` ya está mirando. Cada 15 segundos muestra cuántas lecturas por segundo hace.

## Qué hace al detectar

Siempre copia el código y avisa con sonido. Lo demás se elige con opciones:

| Opción | Qué hace |
| --- | --- |
| `--teclear --enter` | Pega el código en la ventana enfocada y aprieta Enter |
| `--ventanas "texto1,texto2" --enter` | Windows: pega en **todas** las ventanas cuyo título contenga alguno de esos textos. Para tener un navegador por cuenta |
| `--abrir` | Xbox: abre el canje de Microsoft con el código ya cargado |
| `--ejecutar "comando {codigo}"` | Lanza un comando propio. Admite `{codigo}`, `{tipo}` y `{url}` |
| `--solo play` / `--solo xbox` | Ignora los códigos del otro tipo |
| `--confirmar 1` | Dispara a la primera lectura en vez de esperar dos iguales (más rápido, más riesgo de leer mal) |
| `--guiones si` / `no` | Fuerza pegar con o sin guiones. Por defecto: Xbox con, PlayStation sin |
| `--canal otro` | Otro canal de Twitch |

## Probar sin esperar al stream

```
uv run cazar.py --archivo muestras/captura3_play.png     # tiene que leer F27H-7E2L-HP9Q
uv run cazar.py --archivo muestras/captura2.png          # tiene que leer MVXGQ-FPPJY-RKVJ2-DCPR7-RHFHZ
```

Para probar el pegado: abrí `prueba_canje.html` en el navegador, dejá el cursor en la casilla y corré

```
uv run cazar.py --probar ABCD-EFGH-2345 --teclear --enter
```

A los 4 segundos dispara. La página muestra qué recibió y a qué hora.

En Windows, `uv run cazar.py --listar-ventanas` muestra los títulos de las ventanas abiertas, para elegir qué poner en `--ventanas`.

## Antes de que salga un código

- **PlayStation:** entrá a PlayStation Store con la cuenta, abrí "Canjear código" y dejá el cursor en la casilla. No hay forma de mandar el código por link, hay que pegarlo.
- **Xbox:** dejá la sesión de Microsoft iniciada en el navegador. Con `--abrir` se abre la página con el código cargado y queda apretar Siguiente / Confirmar.

## Cosas a saber

- Un código sirve una sola vez y para una región. Pegarlo en varias cuentas a la vez solo ayuda si son de regiones distintas.
- Varios intentos fallidos seguidos pueden hacer que la tienda bloquee el canje en esa cuenta por un rato.
- Canjear con un programa puede ir contra las condiciones de uso de la tienda. Cada uno decide con sus cuentas.
- Cada código detectado queda en `codigos.log` y su imagen en `capturas/`.

## Estado de las pruebas

- Probado en Linux contra el stream en vivo: detectó códigos de PlayStation reales.
- Códigos de Xbox: probado solo con capturas, todavía no con un cuadro del vivo.
- Windows: sin probar (`CAZAR.bat`, `--teclear`, `--ventanas`).
