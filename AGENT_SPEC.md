# Especificación Técnica y Reglas de Arquitectura: Analizador FFT + Audio Router

## 1. Descripción del Sistema
Aplicación de escritorio en Python para Windows que funciona como puente de audio en tiempo real (Input -> DSP/FFT -> Output Passthrough). Recibe la señal desde un dispositivo de entrada (ej. cable virtual nombrado "Analizador" en Windows) y la envía hacia un dispositivo de salida físico (ej. "Altavoces Realtek"), mostrando un analizador de espectro FFT con curva suavizada por splines cúbicos, área sombreada semitransparente, efecto "tira elástica" anclada en 10 Hz, retención de picos/envolvente con decaimiento regulable, selector de tamaño de FFT con buffer circular (Ring Buffer) desacoplado de la latencia de audio, y dos vúmetros independientes (L y R).

## 2. Stack Tecnológico Obligatorio
- **Interfaz Gráfica:** `PyQt6` (Prohibido usar Tkinter o Matplotlib por problemas de rendimiento/FPS).
- **Renderizado 2D:** `pyqtgraph` (acelerado, 60 FPS con ViewBox de ejes bloqueados y fondo oscuro `#0B0926`).
- **Motor de Audio:** `sounddevice` (PortAudio).
- **Procesamiento Matemático:** `numpy` y `scipy.interpolate.make_interp_spline`.
- **Empaquetado:** `PyInstaller` (`--noconsole --onefile`).

## 3. Reglas Inquebrantables para Agentes y Modelos (NO VIOLAR)
1. **Aislamiento del Hilo de Audio:** El callback `audio_callback(indata, outdata, frames, time_info, status)` se ejecuta en un hilo C de alta prioridad. **PROHIBIDO** realizar cálculos de FFT, llamadas a `scipy`, asignación dinámica pesada o actualizaciones de la interfaz de `PyQt6` dentro del callback.
2. **Seguridad y Passthrough de Audio (MUTE y Cola No Bloqueante):**
   - La aplicación debe arrancar **MUTEADA por defecto (`self.is_muted = True`)** para evitar bucles de retroalimentación (feedback loop) accidentales entre micrófono y altavoces.
   - En el callback: si `self.is_muted` es `True`, se ejecuta `outdata.fill(0.0)`. Si no, `outdata[:] = indata`.
   - **Siempre** se envía una copia (`indata.copy()`) a la cola no bloqueante (`queue.Queue(maxsize=8)`) usando `put_nowait()`, permitiendo que el espectro FFT y los vúmetros sigan funcionando aunque la salida esté en mute.
3. **Latencia Mínima Desacoplada de Resolución FFT (Ring Buffer):**
   - El `blocksize` del stream de audio debe permanecer fijo y pequeño (`audio_block_size = 512`) para garantizar latencia ultra baja en el passthrough.
   - El cálculo de la FFT se realiza sobre un buffer circular (`self.ring_buffer`) de tamaño configurable (`512`, `1024`, `2048`, `4096`, `8192`, `16384` muestras) que acumula todos los bloques leídos de la cola en cada refresco visual (~16 ms). Esto otorga resolución espectral extrema en graves sin incrementar la latencia del monitoreo.
4. **Escala Logarítmica 1-2-5 y Margen Izquierdo (10 Hz):**
   - El eje X logarítmico debe comenzar en `log10(10.0) = 1.0` (10 Hz) para dejar el espaciado visual estándar de analizador de estudio a la izquierda de los 20 Hz, y llegar hasta `log10(nyquist)`.
   - Ticks principales siguiendo rigurosamente la serie **1-2-5**: `[20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]` hasta Nyquist, acompañados por ticks secundarios para las líneas de sub-década.
5. **Efecto "Tira Elástica" a 10 Hz:**
   - La curva se ancla en el piso dinámico (`y_floor`) en `10.0 Hz` con un punto elástico intermedio en `15.0 Hz`, conectando suavemente mediante `make_interp_spline` con los bins reales de la FFT ($\ge 25\text{ Hz}$).
6. **Bloqueo Total de Interacción con Mouse en el Gráfico:**
   - Desactivar completamente paneo y zoom por mouse y rueda con `self.plot_widget.setMouseEnabled(x=False, y=False)` y `vb.setMouseEnabled(x=False, y=False)`.
   - Ocultar botón "A" (`hideButtons()`) y menú contextual (`setMenuEnabled(False)`). Los ejes son fijos y solo se reajustan programáticamente mediante controles de la interfaz.
7. **Detector de Envolvente Asimétrico (Ataque Instantáneo + Caída Regulable):**
   - Subida rápida ante nuevos picos (`np.maximum(fft_mag, decayed)`) y caída suave controlada por `self.decay_factor`, ajustable desde el slider de la UI.
8. **Rango Dinámico dB y Relleno Semitransparente:**
   - Controles `QSpinBox` para `dB Mín` y `dB Máx` sincronizan `setYRange`, los ticks de la grilla y el piso de sombreado (`self.curve.setFillLevel(self.db_min)`).
   - Relleno bajo la curva con canal alfa semitransparente; color y opacidad definidos por el tema activo (ver regla 10).
9. **Gestión Segura de Streams:** Cualquier cambio en Dispositivo de Entrada, Dispositivo de Salida o Sample Rate debe detener (`stop()`) y cerrar (`close()`) el stream actual dentro de un bloque `try/except` antes de instanciar un nuevo `sd.Stream`.
10. **Sistema de Temas de Color (Panel "🎨 Colores"):**
   - Todos los colores del gráfico viven en `self.theme` (dict) y se aplican **exclusivamente** a través de `apply_theme()` en el hilo de UI. **PROHIBIDO** hardcodear colores del gráfico fuera de `THEME_PRESETS` / `apply_theme()`.
   - Claves del tema: `background`, `curve`, `curve_width` (décimas de px), `fill`, `fill_alpha` (0-255), `grid`, `grid_alpha` (0-100 %), `axis_text`.
   - En `pyqtgraph` las líneas de grilla heredan el color del pen del eje: el color de grilla se aplica con `axis.setPen()` y la intensidad con `showGrid(alpha=...)`; el texto de los ejes va por separado con `axis.setTextPen()`.
   - `ColorSettingsDialog` es **no modal** y edita el tema en vivo (previsualización inmediata; cancelar el selector de color revierte). Incluye presets (`THEME_PRESETS`) y "Restaurar por defecto".
11. **Persistencia en `config.json`:**
   - Ubicación: junto al `.exe` (o al script en desarrollo); fallback automático a `%APPDATA%\AnalizadorFFT\config.json` si la carpeta no es escribible.
   - Estructura: `{"version": 1, "theme": {...}}`. Al guardar se **preservan** las demás secciones existentes (preparado para sumar dispositivos/ajustes en el futuro).
   - Guardado con debounce de 600 ms tras el último cambio del tema + guardado forzado en `closeEvent`. Escritura atómica (`.tmp` + `os.replace`).
    - La carga valida cada clave (colores con `QColor.isValid()`, numéricos acotados a su rango). Un JSON corrupto o inválido **nunca** debe impedir el arranque: se cae al preset por defecto.
12. **Módulo Desacoplado Retro VFD (`retro_vfd.py`):**
   - El renderizado vectorial (QPainter), balística de envolvente y estética de pantalla VFD estilo procesador Pioneer DEQ-7600 se encuentran encapsulados exclusivamente en el archivo independiente `retro_vfd.py`.
   - Puede correr de forma 100% autónoma (`python retro_vfd.py`) con selector de dispositivos de audio (cola thread-safe) o generador de señal de prueba.
   - En `main.py`, la conmutación se realiza de forma limpia mediante un `QStackedWidget` accionado por el botón `📼 Modo Retro VFD` / `📈 Curva FFT`, sin interrumpir el stream de audio ni destruir los componentes visuales existentes.
   - Distribución de energía en 15 bandas ISO de 2/3 de octava (`[31.5, 50, 80, 125, 200, 315, 500, 800, 1.3k, 2k, 3.2k, 5k, 8k, 12.5k, 20k] Hz`).
   - **Patrón de Subdivisión en "V" Exacto del DEQ-7600 Real:** Cada una de las 20 filas por columna se subdivide en $N$ bloques horizontales: `SEGMENT_SUBDIVISIONS = [4, 4, 3, 3, 2, 2, 1, 1, 1, 2, 2, 3, 3, 4, 4]` (4 bloques para 31.5 y 50 Hz, 3 para 80 y 125 Hz, 2 para 200 y 315 Hz, 1 barra ancha continua para 500, 800 y 1.3 kHz, 2 para 2k y 3.2 kHz, 3 para 5k y 8 kHz, 4 para 12.5k y 20 kHz).
   - **Sin Peak Hold Flotante:** A requerimiento del usuario, no se retiene el tope superior; las columnas únicamente suben con ataque instantáneo y caen con decaimiento suave (`decay_speed = 0.75`).
13. **Modo Mini / Solo Espectro, Siempre al Frente y Pantalla Completa:**
   - **Modo Mini / Solo Espectro (M o Esc):** Oculta todos los paneles de configuración superior (`routing_group` y `visual_group`) y los vúmetros (`vu_group`), dejando exclusivamente el analizador de espectro activo en el centro con una barra compacta minimalista superior (`mini_toolbar`).
   - En **Modo Retro VFD**, el modo mini activa `set_solo_spectrum(True)` en `RetroVFDWidget`, expandiendo las 15 columnas del espectro y su cápsula de frecuencias a lo ancho y alto de la cavidad VFD sin elementos auxiliares (sin iconos SFC, sin butacas ni presets).
   - En **Modo Normal FFT**, el gráfico pyqtgraph de la curva FFT ocupa el 100% del área de la ventana.
   - **Siempre al Frente (`WindowStaysOnTopHint` / P o Ctrl+T):** Permite fijar la ventana como un widget flotante superpuesto sobre cualquier juego, DAW o aplicación en Windows sin perder foco ni posición.
   - **Pantalla Completa (F11):** Alterna modo fullscreen completo sobreponiéndose a todo el escritorio.
   - **Interacción Ágil:** Doble clic en el área del espectro conmuta el Modo Mini. Clic derecho despliega un menú contextual con acceso directo a todos los modos, mute y colores.

## 4. Backlog de Mejoras para Próximos Prompts
- [x] Bloqueo total de mouse/rueda en el gráfico FFT.
- [x] Botón de Mute con inicio protegido por defecto.
- [x] Efecto "Tira Elástica" hasta 10 Hz (anclaje continuo en el piso).
- [x] Retención / Caída espectral asimétrica regulable mediante slider.
- [x] Control manual del rango dinámico dB (Mín / Máx).
- [x] Sombreado semitransparente bajo la curva FFT.
- [x] Ring Buffer (buffer circular) desacoplado del blocksize de audio para alta resolución espectral (hasta 16384 samples) con latencia mínima de 512 samples.
- [x] Escala logarítmica exacta 1-2-5 con inicio en 10 Hz y sub-ticks.
- [x] Panel de configuración de colores (curva, relleno, fondo, grilla, texto de ejes) con presets y previsualización en vivo.
- [x] Persistencia del tema de colores en `config.json`.
- [x] Modo Retro VFD estilo DSP Pioneer DEQ-7600 en módulo independiente (`retro_vfd.py`) con patrón de subdivisión en "V" exacto (4-4-3-3-2-2-1-1-1-2-2-3-3-4-4), sin peak hold, y estética de fósforo fluorescente acelerada por sprites.
- [x] Modo Mini / Solo Espectro (para Curva FFT y Retro VFD), Fijar al Frente (Always on Top) y Pantalla Completa (F11) con atajos y menú contextual.
- [ ] Agregar suavizado por fracciones de octava (1/3, 1/6, 1/12 octava) antes del spline cúbico para evitar rizado excesivo en altas frecuencias.
- [ ] Añadir retícula interactiva (Crosshair) que indique frecuencia exacta (Hz) y nivel (dB) bajo el cursor del mouse.
- [ ] Añadir retención de picos (Peak Hold) independiente en los vúmetros L+R (barritas indicadoras de pico temporal).
- [ ] Extender `config.json` con dispositivos elegidos, sample rate, fft size, escalas, retención y dB min/max.
