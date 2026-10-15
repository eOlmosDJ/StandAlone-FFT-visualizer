import sys
import math
import queue
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QSlider
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QLinearGradient, QPainterPath, QPixmap
)


# 15 Frecuencias centrales ISO estándar del Pioneer DEQ-7600
PIONEER_BANDS = [31.5, 50, 80, 125, 200, 315, 500, 800, 1300, 2000, 3200, 5000, 8000, 12500, 20000]
BAND_LABELS = ["31.5", "50", "80", "125", "200", "315", "500", "800", "1.3k", "2k", "3.2k", "5k", "8k", "12.5k", "20k"]
SFC_PRESETS = ["ROCK", "POPS", "JAZZ", "VOCAL", "HIP HOP", "USER1", "USER2", "USER3"]

# Patrón REAL de la grilla VFD del DEQ-7600 (medido sobre fotos del aparato):
# cada fila de cada columna está partida horizontalmente en N segmentos, y N varía
# por columna formando una "V": 4 en los extremos (graves/agudos) y 1 segmento
# entero (barra ancha) en los medios (500 / 800 / 1.3k).
SEGMENT_SUBDIVISIONS = [4, 4, 3, 3, 2, 2, 1, 1, 1, 2, 2, 3, 3, 4, 4]
NUM_ROWS = 20  # filas verticales por columna


class RetroVFDWidget(QWidget):
    """
    Widget QPainter que simula la pantalla VFD del procesador Pioneer DEQ-7600.

    Fiel a las fotos del aparato real:
    1. 15 columnas x 20 filas de segmentos con esquinas redondeadas.
    2. Cada fila está partida horizontalmente en N segmentos según la columna
       (SEGMENT_SUBDIVISIONS: 4-4-3-3-2-2-1-1-1-2-2-3-3-4-4).
    3. Sin peak hold: las barras solo suben (instantáneo) y bajan (suave).
    4. Persistencia de fósforo: un segmento que se apaga se desvanece en ~100 ms.
    5. Matriz de segmentos apagados visible de fondo.
    Los segmentos se pre-renderizan como sprites (QPixmap) para mantener 60 FPS.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 160)

        self.num_bands = len(PIONEER_BANDS)
        self.num_levels = NUM_ROWS
        self.solo_spectrum = False  # Modo solo analizador (sin iconos, butacas ni presets)

        # Nivel normalizado por banda (0.0 a 1.0)
        self.band_levels = np.zeros(self.num_bands, dtype=np.float32)
        # Brillo de cada segmento (banda x fila), 0.0 apagado .. 1.0 encendido
        self.seg_brightness = np.zeros((self.num_bands, self.num_levels), dtype=np.float32)

        # Balística: ataque instantáneo, caída suave (sin retención de pico)
        self.decay_speed = 0.75       # factor de caída del nivel por frame
        self.afterglow = 0.55         # persistencia del fósforo por frame (~100 ms a 60 FPS)

        # Cache de sprites de segmentos (se invalida al cambiar tamaño / DPI)
        self._sprite_cache = {}
        self._sprite_key = None

        # Estado estético interactivo
        self.active_sfc_idx = 0     # 0: ROCK, 1: POPS, 2: JAZZ...
        self.active_mode_icon = 0   # 0: STUDIO, 1: CONCERT, 2: JAZZ, 3: STADIUM, 4: CLUB
        self.volume_display = 28    # Volumen idéntico al de la foto de referencia ("28" / "22")

        # Paleta de colores VFD fósforo de alta luminiscencia
        self.c_bg = QColor(4, 7, 8)                # Fondo de cavidad de vidrio oscura
        self.c_unlit = QColor(10, 25, 27, 120)      # Segmento apagado visible
        self.c_unlit_border = QColor(14, 38, 42, 90)

        # Fósforo Cian (Columnas principales FFT)
        self.c_cyan_bloom = QColor(0, 245, 255, 45) # Halo exterior de difusión
        self.c_cyan_glow = QColor(30, 255, 245, 120)# Resplandor medio
        self.c_cyan_body = QColor(70, 255, 245, 255)# Fósforo vivo
        self.c_cyan_core = QColor(240, 255, 255, 255)# Núcleo blanco de alta energía

        # Fósforo Verde (Iconos SFC / Presets activos)
        self.c_green_bloom = QColor(0, 255, 90, 40)
        self.c_green_glow = QColor(40, 255, 120, 130)
        self.c_green_body = QColor(65, 255, 130, 255)
        self.c_green_core = QColor(230, 255, 235, 255)
        self.c_green_dim = QColor(16, 50, 28, 110)

        # Fósforo Ámbar / Amarillo (USER1..3 y Subwoofer S.W)
        self.c_amber_glow = QColor(255, 210, 0, 110)
        self.c_amber_body = QColor(255, 220, 30, 255)
        self.c_amber_core = QColor(255, 252, 210, 255)
        self.c_amber_dim = QColor(50, 40, 8, 100)

        # Fósforo Púrpura (Acústica de asientos / parlantes)
        self.c_purple_glow = QColor(180, 110, 255, 90)
        self.c_purple_body = QColor(195, 130, 255, 255)
        self.c_purple_core = QColor(245, 230, 255, 255)

        # Azul eléctrico (Botón SFC MODE)
        self.c_blue_glow = QColor(0, 140, 255, 110)
        self.c_blue_body = QColor(40, 160, 255, 255)
        self.c_blue_core = QColor(220, 240, 255, 255)

    def set_levels_from_fft(self, freqs, fft_mag, db_min=-75.0, db_max=0.0):
        """
        Calcula el nivel por banda de 2/3 de octava a partir del espectro FFT
        y actualiza las 15 columnas. Debe llamarse desde el hilo de UI.
        """
        if len(freqs) == 0 or len(fft_mag) == 0:
            return

        eps = 1e-9
        db_vals = 20.0 * np.log10(fft_mag + eps)
        span = max(1e-6, db_max - db_min)

        targets = np.zeros(self.num_bands, dtype=np.float32)
        for i, fc in enumerate(PIONEER_BANDS):
            mask = (freqs >= fc / 1.26) & (freqs < fc * 1.26)
            if np.any(mask):
                val_db = np.max(db_vals[mask])
            else:
                val_db = db_vals[np.argmin(np.abs(freqs - fc))]
            targets[i] = np.clip((val_db - db_min) / span, 0.0, 1.0)

        self._apply_levels(targets)

    def set_test_pattern(self, phase=0.0):
        """Patrón dinámico para demostración visual (sin audio)."""
        idx = np.arange(self.num_bands)
        wave = 0.5 + 0.44 * np.sin(phase + idx * 0.48) + 0.08 * np.sin(phase * 3.1 + idx * 1.7)
        targets = np.clip(wave * np.exp(-0.02 * idx), 0.05, 0.98).astype(np.float32)
        self._apply_levels(targets)

    def _apply_levels(self, targets):
        """Ataque instantáneo / caída suave + persistencia de fósforo por segmento."""
        rising = targets > self.band_levels
        self.band_levels = np.where(
            rising, targets,
            self.band_levels * self.decay_speed + targets * (1.0 - self.decay_speed)
        ).astype(np.float32)

        lit_rows = np.round(self.band_levels * self.num_levels).astype(int)
        rows = np.arange(self.num_levels)
        lit_mask = rows[np.newaxis, :] < lit_rows[:, np.newaxis]
        # Encendido inmediato; al apagarse el fósforo se desvanece gradualmente
        self.seg_brightness = np.where(lit_mask, 1.0, self.seg_brightness * self.afterglow).astype(np.float32)
        self.seg_brightness[self.seg_brightness < 0.03] = 0.0
        self.update()

    def set_solo_spectrum(self, enabled: bool):
        """Activa o desactiva el modo de solo analizador de espectro (sin botones laterales ni presets)."""
        if self.solo_spectrum != enabled:
            self.solo_spectrum = enabled
            self._sprite_cache.clear()
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = self.width()
        h = self.height()

        if self.solo_spectrum:
            # Modo Solo Analizador (Minimizado / Pura visualización):
            # Ocupa todo el widget con la cavidad de cristal VFD y filamentos,
            # mostrando exclusivamente las 15 columnas del espectro y su cápsula de frecuencias.
            margin = 5.0
            vfd_rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)
            self._draw_vfd_glass(p, vfd_rect)
            self._draw_vfd_filaments(p, vfd_rect)

            center_rect = QRectF(vfd_rect.left() + 6, vfd_rect.top() + 6, vfd_rect.width() - 12, vfd_rect.height() - 12)
            self._draw_spectrum_columns(p, center_rect, include_presets=False)
            return

        # Modo Completo: Faceplate Pioneer DEQ-7600
        # 1. Chasis exterior metálico Pioneer 1-DIN
        self._draw_bezel(p, w, h)

        # 2. Ventana de cristal VFD de alto contraste
        vfd_margin_x = 20
        vfd_margin_y = 42
        vfd_rect = QRectF(vfd_margin_x, vfd_margin_y, w - 2 * vfd_margin_x, h - vfd_margin_y - 18)
        self._draw_vfd_glass(p, vfd_rect)

        # 3. Filamentos VFD casi invisibles
        self._draw_vfd_filaments(p, vfd_rect)

        # Dimensiones de las 3 secciones (Izquierda: Iconos, Centro: 15 Bandas, Derecha: Volumen/Altavoces)
        left_w = vfd_rect.width() * 0.155
        right_w = vfd_rect.width() * 0.135
        center_x = vfd_rect.left() + left_w
        center_w = vfd_rect.width() - left_w - right_w

        # 4. Iconos SFC y Modos con efecto Neón Resplandeciente (Izquierda)
        left_rect = QRectF(vfd_rect.left() + 5, vfd_rect.top() + 5, left_w - 10, vfd_rect.height() - 10)
        self._draw_left_sfc_icons(p, left_rect)

        # 5. Analizador de 15 Columnas con Sub-Líneas Divididas y Brillo Neón (Centro)
        center_rect = QRectF(center_x, vfd_rect.top() + 6, center_w, vfd_rect.height() - 12)
        self._draw_spectrum_columns(p, center_rect, include_presets=True)

        # 6. Display 7-Segmentos Neón + Altavoces (Derecha)
        right_rect = QRectF(vfd_rect.right() - right_w + 3, vfd_rect.top() + 5, right_w - 8, vfd_rect.height() - 10)
        self._draw_right_info(p, right_rect)

    def _draw_bezel(self, p, w, h):
        """Marco de aluminio negro cepillado con serigrafía Pioneer DEQ-7600."""
        chassis_grad = QLinearGradient(0, 0, 0, h)
        chassis_grad.setColorAt(0.0, QColor(42, 45, 48))
        chassis_grad.setColorAt(0.06, QColor(20, 22, 24))
        chassis_grad.setColorAt(0.94, QColor(14, 15, 17))
        chassis_grad.setColorAt(1.0, QColor(32, 35, 38))
        p.fillRect(0, 0, w, h, chassis_grad)

        p.setPen(QPen(QColor(75, 80, 86), 1.5))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 5, 5)
        p.setPen(QPen(QColor(8, 10, 11), 1.5))
        p.drawRoundedRect(QRectF(3, 3, w - 6, h - 6), 4, 4)

        # Tornillos en las 4 esquinas
        for sx, sy in [(11, 11), (w - 11, 11), (11, h - 11), (w - 11, h - 11)]:
            p.setPen(QPen(QColor(65, 70, 75), 1))
            p.setBrush(QColor(30, 33, 36))
            p.drawEllipse(QPointF(sx, sy), 3, 3)
            p.setPen(QColor(12, 14, 16))
            p.drawLine(int(sx - 2), int(sy), int(sx + 2), int(sy))

        # Serigrafía superior Pioneer DEQ-7600
        p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        p.setPen(QColor(225, 230, 235))
        p.drawText(24, 25, "Ω  PIONEER")

        p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        p.setPen(QColor(195, 200, 205))
        p.drawText(128, 25, "DEQ-7600")

        p.setFont(QFont("Arial", 8, QFont.Weight.DemiBold))
        p.setPen(QColor(145, 152, 158))
        p.drawText(210, 25, "DIGITAL SIGNAL PROCESSOR")

        # Texto lateral en el marco
        p.setPen(QColor(120, 128, 135))
        p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        p.drawText(24, 38, "MODE / SOUND FIELD CONTROL")

    def _draw_vfd_glass(self, p, r):
        """Fondo de cavidad de tubo al vacío con tinte azul-verdoso profundo."""
        p.fillRect(r, self.c_bg)

        glass_grad = QLinearGradient(r.left(), r.top(), r.left(), r.bottom())
        glass_grad.setColorAt(0.0, QColor(12, 24, 26, 210))
        glass_grad.setColorAt(0.12, QColor(4, 9, 11, 240))
        glass_grad.setColorAt(0.88, QColor(3, 7, 9, 240))
        glass_grad.setColorAt(1.0, QColor(10, 22, 24, 210))
        p.fillRect(r, glass_grad)

        p.setPen(QPen(QColor(12, 16, 18), 2.0))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QPen(QColor(36, 44, 48), 1.0))
        p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 1, r.width() - 2, r.height() - 2), 2, 2)

    def _draw_vfd_filaments(self, p, r):
        """Hilos conductores de filamento VFD horizontales."""
        p.setPen(QPen(QColor(50, 110, 100, 24), 0.75))
        for i in range(1, 5):
            y = r.top() + i * (r.height() / 5.0)
            p.drawLine(int(r.left() + 6), int(y), int(r.right() - 6), int(y))

    def _draw_neon_text(self, p, rect, text, font, col_body, col_glow, col_core, align=Qt.AlignmentFlag.AlignCenter):
        """Dibuja texto con resplandor neón multicapa (bloom + hot core)."""
        p.setFont(font)
        # Capa 1: Halo exterior
        p.setPen(col_glow)
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            p.drawText(rect.adjusted(dx, dy, dx, dy), align, text)
        # Capa 2: Cuerpo brillante
        p.setPen(col_body)
        p.drawText(rect, align, text)
        # Capa 3: Núcleo blanco
        p.setPen(col_core)
        p.drawText(rect, align, text)

    def _draw_neon_box(self, p, rect, col_body, col_glow, radius=3.0, width=1.3):
        """Dibuja un rectángulo redondeado con resplandor neón multicapa."""
        # Halo de difusión exterior
        p.setPen(QPen(col_glow, width + 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        # Trazo nítido brillante
        p.setPen(QPen(col_body, width))
        p.drawRoundedRect(rect, radius, radius)

    def _draw_left_sfc_icons(self, p, r):
        """Iconos retro de campos sonoros con efecto de brillo neón auténtico."""
        icons = [
            ("STUDIO", "🎧"),
            ("CONCERT", "▦"),
            ("JAZZ", "🎷"),
            ("STADIUM", "◎"),
            ("CLUB", "♫"),
            ("SFC MODE", "DSP")
        ]

        grid_cols = 2
        grid_rows = 3
        cell_w = r.width() / grid_cols
        cell_h = (r.height() - 8) / grid_rows

        font_label = QFont("Arial", 6, QFont.Weight.Bold)
        font_icon = QFont("Segoe UI Emoji", 11)

        for idx, (title, sym) in enumerate(icons):
            col = idx % grid_cols
            row = idx // grid_cols
            cx = r.left() + col * cell_w
            cy = r.top() + row * cell_h
            box = QRectF(cx + 2, cy + 2, cell_w - 4, cell_h - 4)

            is_active = (idx == self.active_mode_icon)

            if idx == 5:
                # Botón azul SFC MODE
                if is_active:
                    p.fillRect(box, QColor(10, 45, 95, 180))
                    self._draw_neon_box(p, box, self.c_blue_body, self.c_blue_glow, 3.0, 1.4)
                    self._draw_neon_text(p, QRectF(box.left(), box.top() + 2, box.width(), box.height() * 0.55),
                                         sym, font_icon, self.c_blue_body, self.c_blue_glow, self.c_blue_core)
                    self._draw_neon_text(p, QRectF(box.left(), box.top() + box.height() * 0.54, box.width(), box.height() * 0.42),
                                         title, font_label, self.c_blue_body, self.c_blue_glow, self.c_blue_core)
                else:
                    p.fillRect(box, QColor(5, 18, 35, 100))
                    p.setPen(QPen(QColor(20, 50, 90), 1.0))
                    p.drawRoundedRect(box, 3, 3)
                    p.setFont(font_icon)
                    p.setPen(QColor(50, 90, 140))
                    p.drawText(QRectF(box.left(), box.top() + 2, box.width(), box.height() * 0.55), Qt.AlignmentFlag.AlignCenter, sym)
                    p.setFont(font_label)
                    p.drawText(QRectF(box.left(), box.top() + box.height() * 0.54, box.width(), box.height() * 0.42), Qt.AlignmentFlag.AlignCenter, title)
            else:
                # Iconos verdes de sonido
                if is_active:
                    # Fondo con ligero tinte verde encendido
                    p.fillRect(box, QColor(8, 42, 18, 160))
                    # Resplandor neón verde
                    self._draw_neon_box(p, box, self.c_green_body, self.c_green_glow, 3.0, 1.4)
                    self._draw_neon_text(p, QRectF(box.left(), box.top() + 2, box.width(), box.height() * 0.55),
                                         sym, font_icon, self.c_green_body, self.c_green_glow, self.c_green_core)
                    self._draw_neon_text(p, QRectF(box.left(), box.top() + box.height() * 0.54, box.width(), box.height() * 0.42),
                                         title, font_label, self.c_green_body, self.c_green_glow, self.c_green_core)
                else:
                    # Apagado tenue
                    p.fillRect(box, QColor(3, 14, 7, 70))
                    p.setPen(QPen(self.c_green_dim, 0.9))
                    p.drawRoundedRect(box, 3, 3)
                    p.setFont(font_icon)
                    p.setPen(QColor(24, 75, 40))
                    p.drawText(QRectF(box.left(), box.top() + 2, box.width(), box.height() * 0.55), Qt.AlignmentFlag.AlignCenter, sym)
                    p.setFont(font_label)
                    p.drawText(QRectF(box.left(), box.top() + box.height() * 0.54, box.width(), box.height() * 0.42), Qt.AlignmentFlag.AlignCenter, title)

    def _get_segment_sprites(self, seg_w, seg_h):
        """
        Pre-renderiza (y cachea) los sprites de un segmento encendido y apagado.
        Devuelve (lit_pixmap, unlit_pixmap, pad). El sprite encendido incluye el halo
        de fósforo, por eso se dibuja desplazado -pad.
        """
        dpr = self.devicePixelRatioF()
        key = (round(seg_w, 2), round(seg_h, 2), dpr)
        cached = self._sprite_cache.get(key)
        if cached is not None:
            return cached
        if len(self._sprite_cache) > 32:
            self._sprite_cache.clear()

        pad = max(2.0, seg_h * 0.9)
        body = QRectF(pad, pad, seg_w, seg_h)
        # Radio sutil: esquinas suavemente redondeadas pero forma rectangular nítida como en el DEQ-7600
        radius = min(1.4, min(seg_w, seg_h) * 0.22)

        def new_pixmap():
            pm = QPixmap(int(math.ceil((seg_w + 2 * pad) * dpr)), int(math.ceil((seg_h + 2 * pad) * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            return pm

        # --- Segmento encendido: halo difuso + cuerpo cian + núcleo blanco ---
        lit = new_pixmap()
        q = QPainter(lit)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.setPen(Qt.PenStyle.NoPen)
        for grow, alpha in ((pad * 0.95, 16), (pad * 0.6, 32), (pad * 0.3, 70)):
            q.setBrush(QColor(0, 235, 255, alpha))
            q.drawRoundedRect(body.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow)
        grad = QLinearGradient(0, body.top(), 0, body.bottom())
        grad.setColorAt(0.0, QColor(160, 255, 250))
        grad.setColorAt(0.5, QColor(70, 250, 235))
        grad.setColorAt(1.0, QColor(28, 200, 200))
        q.setBrush(QBrush(grad))
        q.drawRoundedRect(body, radius, radius)
        inset_x = max(1.0, seg_w * 0.08)
        core = body.adjusted(inset_x, seg_h * 0.28, -inset_x, -seg_h * 0.36)
        q.setBrush(QColor(235, 255, 255, 220))
        q.drawRoundedRect(core, radius * 0.5, radius * 0.5)
        q.end()

        # --- Segmento apagado: fósforo sin excitar, visible a través del vidrio ---
        unlit = new_pixmap()
        q = QPainter(unlit)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.setPen(QPen(QColor(46, 80, 86, 110), 0.6))
        q.setBrush(QColor(22, 44, 48, 150))
        q.drawRoundedRect(body, radius, radius)
        q.end()

        sprites = (lit, unlit, pad)
        self._sprite_cache[key] = sprites
        return sprites

    def _draw_spectrum_columns(self, p, r, include_presets=True):
        """
        15 columnas x 20 filas. Cada fila se parte en N segmentos horizontales
        según SEGMENT_SUBDIVISIONS (patrón en "V" del DEQ-7600 real).
        """
        h_total = r.height()
        h_sfc_presets = 21 if include_presets else 0
        gap_presets = 4 if include_presets else 0
        h_freq_capsule = 22
        h_bars = h_total - h_sfc_presets - gap_presets - h_freq_capsule - 10

        bars_rect = QRectF(r.left(), r.top() + 3, r.width(), h_bars)

        # Columnas: todas del mismo ancho, separadas por una franja oscura clara
        col_gap_ratio = 0.22
        col_w = bars_rect.width() / (self.num_bands + (self.num_bands - 1) * col_gap_ratio)
        col_gap = col_w * col_gap_ratio
        sub_gap = max(1.2, col_w * 0.055)   # ranura entre segmentos de una misma fila

        # Filas: el segmento ocupa ~70% del paso vertical, el resto es separación fina entre filas
        row_pitch = bars_rect.height() / self.num_levels
        seg_h = row_pitch * 0.70

        # Precalcular geometría por columna
        layout = []
        for col_idx in range(self.num_bands):
            n = SEGMENT_SUBDIVISIONS[col_idx]
            cx = bars_rect.left() + col_idx * (col_w + col_gap)
            sub_w = (col_w - (n - 1) * sub_gap) / n
            lit, unlit, pad = self._get_segment_sprites(sub_w, seg_h)
            xs = [cx + k * (sub_w + sub_gap) for k in range(n)]
            layout.append((xs, lit, unlit, pad))

        def row_y(row):
            return bars_rect.bottom() - (row + 1) * row_pitch + (row_pitch - seg_h) / 2.0

        # Pasada 1: matriz apagada (solo donde el segmento no está a pleno)
        for col_idx, (xs, lit, unlit, pad) in enumerate(layout):
            for row in range(self.num_levels):
                if self.seg_brightness[col_idx, row] >= 1.0:
                    continue
                y = row_y(row) - pad
                for x in xs:
                    p.drawPixmap(QPointF(x - pad, y), unlit)

        # Pasada 2: segmentos encendidos (con persistencia de fósforo vía opacidad)
        for col_idx, (xs, lit, unlit, pad) in enumerate(layout):
            for row in range(self.num_levels):
                b = float(self.seg_brightness[col_idx, row])
                if b <= 0.0:
                    continue
                p.setOpacity(b)
                y = row_y(row) - pad
                for x in xs:
                    p.drawPixmap(QPointF(x - pad, y), lit)
        p.setOpacity(1.0)

        # Cápsula de frecuencias con borde neón
        y_freq = bars_rect.bottom() + 4
        freq_rect = QRectF(r.left(), y_freq, r.width(), h_freq_capsule)
        self._draw_frequency_capsule(p, freq_rect, col_w, col_gap)

        if include_presets:
            # Fila de Presets SFC
            y_presets = freq_rect.bottom() + gap_presets
            presets_rect = QRectF(r.left(), y_presets, r.width(), h_sfc_presets)
            self._draw_sfc_presets(p, presets_rect)

    def _draw_frequency_capsule(self, p, r, col_w, col_gap):
        """Cápsula redondeada serigrafiada con resplandor neón cian/blanco."""
        # Halo de difusión exterior del marco
        self._draw_neon_box(p, r, QColor(140, 240, 250, 220), QColor(0, 229, 255, 60), radius=4.0, width=1.2)
        p.fillRect(r.adjusted(1, 1, -1, -1), QColor(5, 16, 20, 180))

        font_hz = QFont("Arial", 7, QFont.Weight.Bold)
        p.setFont(font_hz)

        for col_idx, label in enumerate(BAND_LABELS):
            cx = r.left() + col_idx * (col_w + col_gap)
            text_rect = QRectF(cx - 2, r.top(), col_w + 4, r.height())
            self._draw_neon_text(p, text_rect, label, font_hz,
                                 QColor(180, 245, 255), QColor(0, 200, 255, 60), QColor(245, 255, 255))

    def _draw_sfc_presets(self, p, r):
        """Fila inferior de cápsulas de presets con iluminación de fósforo verde y ámbar."""
        num_p = len(SFC_PRESETS)
        spacing = 4.0
        btn_w = (r.width() - (num_p - 1) * spacing) / num_p

        font_p = QFont("Arial", 6, QFont.Weight.Bold)
        p.setFont(font_p)

        for i, name in enumerate(SFC_PRESETS):
            bx = r.left() + i * (btn_w + spacing)
            box = QRectF(bx, r.top(), btn_w, r.height() - 2)

            is_active = (i == self.active_sfc_idx)
            is_user = name.startswith("USER")

            if is_user:
                # USER1..3 en ámbar/amarillo
                if is_active:
                    p.fillRect(box, QColor(45, 35, 6, 170))
                    self._draw_neon_box(p, box, self.c_amber_body, self.c_amber_glow, 3.0, 1.3)
                    self._draw_neon_text(p, box, name, font_p, self.c_amber_body, self.c_amber_glow, self.c_amber_core)
                else:
                    p.fillRect(box, QColor(15, 12, 3, 70))
                    p.setPen(QPen(self.c_amber_dim, 0.9))
                    p.drawRoundedRect(box, 3, 3)
                    p.setPen(QColor(130, 105, 30))
                    p.drawText(box, Qt.AlignmentFlag.AlignCenter, name)
            else:
                # ROCK, POPS, JAZZ, VOCAL, HIP HOP en verde VFD
                if is_active:
                    p.fillRect(box, QColor(10, 42, 18, 170))
                    self._draw_neon_box(p, box, self.c_green_body, self.c_green_glow, 3.0, 1.3)
                    self._draw_neon_text(p, box, name, font_p, self.c_green_body, self.c_green_glow, self.c_green_core)
                else:
                    p.fillRect(box, QColor(4, 15, 8, 70))
                    p.setPen(QPen(self.c_green_dim, 0.9))
                    p.drawRoundedRect(box, 3, 3)
                    p.setPen(QColor(40, 110, 55))
                    p.drawText(box, Qt.AlignmentFlag.AlignCenter, name)

    def _draw_right_info(self, p, r):
        """Sección derecha: Volumen 7-segmentos neón, altavoz S.W y distribución acústica."""
        # 1. Display de volumen neón cian (ej. "28")
        font_vol = QFont("Consolas", 25, QFont.Weight.Bold)
        vol_rect = QRectF(r.left() + 6, r.top() + 3, r.width() - 12, 38)

        # Fondo tenue '88' típico de tubos 7-segmentos VFD
        p.setFont(font_vol)
        p.setPen(QColor(16, 38, 42, 120))
        p.drawText(vol_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "88")

        # Valor activo con resplandor neón
        self._draw_neon_text(p, vol_rect, f"{self.volume_display:2d}", font_vol,
                             self.c_cyan_body, self.c_cyan_glow, self.c_cyan_core,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # F R L indicadores
        font_frl = QFont("Arial", 6, QFont.Weight.Bold)
        p.setFont(font_frl)
        p.setPen(QColor(120, 200, 220, 190))
        p.drawText(QRectF(r.left() + 4, r.top() + 5, 20, 36), Qt.AlignmentFlag.AlignLeft, "F\nR\nL")

        # 2. Subwoofer S.W en ámbar/amarillo brillante
        sw_rect = QRectF(r.left() + 4, r.top() + 47, r.width() - 8, 20)
        p.setFont(QFont("Segoe UI Emoji", 10))
        self._draw_neon_text(p, QRectF(sw_rect.left(), sw_rect.top(), 20, 20), "🔊",
                             QFont("Segoe UI Emoji", 10), self.c_amber_body, self.c_amber_glow, self.c_amber_core)
        self._draw_neon_text(p, QRectF(sw_rect.left() + 22, sw_rect.top(), 50, 20), "S.W",
                             QFont("Arial", 8, QFont.Weight.Bold), self.c_amber_body, self.c_amber_glow, self.c_amber_core,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # 3. Asientos / Staging acústico en púrpura neón
        seats_rect = QRectF(r.left() + 6, sw_rect.bottom() + 6, r.width() - 12, r.bottom() - sw_rect.bottom() - 10)
        w_seat = seats_rect.width() * 0.72
        h_seat = seats_rect.height() * 0.22

        p.setBrush(QColor(50, 15, 80, 80))
        for row_idx in range(3):
            s_box = QRectF(seats_rect.center().x() - w_seat / 2, seats_rect.top() + 3 + row_idx * (h_seat + 4), w_seat, h_seat)
            self._draw_neon_box(p, s_box, self.c_purple_body, self.c_purple_glow, radius=2.5, width=1.2)
            # Dibujar dos butacas (asientos) en azul cian neón dentro de cada fila
            seat_cw = (s_box.width() - 7) / 2
            seat_ch = s_box.height() - 4
            for s_i in range(2):
                c_rect = QRectF(s_box.left() + 2.5 + s_i * (seat_cw + 2), s_box.top() + 2, seat_cw, seat_ch)
                p.setPen(QPen(QColor(30, 140, 240, 180), 0.9))
                p.setBrush(QColor(0, 110, 200, 130))
                p.drawRoundedRect(c_rect, 1.5, 1.5)

        font_fr = QFont("Arial", 6, QFont.Weight.Bold)
        self._draw_neon_text(p, QRectF(seats_rect.left(), seats_rect.top() + 1, 14, 14), "F", font_fr,
                             self.c_purple_body, self.c_purple_glow, self.c_purple_core)
        self._draw_neon_text(p, QRectF(seats_rect.left(), seats_rect.bottom() - 14, 14, 14), "R", font_fr,
                             self.c_purple_body, self.c_purple_glow, self.c_purple_core)


class RetroVFDWindow(QMainWindow):
    """
    Ventana independiente del Procesador Digital Pioneer DEQ-7600
    que puede correr por sí misma o sincronizarse con el stream principal.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PIONEER DEQ-7600 - Retro VFD Display")
        self.resize(1020, 350)
        self.setStyleSheet("background-color: #101214;")

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(12, 12, 12, 12)

        self.vfd = RetroVFDWidget()
        layout.addWidget(self.vfd, 1)

        self.is_mini_mode = False
        self.is_always_on_top = False
        self.saved_geometry = None

        # Barra de control inferior para cambiar presets, modo y fuente de audio
        self.ctrl_widget = QWidget()
        ctrl_layout = QHBoxLayout(self.ctrl_widget)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)

        ctrl_layout.addWidget(QLabel("<b style='color:#AAA;'>Entrada Audio:</b>"))
        self.audio_in_combo = QComboBox()
        self.audio_in_combo.setMinimumWidth(180)
        self._populate_audio_devices()
        self.audio_in_combo.currentIndexChanged.connect(self._restart_standalone_audio)
        ctrl_layout.addWidget(self.audio_in_combo)

        ctrl_layout.addSpacing(10)
        ctrl_layout.addWidget(QLabel("<b style='color:#AAA;'>Modo SFC:</b>"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(SFC_PRESETS)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_change)
        ctrl_layout.addWidget(self.preset_combo)

        ctrl_layout.addSpacing(10)
        ctrl_layout.addWidget(QLabel("<b style='color:#AAA;'>Efecto:</b>"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["STUDIO", "CONCERT", "JAZZ", "STADIUM", "CLUB"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        ctrl_layout.addWidget(self.mode_combo)

        ctrl_layout.addStretch()

        self.demo_btn = QPushButton("▶ Señal Demo")
        self.demo_btn.setCheckable(True)
        self.demo_btn.toggled.connect(self._toggle_demo)
        ctrl_layout.addWidget(self.demo_btn)

        self.mini_btn = QPushButton("🗖 Solo Espectro")
        self.mini_btn.setCheckable(True)
        self.mini_btn.setToolTip("Mostrar exclusivamente el analizador de espectro (M o Esc)")
        self.mini_btn.clicked.connect(self.toggle_mini_mode)
        ctrl_layout.addWidget(self.mini_btn)

        self.on_top_btn = QPushButton("📌 Fijar")
        self.on_top_btn.setCheckable(True)
        self.on_top_btn.setToolTip("Mantener siempre visible sobre otras ventanas (P)")
        self.on_top_btn.clicked.connect(self.toggle_always_on_top)
        ctrl_layout.addWidget(self.on_top_btn)

        self.fullscreen_btn = QPushButton("⛶ Pantalla Completa")
        self.fullscreen_btn.setToolTip("Alternar pantalla completa (F11)")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        ctrl_layout.addWidget(self.fullscreen_btn)

        layout.addWidget(self.ctrl_widget)

        # Cola y temporizador seguros para audio en modo independiente
        self.standalone_queue = queue.Queue(maxsize=4)
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(16)
        self.ui_timer.timeout.connect(self._process_standalone_queue)
        self.ui_timer.start()

        self.sd_stream = None
        self.demo_timer = QTimer(self)
        self.demo_timer.timeout.connect(self._on_demo_tick)
        self.demo_phase = 0.0

    def _populate_audio_devices(self):
        try:
            import sounddevice as sd
            self.audio_in_combo.clear()
            self.audio_in_combo.addItem("— Desactivado —", -1)
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    self.audio_in_combo.addItem(f"{dev['name']}", idx)
            default_in = sd.default.device[0]
            found = self.audio_in_combo.findData(default_in)
            if found >= 0:
                self.audio_in_combo.setCurrentIndex(found)
        except Exception:
            self.audio_in_combo.addItem("Sounddevice no disponible", -1)

    def _process_standalone_queue(self):
        last_data = None
        while not self.standalone_queue.empty():
            try:
                last_data = self.standalone_queue.get_nowait()
            except queue.Empty:
                break
        if last_data is not None:
            freqs, mag = last_data
            self.feed_audio_data(freqs, mag)

    def _restart_standalone_audio(self):
        if self.sd_stream is not None:
            try:
                self.sd_stream.stop()
                self.sd_stream.close()
            except Exception:
                pass
            self.sd_stream = None

        dev_idx = self.audio_in_combo.currentData()
        if dev_idx is None or dev_idx < 0:
            return

        try:
            import sounddevice as sd
            sr = 48000
            bs = 1024

            def callback(indata, frames, time_info, status):
                mono = np.mean(indata, axis=1)
                win = np.hanning(len(mono))
                mag = np.abs(np.fft.rfft(mono * win)) * (2.0 / np.sum(win))
                freqs = np.fft.rfftfreq(len(mono), 1.0 / sr)
                try:
                    self.standalone_queue.put_nowait((freqs, mag))
                except queue.Full:
                    pass

            self.sd_stream = sd.InputStream(
                device=dev_idx,
                samplerate=sr,
                blocksize=bs,
                channels=1,
                dtype='float32',
                callback=callback
            )
            self.sd_stream.start()
            if self.demo_btn.isChecked():
                self.demo_btn.setChecked(False)
        except Exception as e:
            print("Error iniciando audio standalone:", e)

    def _on_preset_change(self, idx):
        self.vfd.active_sfc_idx = idx
        self.vfd.update()

    def _on_mode_change(self, idx):
        self.vfd.active_mode_icon = idx
        self.vfd.update()

    def _toggle_demo(self, checked):
        if checked:
            self.demo_btn.setText("⏹ Detener Demo")
            self.demo_timer.start(20)
        else:
            self.demo_btn.setText("▶ Señal Demo")
            self.demo_timer.stop()

    def _on_demo_tick(self):
        self.demo_phase += 0.07
        self.vfd.set_test_pattern(self.demo_phase)

    def toggle_mini_mode(self):
        self.is_mini_mode = not self.is_mini_mode
        if self.is_mini_mode:
            if not self.isFullScreen():
                self.saved_geometry = self.saveGeometry()
            self.ctrl_widget.setVisible(False)
            self.vfd.set_solo_spectrum(True)
            self.mini_btn.setChecked(True)
            self.mini_btn.setText("🗗 Restaurar")
            if not self.isFullScreen():
                self.resize(800, 260)
        else:
            self.ctrl_widget.setVisible(True)
            self.vfd.set_solo_spectrum(False)
            self.mini_btn.setChecked(False)
            self.mini_btn.setText("🗖 Solo Espectro")
            if self.saved_geometry is not None and not self.isFullScreen():
                self.restoreGeometry(self.saved_geometry)

    def toggle_always_on_top(self):
        self.is_always_on_top = not self.is_always_on_top
        pos = self.pos()
        size = self.size()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.is_always_on_top)
        self.show()
        self.move(pos)
        self.resize(size)
        self.on_top_btn.setChecked(self.is_always_on_top)
        self.on_top_btn.setText("📌 Fijado" if self.is_always_on_top else "📌 Fijar")

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_btn.setText("⛶ Pantalla Completa")
        else:
            self.showFullScreen()
            self.fullscreen_btn.setText("🗗 Ventana")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self.toggle_fullscreen()
            event.accept()
        elif event.key() in (Qt.Key.Key_M, Qt.Key.Key_Escape):
            if self.isFullScreen():
                self.showNormal()
                self.fullscreen_btn.setText("⛶ Pantalla Completa")
            elif self.is_mini_mode:
                self.toggle_mini_mode()
            elif event.key() == Qt.Key.Key_M:
                self.toggle_mini_mode()
            event.accept()
        elif event.key() == Qt.Key.Key_P:
            self.toggle_always_on_top()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_mini_mode()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def feed_audio_data(self, freqs, fft_mag, db_min=-75.0, db_max=0.0):
        """Método público para alimentar la pantalla desde el analizador principal."""
        self.vfd.set_levels_from_fft(freqs, fft_mag, db_min, db_max)

    def closeEvent(self, event):
        if self.sd_stream is not None:
            try:
                self.sd_stream.stop()
                self.sd_stream.close()
            except Exception:
                pass
        self.demo_timer.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = RetroVFDWindow()
    win.show()
    if win.audio_in_combo.currentData() == -1:
        win.demo_btn.setChecked(True)
    sys.exit(app.exec())
