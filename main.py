import sys
import os
import json
import queue
import numpy as np
import sounddevice as sd
from scipy.interpolate import make_interp_spline
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLabel, QProgressBar, QGroupBox, QMessageBox,
    QPushButton, QSlider, QSpinBox, QDialog, QColorDialog,
    QGridLayout, QDialogButtonBox, QStackedWidget, QMenu
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QCursor
import pyqtgraph as pg


# --- Temas de color del gráfico ---
# curve_width en décimas de píxel (22 = 2.2 px), fill_alpha 0-255, grid_alpha 0-100 (%)
DEFAULT_PRESET_NAME = "Estudio Cian (Default)"
THEME_PRESETS = {
    "Estudio Cian (Default)": {
        "background": "#0B0926", "curve": "#00E5FF", "curve_width": 22,
        "fill": "#00E5FF", "fill_alpha": 65,
        "grid": "#969696", "grid_alpha": 35, "axis_text": "#969696",
    },
    "Ámbar Analógico": {
        "background": "#140C02", "curve": "#FFB300", "curve_width": 22,
        "fill": "#FF8F00", "fill_alpha": 60,
        "grid": "#8A6A3A", "grid_alpha": 40, "axis_text": "#D9B98A",
    },
    "Fósforo Verde": {
        "background": "#020F06", "curve": "#39FF14", "curve_width": 20,
        "fill": "#00C853", "fill_alpha": 55,
        "grid": "#2E7D4F", "grid_alpha": 45, "axis_text": "#7FD89A",
    },
    "Magenta Neón": {
        "background": "#12001A", "curve": "#FF2BD6", "curve_width": 24,
        "fill": "#B000FF", "fill_alpha": 60,
        "grid": "#7A4A8F", "grid_alpha": 40, "axis_text": "#D8A8E8",
    },
    "Grafito Rojo": {
        "background": "#0F0F10", "curve": "#FF3D3D", "curve_width": 22,
        "fill": "#FF3D3D", "fill_alpha": 50,
        "grid": "#5A5A60", "grid_alpha": 45, "axis_text": "#B0B0B8",
    },
    "Claro (Impresión)": {
        "background": "#F5F6F8", "curve": "#0055CC", "curve_width": 22,
        "fill": "#0055CC", "fill_alpha": 45,
        "grid": "#6B7280", "grid_alpha": 40, "axis_text": "#303540",
    },
}


# Rangos válidos de las claves numéricas del tema (para validar config.json)
THEME_NUMERIC_RANGES = {"curve_width": (10, 60), "fill_alpha": (0, 255), "grid_alpha": (5, 100)}
THEME_COLOR_KEYS = ("background", "curve", "fill", "grid", "axis_text")
CONFIG_FILENAME = "config.json"


def _app_dir():
    """Carpeta del .exe (PyInstaller --onefile) o del script en modo desarrollo."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _config_candidates():
    """Primero junto al ejecutable; fallback a %APPDATA%\\AnalizadorFFT si esa carpeta no es escribible."""
    paths = [os.path.join(_app_dir(), CONFIG_FILENAME)]
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, "AnalizadorFFT", CONFIG_FILENAME))
    return paths


def load_theme_from_config():
    """Devuelve el último tema guardado, validado clave por clave. Ante cualquier problema usa el default."""
    theme = dict(THEME_PRESETS[DEFAULT_PRESET_NAME])
    for path in _config_candidates():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f).get("theme", {})
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(saved, dict):
            continue
        for key in THEME_COLOR_KEYS:
            value = saved.get(key)
            if isinstance(value, str) and QColor(value).isValid():
                theme[key] = QColor(value).name()
        for key, (vmin, vmax) in THEME_NUMERIC_RANGES.items():
            value = saved.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                theme[key] = int(min(max(value, vmin), vmax))
        break
    return theme


def save_theme_to_config(theme):
    """Guarda el tema preservando cualquier otra sección existente de config.json. Nunca lanza excepciones."""
    for path in _config_candidates():
        try:
            data = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
                except (OSError, ValueError):
                    pass
            data["version"] = 1
            data["theme"] = {k: theme[k] for k in (*THEME_COLOR_KEYS, *THEME_NUMERIC_RANGES)}
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)  # escritura atómica: no deja un JSON a medias
            return True
        except OSError:
            continue
    return False


class ColorSettingsDialog(QDialog):
    """Panel no modal de colores. Modifica el dict de tema compartido y llama a on_change() en vivo."""

    COLOR_ROWS = [
        ("background", "Fondo del gráfico"),
        ("curve", "Línea de la curva"),
        ("fill", "Relleno bajo la curva"),
        ("grid", "Líneas de grilla / frecuencia"),
        ("axis_text", "Texto de los ejes"),
    ]

    def __init__(self, theme, on_change, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de Colores")
        self.setModal(False)
        self.theme = theme
        self.on_change = on_change
        self.swatches = {}
        self.sliders = {}

        layout = QVBoxLayout(self)

        # Presets
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("— Personalizado —")
        self.preset_combo.addItems(list(THEME_PRESETS.keys()))
        self.preset_combo.activated.connect(self._on_preset_selected)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        # Colores
        colors_group = QGroupBox("Colores")
        colors_grid = QGridLayout(colors_group)
        for row, (key, label) in enumerate(self.COLOR_ROWS):
            colors_grid.addWidget(QLabel(label), row, 0)
            btn = QPushButton()
            btn.setFixedSize(110, 26)
            btn.clicked.connect(lambda _=False, k=key, l=label: self._pick_color(k, l))
            colors_grid.addWidget(btn, row, 1)
            self.swatches[key] = btn
        layout.addWidget(colors_group)

        # Grosor / opacidades
        style_group = QGroupBox("Grosor e Intensidad")
        style_grid = QGridLayout(style_group)
        self._add_slider(style_grid, 0, "Grosor de la curva", "curve_width", 10, 60,
                         lambda v: f"{v / 10:.1f} px")
        self._add_slider(style_grid, 1, "Opacidad del relleno", "fill_alpha", 0, 255,
                         lambda v: f"{round(v * 100 / 255)}%")
        self._add_slider(style_grid, 2, "Intensidad de la grilla", "grid_alpha", 5, 100,
                         lambda v: f"{v}%")
        layout.addWidget(style_group)

        # Botones
        buttons = QDialogButtonBox()
        reset_btn = buttons.addButton("Restaurar por defecto", QDialogButtonBox.ButtonRole.ResetRole)
        close_btn = buttons.addButton("Cerrar", QDialogButtonBox.ButtonRole.AcceptRole)
        reset_btn.clicked.connect(lambda: self._apply_preset(DEFAULT_PRESET_NAME))
        close_btn.clicked.connect(self.close)
        layout.addWidget(buttons)

        self._refresh_widgets()
        self._sync_preset_combo()

    def _add_slider(self, grid, row, label, key, vmin, vmax, fmt):
        grid.addWidget(QLabel(label), row, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(vmin, vmax)
        slider.setMinimumWidth(180)
        val_label = QLabel()
        val_label.setFixedWidth(50)
        slider.valueChanged.connect(lambda v, k=key: self._on_slider_changed(k, v))
        grid.addWidget(slider, row, 1)
        grid.addWidget(val_label, row, 2)
        self.sliders[key] = (slider, val_label, fmt)

    def _on_slider_changed(self, key, value):
        self.theme[key] = value
        slider, val_label, fmt = self.sliders[key]
        val_label.setText(fmt(value))
        self._sync_preset_combo()
        self.on_change()

    def _pick_color(self, key, label):
        original = self.theme[key]
        dlg = QColorDialog(QColor(original), self)
        dlg.setWindowTitle(f"Elegir color: {label}")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        # Previsualización en vivo mientras se elige
        dlg.currentColorChanged.connect(lambda c, k=key: self._set_color(k, c.name()))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_color(key, dlg.selectedColor().name())
        else:
            self._set_color(key, original)

    def _set_color(self, key, hex_color):
        self.theme[key] = hex_color
        self._style_swatch(key)
        self._sync_preset_combo()
        self.on_change()

    def _style_swatch(self, key):
        color = QColor(self.theme[key])
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        text_color = "#000000" if luminance > 140 else "#FFFFFF"
        btn = self.swatches[key]
        btn.setText(color.name().upper())
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {color.name()}; color: {text_color};"
            f" border: 1px solid #555; border-radius: 3px; font-family: Consolas, monospace; }}"
        )

    def _refresh_widgets(self):
        for key in self.swatches:
            self._style_swatch(key)
        for key, (slider, val_label, fmt) in self.sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(self.theme[key]))
            slider.blockSignals(False)
            val_label.setText(fmt(int(self.theme[key])))

    def _sync_preset_combo(self):
        # Muestra el preset activo si el tema coincide exactamente; si no, "Personalizado"
        for name, preset in THEME_PRESETS.items():
            if all(str(self.theme.get(k)).lower() == str(v).lower() for k, v in preset.items()):
                self.preset_combo.setCurrentText(name)
                return
        self.preset_combo.setCurrentIndex(0)

    def _on_preset_selected(self, index):
        if index <= 0:
            return
        self._apply_preset(self.preset_combo.itemText(index))

    def _apply_preset(self, name):
        self.theme.update(THEME_PRESETS[name])
        self._refresh_widgets()
        self._sync_preset_combo()
        self.on_change()


class AudioAnalyzerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Analizador de Espectro FFT & Audio Router")
        self.resize(1320, 750)

        # Parámetros de Audio y DSP
        self.sample_rates = [44100, 48000, 88200, 96000]
        self.current_sr = 48000
        self.audio_block_size = 512  # Fijo y pequeño para baja latencia en el monitoreo
        self.fft_sizes = [512, 1024, 2048, 4096, 8192, 16384]
        self.fft_size = 4096
        self.channels = 2

        self.audio_queue = queue.Queue(maxsize=8)
        self.ring_buffer = np.zeros(self.fft_size, dtype=np.float32)
        self.stream = None

        # Inicia MUTEADO por defecto para evitar feedback loops
        self.is_muted = True

        # Retención espectral
        self.held_fft = None
        self.decay_factor = 0.88

        # Rango dinámico del eje Y en dB
        self.db_min = -90
        self.db_max = 0

        # Tema de colores del gráfico (editable en vivo desde el panel "🎨 Colores")
        # Se restaura el último tema usado desde config.json (si existe y es válido)
        self.theme = load_theme_from_config()
        self.color_dialog = None

        # Guardado diferido del tema: evita escribir a disco en cada movimiento de slider
        self.theme_save_timer = QTimer(self)
        self.theme_save_timer.setSingleShot(True)
        self.theme_save_timer.setInterval(600)
        self.theme_save_timer.timeout.connect(self.save_theme)

        pg.setConfigOptions(antialias=True)

        # Estados de ventana: Modo Mini / Solo Espectro, Fijar al Frente, Pantalla Completa
        self.is_mini_mode = False
        self.is_always_on_top = False
        self.saved_normal_geometry = None

        self._init_ui()
        self._populate_devices()
        self.restart_audio_stream()

        # Timer de refresco visual (~60 FPS)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_visuals)
        self.timer.start(16)

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(8, 6, 8, 8)
        main_layout.setSpacing(6)

        # Barra superior compacta (visible únicamente en Modo Mini)
        self.mini_toolbar = QWidget()
        mini_layout = QHBoxLayout(self.mini_toolbar)
        mini_layout.setContentsMargins(6, 3, 6, 3)
        mini_layout.setSpacing(6)
        self.mini_toolbar.setStyleSheet("""
            QWidget {
                background-color: #121518;
                border-bottom: 1px solid #283038;
            }
            QPushButton {
                background-color: #242A30;
                color: #DDD;
                border: 1px solid #38424D;
                border-radius: 3px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #323A44;
                color: #FFF;
            }
        """)

        self.mini_restore_btn = QPushButton("🗗 Restaurar Controles (M)")
        self.mini_restore_btn.setToolTip("Volver a mostrar la consola completa con todos los paneles (Atajo: M o Esc)")
        self.mini_restore_btn.clicked.connect(self.toggle_mini_mode)
        mini_layout.addWidget(self.mini_restore_btn)

        self.mini_retro_btn = QPushButton("📼 Modo Retro VFD")
        self.mini_retro_btn.setCheckable(True)
        self.mini_retro_btn.setToolTip("Alternar entre la curva FFT y las columnas retro VFD")
        self.mini_retro_btn.clicked.connect(self.toggle_retro_mode)
        mini_layout.addWidget(self.mini_retro_btn)

        self.mini_on_top_btn = QPushButton("📌 Fijar al Frente")
        self.mini_on_top_btn.setCheckable(True)
        self.mini_on_top_btn.setToolTip("Mantener la ventana siempre visible sobre otras aplicaciones (Atajo: P)")
        self.mini_on_top_btn.clicked.connect(self.toggle_always_on_top)
        mini_layout.addWidget(self.mini_on_top_btn)

        self.mini_fs_btn = QPushButton("⛶ Pantalla Completa")
        self.mini_fs_btn.setToolTip("Alternar pantalla completa (Atajo: F11)")
        self.mini_fs_btn.clicked.connect(self.toggle_fullscreen)
        mini_layout.addWidget(self.mini_fs_btn)

        self.mini_mute_btn = QPushButton()
        self.mini_mute_btn.setCheckable(True)
        self.mini_mute_btn.setChecked(True)
        self.mini_mute_btn.setToolTip("Silenciar o activar salida de audio")
        self.mini_mute_btn.clicked.connect(lambda: self.toggle_mute(not self.is_muted))
        mini_layout.addWidget(self.mini_mute_btn)

        mini_layout.addStretch()

        self.mini_hint_label = QLabel("<span style='color:#6F7D8C; font-size:10px;'>Doble clic: Modo Mini | Clic derecho: Menú | F11: Pantalla completa</span>")
        mini_layout.addWidget(self.mini_hint_label)

        self.mini_toolbar.setVisible(False)
        main_layout.addWidget(self.mini_toolbar)

        # --- FILA 1: Dispositivos, Mute, Sample Rate y FFT Size ---
        self.routing_group = QGroupBox("Enrutamiento de Audio y Motor FFT")
        routing_layout = QHBoxLayout(self.routing_group)

        self.mute_btn = QPushButton()
        self.mute_btn.setCheckable(True)
        self.mute_btn.setChecked(True)
        self.mute_btn.setMinimumWidth(280)
        self.mute_btn.clicked.connect(self.toggle_mute)
        self._update_mute_button_style()
        routing_layout.addWidget(self.mute_btn)

        self.in_combo = QComboBox()
        self.in_combo.currentIndexChanged.connect(self.restart_audio_stream)
        routing_layout.addWidget(QLabel("Entrada:"))
        routing_layout.addWidget(self.in_combo, 1)

        self.out_combo = QComboBox()
        self.out_combo.currentIndexChanged.connect(self.restart_audio_stream)
        routing_layout.addWidget(QLabel("Salida:"))
        routing_layout.addWidget(self.out_combo, 1)

        self.sr_combo = QComboBox()
        for sr in self.sample_rates:
            self.sr_combo.addItem(f"{sr} Hz", sr)
        self.sr_combo.setCurrentText("48000 Hz")
        self.sr_combo.currentIndexChanged.connect(self.on_sr_changed)
        routing_layout.addWidget(QLabel("Sample Rate:"))
        routing_layout.addWidget(self.sr_combo)

        self.fft_combo = QComboBox()
        for sz in self.fft_sizes:
            self.fft_combo.addItem(f"{sz} samples", sz)
        self.fft_combo.setCurrentText("4096 samples")
        self.fft_combo.currentIndexChanged.connect(self.on_fft_size_changed)
        routing_layout.addWidget(QLabel("FFT Samples:"))
        routing_layout.addWidget(self.fft_combo)

        main_layout.addWidget(self.routing_group)

        # --- FILA 2: Escalas, Rango dB y Retención ---
        self.visual_group = QGroupBox("Controles de Espectro, Escalas y Retención")
        visual_layout = QHBoxLayout(self.visual_group)

        self.x_scale_combo = QComboBox()
        self.x_scale_combo.addItems(["Logarítmica", "Lineal"])
        self.x_scale_combo.currentIndexChanged.connect(self.update_axes_config)
        visual_layout.addWidget(QLabel("Eje X:"))
        visual_layout.addWidget(self.x_scale_combo)

        self.y_scale_combo = QComboBox()
        self.y_scale_combo.addItems(["Logarítmica (dB)", "Lineal (Amplitud)"])
        self.y_scale_combo.currentIndexChanged.connect(self.on_y_scale_changed)
        visual_layout.addWidget(QLabel("Eje Y:"))
        visual_layout.addWidget(self.y_scale_combo)

        visual_layout.addWidget(QLabel("dB Mín:"))
        self.db_min_spin = QSpinBox()
        self.db_min_spin.setRange(-140, -10)
        self.db_min_spin.setSingleStep(5)
        self.db_min_spin.setValue(self.db_min)
        self.db_min_spin.setSuffix(" dB")
        self.db_min_spin.valueChanged.connect(self.on_db_range_changed)
        visual_layout.addWidget(self.db_min_spin)

        visual_layout.addWidget(QLabel("dB Máx:"))
        self.db_max_spin = QSpinBox()
        self.db_max_spin.setRange(-60, 20)
        self.db_max_spin.setSingleStep(5)
        self.db_max_spin.setValue(self.db_max)
        self.db_max_spin.setSuffix(" dB")
        self.db_max_spin.valueChanged.connect(self.on_db_range_changed)
        visual_layout.addWidget(self.db_max_spin)

        visual_layout.addWidget(QLabel("Retención al subir:"))
        self.hold_slider = QSlider(Qt.Orientation.Horizontal)
        self.hold_slider.setRange(0, 99)
        self.hold_slider.setValue(85)
        self.hold_slider.setFixedWidth(160)
        self.hold_slider.valueChanged.connect(self.on_hold_changed)
        visual_layout.addWidget(self.hold_slider)

        self.hold_val_label = QLabel("85%")
        self.hold_val_label.setFixedWidth(40)
        visual_layout.addWidget(self.hold_val_label)

        visual_layout.addStretch()

        # Botón del panel de colores (curva, relleno, fondo, grilla, texto)
        self.colors_btn = QPushButton("🎨 Colores")
        self.colors_btn.setToolTip("Configurar colores de la curva, el fondo y las líneas de frecuencia")
        self.colors_btn.clicked.connect(self.open_color_settings)
        visual_layout.addWidget(self.colors_btn)

        # Botón de alternancia Modo Retro (Pioneer DEQ-7600 VFD)
        self.retro_btn = QPushButton("📼 Modo Retro VFD")
        self.retro_btn.setCheckable(True)
        self.retro_btn.setToolTip("Alternar entre la curva FFT y la pantalla de columnas retro estilo Pioneer DEQ-7600")
        self.retro_btn.clicked.connect(self.toggle_retro_mode)
        visual_layout.addWidget(self.retro_btn)

        # Botón Modo Mini (Solo Analizador de Espectro)
        self.mini_mode_btn = QPushButton("🗖 Modo Mini")
        self.mini_mode_btn.setToolTip("Minimizar la interfaz y mostrar exclusivamente el analizador de espectro (Atajo: M o Esc)")
        self.mini_mode_btn.clicked.connect(self.toggle_mini_mode)
        visual_layout.addWidget(self.mini_mode_btn)

        # Botón Fijar al Frente (Always on Top)
        self.on_top_btn = QPushButton("📌 Fijar al Frente")
        self.on_top_btn.setCheckable(True)
        self.on_top_btn.setToolTip("Mantener la ventana siempre visible sobre cualquier otra aplicación (Atajo: P)")
        self.on_top_btn.clicked.connect(self.toggle_always_on_top)
        visual_layout.addWidget(self.on_top_btn)

        # Botón Pantalla Completa
        self.fullscreen_btn = QPushButton("⛶ Pantalla Completa")
        self.fullscreen_btn.setToolTip("Expandir a pantalla completa sobreponiéndose a todo el escritorio (Atajo: F11)")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        visual_layout.addWidget(self.fullscreen_btn)

        main_layout.addWidget(self.visual_group)

        # --- ÁREA CENTRAL: Stack (Gráfico FFT / Pantalla VFD) + Vúmetros L/R ---
        center_layout = QHBoxLayout()

        self.display_stack = QStackedWidget()

        self.plot_widget = pg.PlotWidget()
        # Bloqueo total de mouse y rueda
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.hideButtons()
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.getPlotItem().vb.setMouseEnabled(x=False, y=False)

        # Curva con área sombreada semitransparente (colores reales aplicados en apply_theme)
        self.curve = self.plot_widget.plot(fillLevel=self.db_min)
        self.display_stack.addWidget(self.plot_widget)

        # Widget VFD Pioneer DEQ-7600 (aislado en retro_vfd.py)
        try:
            from retro_vfd import RetroVFDWidget
            self.vfd_widget = RetroVFDWidget()
            self.display_stack.addWidget(self.vfd_widget)
        except Exception as e:
            self.vfd_widget = None

        center_layout.addWidget(self.display_stack, 1)

        # Panel de Vúmetros (L + R)
        self.vu_group = QGroupBox("Nivel L+R (dBFS)")
        vu_layout = QHBoxLayout(self.vu_group)

        self.vu_left = self._create_vumeter("L")
        self.vu_right = self._create_vumeter("R")
        vu_layout.addLayout(self.vu_left["layout"])
        vu_layout.addLayout(self.vu_right["layout"])

        center_layout.addWidget(self.vu_group)
        main_layout.addLayout(center_layout, 1)

        # Filtros de eventos para interacción fluida: doble clic para Modo Mini y menú contextual
        self.display_stack.installEventFilter(self)
        self.plot_widget.installEventFilter(self)
        if self.vfd_widget is not None:
            self.vfd_widget.installEventFilter(self)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.display_stack.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.display_stack.customContextMenuRequested.connect(self.show_context_menu)

        self.on_hold_changed()
        self.update_axes_config()
        self.apply_theme()

    def open_color_settings(self):
        if self.color_dialog is None:
            self.color_dialog = ColorSettingsDialog(self.theme, self.on_theme_changed, self)
        self.color_dialog.show()
        self.color_dialog.raise_()
        self.color_dialog.activateWindow()

    def set_retro_mode(self, is_retro: bool):
        if self.vfd_widget is None:
            return
        self.retro_btn.setChecked(is_retro)
        if hasattr(self, 'mini_retro_btn'):
            self.mini_retro_btn.setChecked(is_retro)

        if is_retro:
            btn_text = "📈 Curva FFT"
            style = """
                QPushButton {
                    background-color: #00838F;
                    color: white;
                    font-weight: bold;
                    padding: 4px 10px;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #00ACC1; }
            """
            self.retro_btn.setText(btn_text)
            self.retro_btn.setStyleSheet(style)
            if hasattr(self, 'mini_retro_btn'):
                self.mini_retro_btn.setText(btn_text)
                self.mini_retro_btn.setStyleSheet("background-color: #00838F; color: white; font-weight: bold; padding: 4px 10px;")
            self.display_stack.setCurrentIndex(1)
            if self.is_mini_mode:
                self.vfd_widget.set_solo_spectrum(True)
        else:
            btn_text = "📼 Modo Retro VFD"
            self.retro_btn.setText(btn_text)
            self.retro_btn.setStyleSheet("")
            if hasattr(self, 'mini_retro_btn'):
                self.mini_retro_btn.setText(btn_text)
                self.mini_retro_btn.setStyleSheet("")
            self.display_stack.setCurrentIndex(0)

    def toggle_retro_mode(self):
        new_state = (self.display_stack.currentIndex() != 1)
        self.set_retro_mode(new_state)

    def toggle_mini_mode(self):
        self.is_mini_mode = not self.is_mini_mode
        if self.is_mini_mode:
            if not self.isFullScreen():
                self.saved_normal_geometry = self.saveGeometry()

            self.routing_group.setVisible(False)
            self.visual_group.setVisible(False)
            self.vu_group.setVisible(False)
            self.mini_toolbar.setVisible(True)

            if self.vfd_widget is not None and self.display_stack.currentIndex() == 1:
                self.vfd_widget.set_solo_spectrum(True)

            if not self.isFullScreen():
                self.resize(880, 320)
        else:
            self.mini_toolbar.setVisible(False)
            self.routing_group.setVisible(True)
            self.visual_group.setVisible(True)
            self.vu_group.setVisible(True)

            if self.vfd_widget is not None:
                self.vfd_widget.set_solo_spectrum(False)

            if self.saved_normal_geometry is not None and not self.isFullScreen():
                self.restoreGeometry(self.saved_normal_geometry)

    def toggle_always_on_top(self):
        self.is_always_on_top = not self.is_always_on_top
        pos = self.pos()
        size = self.size()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.is_always_on_top)
        self.show()
        self.move(pos)
        self.resize(size)
        self._update_on_top_button_style()

    def _update_on_top_button_style(self):
        if self.is_always_on_top:
            style = """
                QPushButton {
                    background-color: #F57F17;
                    color: black;
                    font-weight: bold;
                    padding: 4px 10px;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #FBC02D; }
            """
            txt = "📌 Fijado al Frente"
        else:
            style = ""
            txt = "📌 Fijar al Frente"

        self.on_top_btn.setChecked(self.is_always_on_top)
        self.on_top_btn.setText(txt)
        self.on_top_btn.setStyleSheet(style)

        if hasattr(self, 'mini_on_top_btn'):
            self.mini_on_top_btn.setChecked(self.is_always_on_top)
            self.mini_on_top_btn.setText(txt)
            self.mini_on_top_btn.setStyleSheet(style)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_btn.setText("⛶ Pantalla Completa")
            if hasattr(self, 'mini_fs_btn'):
                self.mini_fs_btn.setText("⛶ Pantalla Completa")
        else:
            self.showFullScreen()
            self.fullscreen_btn.setText("🗗 Salir Pantalla Completa")
            if hasattr(self, 'mini_fs_btn'):
                self.mini_fs_btn.setText("🗗 Ventana")

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1A1D22;
                color: #EEE;
                border: 1px solid #38424D;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #00838F;
                color: #FFF;
            }
        """)

        act_mini = menu.addAction("🗖 Modo Mini / Solo Espectro (M)")
        act_mini.setCheckable(True)
        act_mini.setChecked(self.is_mini_mode)
        act_mini.triggered.connect(self.toggle_mini_mode)

        act_top = menu.addAction("📌 Fijar al Frente / Always on Top (P)")
        act_top.setCheckable(True)
        act_top.setChecked(self.is_always_on_top)
        act_top.triggered.connect(self.toggle_always_on_top)

        act_fs = menu.addAction("⛶ Pantalla Completa (F11)")
        act_fs.setCheckable(True)
        act_fs.setChecked(self.isFullScreen())
        act_fs.triggered.connect(self.toggle_fullscreen)

        menu.addSeparator()

        is_retro = (self.display_stack.currentIndex() == 1)
        mode_text = "📈 Cambiar a Curva FFT" if is_retro else "📼 Cambiar a Modo Retro VFD"
        act_mode = menu.addAction(mode_text)
        act_mode.triggered.connect(self.toggle_retro_mode)

        act_mute = menu.addAction("🔇 Salida Muteada")
        act_mute.setCheckable(True)
        act_mute.setChecked(self.is_muted)
        act_mute.triggered.connect(lambda: self.toggle_mute(not self.is_muted))

        menu.addSeparator()
        act_colors = menu.addAction("🎨 Configurar Colores...")
        act_colors.triggered.connect(self.open_color_settings)

        menu.exec(QCursor.pos())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self.toggle_fullscreen()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                self.fullscreen_btn.setText("⛶ Pantalla Completa")
                if hasattr(self, 'mini_fs_btn'):
                    self.mini_fs_btn.setText("⛶ Pantalla Completa")
            elif self.is_mini_mode:
                self.toggle_mini_mode()
            event.accept()
        elif event.key() == Qt.Key.Key_M:
            self.toggle_mini_mode()
            event.accept()
        elif event.key() == Qt.Key.Key_P:
            self.toggle_always_on_top()
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj in (self.display_stack, self.plot_widget, getattr(self, 'vfd_widget', None)):
            if event.type() == event.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.toggle_mini_mode()
                    return True
        return super().eventFilter(obj, event)

    def on_theme_changed(self):
        self.apply_theme()
        self.theme_save_timer.start()  # reinicia el debounce; guarda 600 ms después del último cambio

    def save_theme(self):
        save_theme_to_config(self.theme)

    def apply_theme(self):
        """Aplica self.theme al gráfico. Se ejecuta en el hilo de UI (nunca desde el callback de audio)."""
        t = self.theme
        self.plot_widget.setBackground(t["background"])

        self.curve.setPen(pg.mkPen(color=t["curve"], width=t["curve_width"] / 10.0))
        fill = QColor(t["fill"])
        fill.setAlpha(int(t["fill_alpha"]))
        self.curve.setBrush(pg.mkBrush(fill))

        # En pyqtgraph las líneas de grilla usan el color del pen del eje (con su propio alfa)
        grid_pen = pg.mkPen(t["grid"])
        text_pen = pg.mkPen(t["axis_text"])
        for name in ("bottom", "left"):
            axis = self.plot_widget.getAxis(name)
            axis.setPen(grid_pen)
            axis.setTextPen(text_pen)
        self.plot_widget.showGrid(x=True, y=True, alpha=t["grid_alpha"] / 100.0)

    def _update_mute_button_style(self):
        if self.is_muted:
            self.mute_btn.setText("🔇 SALIDA MUTEADA (Activar)")
            self.mute_btn.setStyleSheet("""
                QPushButton {
                    background-color: #D32F2F;
                    color: white;
                    font-weight: bold;
                    padding: 6px 12px;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #E53935; }
            """)
            if hasattr(self, 'mini_mute_btn'):
                self.mini_mute_btn.setChecked(True)
                self.mini_mute_btn.setText("🔇 Muteado")
                self.mini_mute_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #D32F2F;
                        color: white;
                        font-weight: bold;
                        padding: 3px 8px;
                        border-radius: 3px;
                    }
                    QPushButton:hover { background-color: #E53935; }
                """)
        else:
            self.mute_btn.setText("🔊 MONITOREO ACTIVO (Mutear)")
            self.mute_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2E7D32;
                    color: white;
                    font-weight: bold;
                    padding: 6px 12px;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #388E3C; }
            """)
            if hasattr(self, 'mini_mute_btn'):
                self.mini_mute_btn.setChecked(False)
                self.mini_mute_btn.setText("🔊 Activo")
                self.mini_mute_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2E7D32;
                        color: white;
                        font-weight: bold;
                        padding: 3px 8px;
                        border-radius: 3px;
                    }
                    QPushButton:hover { background-color: #388E3C; }
                """)

    def toggle_mute(self, checked=None):
        if checked is None:
            self.is_muted = self.mute_btn.isChecked()
        else:
            self.is_muted = checked
            self.mute_btn.setChecked(self.is_muted)
        self._update_mute_button_style()

    def on_fft_size_changed(self):
        self.fft_size = self.fft_combo.currentData()
        self.ring_buffer = np.zeros(self.fft_size, dtype=np.float32)
        self.held_fft = None

    def on_hold_changed(self):
        val = self.hold_slider.value()
        self.hold_val_label.setText(f"{val}%")
        self.decay_factor = 0.20 + (val / 99.0) * 0.792

    def on_y_scale_changed(self):
        is_y_log = (self.y_scale_combo.currentText() == "Logarítmica (dB)")
        self.db_min_spin.setEnabled(is_y_log)
        self.db_max_spin.setEnabled(is_y_log)
        self.update_axes_config()

    def on_db_range_changed(self):
        min_v = self.db_min_spin.value()
        max_v = self.db_max_spin.value()
        if min_v >= max_v - 5:
            if self.sender() == self.db_min_spin:
                self.db_min_spin.setValue(max_v - 5)
            else:
                self.db_max_spin.setValue(min_v + 5)
            return

        self.db_min = self.db_min_spin.value()
        self.db_max = self.db_max_spin.value()
        self.update_axes_config()

    def _create_vumeter(self, label_text):
        layout = QVBoxLayout()
        val_label = QLabel("-60 dB")
        val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val_label.setMinimumWidth(50)

        bar = QProgressBar()
        bar.setOrientation(Qt.Orientation.Vertical)
        bar.setRange(-60, 0)
        bar.setValue(-60)
        bar.setTextVisible(False)
        bar.setFixedWidth(28)
        bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #333;
                border-radius: 4px;
                background-color: #1A1A1A;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(
                    x1:0, y1:1, x2:0, y2:0,
                    stop:0 #00E676, stop:0.75 #FFEA00, stop:1 #FF1744
                );
            }
        """)

        ch_label = QLabel(f"<b>{label_text}</b>")
        ch_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(val_label)
        layout.addWidget(bar, 1, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(ch_label)
        return {"layout": layout, "bar": bar, "label": val_label}

    def _populate_devices(self):
        self.in_combo.blockSignals(True)
        self.out_combo.blockSignals(True)
        self.in_combo.clear()
        self.out_combo.clear()

        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        for idx, dev in enumerate(devices):
            api_name = hostapis[dev['hostapi']]['name']
            dev_label = f"[{api_name}] {dev['name']}"
            if dev['max_input_channels'] > 0:
                self.in_combo.addItem(dev_label, idx)
            if dev['max_output_channels'] > 0:
                self.out_combo.addItem(dev_label, idx)

        default_in, default_out = sd.default.device
        in_idx = self.in_combo.findData(default_in)
        out_idx = self.out_combo.findData(default_out)
        if in_idx >= 0:
            self.in_combo.setCurrentIndex(in_idx)
        if out_idx >= 0:
            self.out_combo.setCurrentIndex(out_idx)

        self.in_combo.blockSignals(False)
        self.out_combo.blockSignals(False)

    def on_sr_changed(self):
        self.current_sr = self.sr_combo.currentData()
        self.ring_buffer = np.zeros(self.fft_size, dtype=np.float32)
        self.held_fft = None
        self.update_axes_config()
        self.restart_audio_stream()

    def update_axes_config(self):
        is_x_log = (self.x_scale_combo.currentText() == "Logarítmica")
        is_y_log = (self.y_scale_combo.currentText() == "Logarítmica (dB)")

        self.plot_widget.setLogMode(x=is_x_log, y=False)
        bottom_axis = self.plot_widget.getAxis('bottom')
        left_axis = self.plot_widget.getAxis('left')

        nyquist = self.current_sr / 2.0
        if is_x_log:
            # Borde izquierdo en log10(10 Hz) = 1.0 para dar exactamente el espaciado 20 - 50 - 100 - 200 - 500 - 1k - 2k - 5k - 10k - 20k hasta Nyquist
            self.plot_widget.setXRange(np.log10(10.0), np.log10(nyquist), padding=0.0)
            freq_marks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]
            ticks = []
            for f in freq_marks:
                if f <= nyquist:
                    label = f"{int(f/1000)}k" if f >= 1000 else str(f)
                    ticks.append((np.log10(f), label))

            # Ticks secundarios para las líneas de sub-década
            minor_freqs = [30, 40, 60, 70, 80, 90, 300, 400, 600, 700, 800, 900, 3000, 4000, 6000, 7000, 8000, 9000, 30000, 40000]
            minor_ticks = [(np.log10(f), "") for f in minor_freqs if f <= nyquist]
            bottom_axis.setTicks([ticks, minor_ticks])
        else:
            self.plot_widget.setXRange(0.0, nyquist, padding=0.0)
            step = 2000 if nyquist <= 24000 else 5000
            ticks = [
                (f, f"{int(f/1000)}k" if f >= 1000 else str(f))
                for f in range(0, int(nyquist) + 1, step)
            ]
            bottom_axis.setTicks([ticks])

        bottom_axis.setLabel("Frecuencia", units="Hz")

        if is_y_log:
            self.plot_widget.setYRange(self.db_min, self.db_max, padding=0.0)
            span = self.db_max - self.db_min
            step_db = 5 if span <= 40 else (10 if span <= 100 else 20)
            first_tick = int(np.ceil(self.db_min / step_db) * step_db)
            db_ticks = [(db, f"{db} dB") for db in range(first_tick, self.db_max + 1, step_db)]
            left_axis.setTicks([db_ticks])
            left_axis.setLabel("Nivel", units="dBFS")
            self.curve.setFillLevel(float(self.db_min))
        else:
            self.plot_widget.setYRange(0.0, 1.0, padding=0.0)
            lin_ticks = [(val / 10.0, f"{val / 10.0:.1f}") for val in range(0, 11, 2)]
            left_axis.setTicks([lin_ticks])
            left_axis.setLabel("Amplitud Lineal")
            self.curve.setFillLevel(0.0)

    def audio_callback(self, indata, outdata, frames, time_info, status):
        if self.is_muted:
            outdata.fill(0.0)
        else:
            outdata[:] = indata

        try:
            self.audio_queue.put_nowait(indata.copy())
        except queue.Full:
            pass

    def restart_audio_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        in_dev = self.in_combo.currentData()
        out_dev = self.out_combo.currentData()
        if in_dev is None or out_dev is None:
            return

        try:
            self.stream = sd.Stream(
                samplerate=self.current_sr,
                blocksize=self.audio_block_size,
                device=(in_dev, out_dev),
                channels=self.channels,
                dtype='float32',
                callback=self.audio_callback
            )
            self.stream.start()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error de Audio",
                f"No se pudo iniciar el stream con los dispositivos/Sample Rate elegidos:\n{e}"
            )

    def update_visuals(self):
        blocks = []
        while not self.audio_queue.empty():
            try:
                blocks.append(self.audio_queue.get_nowait())
            except queue.Empty:
                break

        if not blocks:
            return

        # 1. Vúmetros L y R con el bloque más reciente
        latest_block = blocks[-1]
        eps = 1e-10
        rms_l = np.sqrt(np.mean(latest_block[:, 0] ** 2))
        rms_r = np.sqrt(np.mean(latest_block[:, 1] ** 2)) if latest_block.shape[1] > 1 else rms_l

        db_l = int(np.clip(20 * np.log10(rms_l + eps), -60, 0))
        db_r = int(np.clip(20 * np.log10(rms_r + eps), -60, 0))

        if hasattr(self, 'vu_group') and self.vu_group.isVisible():
            self.vu_left["bar"].setValue(db_l)
            self.vu_left["label"].setText(f"{db_l} dB")
            self.vu_right["bar"].setValue(db_r)
            self.vu_right["label"].setText(f"{db_r} dB")

        # 2. Actualizar Ring Buffer para el tamaño de FFT seleccionado
        for blk in blocks:
            mono_blk = np.mean(blk, axis=1)
            n = len(mono_blk)
            if n >= self.fft_size:
                self.ring_buffer[:] = mono_blk[-self.fft_size:]
            else:
                self.ring_buffer = np.roll(self.ring_buffer, -n)
                self.ring_buffer[-n:] = mono_blk

        # 3. Calcular FFT sobre el Ring Buffer completo
        window = np.hanning(self.fft_size)
        fft_mag = np.abs(np.fft.rfft(self.ring_buffer * window)) * (2.0 / np.sum(window))

        if self.held_fft is None or len(self.held_fft) != len(fft_mag):
            self.held_fft = fft_mag
        else:
            decayed = self.held_fft * self.decay_factor + fft_mag * (1.0 - self.decay_factor)
            self.held_fft = np.maximum(fft_mag, decayed)

        freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.current_sr)

        # Tomamos bins reales desde >= 25 Hz y anclamos el extremo izquierdo como tira elástica
        valid = freqs >= 25.0
        f_real = freqs[valid]
        mag_real = self.held_fft[valid]

        is_y_log = (self.y_scale_combo.currentText() == "Logarítmica (dB)")
        if is_y_log:
            y_floor = float(self.db_min)
            y_ceil = float(self.db_max)
            y_real = np.clip(20 * np.log10(mag_real + eps), y_floor, y_ceil)
        else:
            y_floor = 0.0
            y_ceil = 1.0
            y_real = np.clip(mag_real, y_floor, y_ceil)

        # 4. Si el modo Retro VFD está activo, alimentar la pantalla Pioneer DEQ-7600
        if self.display_stack.currentIndex() == 1 and self.vfd_widget is not None:
            self.vfd_widget.set_levels_from_fft(freqs, self.held_fft, float(self.db_min), float(self.db_max))
            return

        # 5. Modo Normal: Efecto "Tira Elástica" + Interpolación Spline Cúbica
        is_x_log = (self.x_scale_combo.currentText() == "Logarítmica")
        if len(f_real) > 4:
            first_y = y_real[0]
            if is_x_log:
                # El borde izquierdo visible es 10.0 Hz (log10 = 1.0). Anclamos en 10 Hz en el piso
                # y en 15 Hz a mitad de camino para que las frecuencias bajas tiren elásticamente de la punta
                anchor_f = np.array([10.0, 15.0])
                anchor_y = np.array([y_floor, y_floor + 0.35 * (first_y - y_floor)])

                f_full = np.concatenate([anchor_f, f_real])
                y_full = np.concatenate([anchor_y, y_real])

                log_f = np.log10(f_full)
                log_f_smooth = np.linspace(log_f[0], log_f[-1], 700)
                spline = make_interp_spline(log_f, y_full, k=3)
                y_smooth = np.clip(spline(log_f_smooth), y_floor, y_ceil)
                x_smooth = 10 ** log_f_smooth
            else:
                anchor_f = np.array([0.0, 12.0])
                anchor_y = np.array([y_floor, y_floor + 0.35 * (first_y - y_floor)])

                f_full = np.concatenate([anchor_f, f_real])
                y_full = np.concatenate([anchor_y, y_real])

                x_smooth = np.linspace(0.0, f_full[-1], 700)
                spline = make_interp_spline(f_full, y_full, k=3)
                y_smooth = np.clip(spline(x_smooth), y_floor, y_ceil)

            self.curve.setData(x_smooth, y_smooth)
        else:
            self.curve.setData(f_real, y_real)

    def closeEvent(self, event):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        if self.theme_save_timer.isActive():
            self.theme_save_timer.stop()
            self.save_theme()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AudioAnalyzerApp()
    window.show()
    sys.exit(app.exec())
