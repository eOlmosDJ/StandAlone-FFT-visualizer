# StandAlone FFT Visualiser & Audio Router

A high-performance real-time audio routing bridge and spectrum visualiser for Windows, built with Python, PyQt6, pyqtgraph, and PortAudio (sounddevice).

Designed for audio engineers, music producers, and enthusiasts desiring hardware-accurate frequency analysis, zero monitoring latency, and authentic vintage display aesthetics.

---

## Key Features

### 1. Real-Time Audio Engine & DSP
- **Zero-Latency Monitoring:** Audio callback executes on a high-priority C thread with a fixed, small block size (512 samples) for direct passthrough without latency.
- **Decoupled Ring Buffer FFT:** Configurable spectral resolution (512 to 16,384 samples) using a rolling circular buffer. Delivers pinpoint bass resolution without increasing audio monitoring latency.
- **Feedback Protection:** Application launches in **Muted** state by default to prevent accidental microphone-to-speaker acoustic feedback loops.
- **Asymmetric Envelope Detection:** Instantaneous attack coupled with adjustable decay factor for smooth peak tracking.

### 2. Studio-Grade Spectrum Display
- **Hardware-Accelerated 2D Rendering:** Sustained 60 FPS using `pyqtgraph`. Fixed-axis ViewBox with mouse pan/zoom locked to eliminate accidental graph displacement.
- **Exact 1-2-5 Logarithmic Scale:** Easing margin starting at 10 Hz (`log10(10.0) = 1.0`), extending to Nyquist with 1-2-5 major division ticks (`20, 50, 100, 200, 500, 1k, 2k, 5k, 10k, 20k`) and sub-decade minor tick marks.
- **"Elastic Band" Low-End Anchoring:** The frequency curve smoothly anchors at 10 Hz on the dynamic dB floor, naturally flowing into actual FFT energy bins via cubic splines (`scipy.interpolate.make_interp_spline`).
- **Dynamic dB Range & Area Fill:** Adjustable min/max dB limits with semi-transparent real-time area filling underneath the curve.
- **Live Theme Customiser:** Non-modal colour settings panel allowing real-time editing of curve, fill, background, grid, and axis text colours with preset persistence in `config.json`.

### 3. Pioneer DEQ-7600 Retro VFD Display Mode
- **Authentic Vacuum Fluorescent Display:** Recreates the iconic visual signature of the Pioneer DEQ-7600 digital signal processor.
- **Accurate "V" Segment Subdivision:** 15 ISO 2/3-octave frequency bands with hardware-accurate row subdivisions:
  `[4, 4, 3, 3, 2, 2, 1, 1, 1, 2, 2, 3, 3, 4, 4]`
  - 31.5 & 50 Hz: 4 rectangular phosphor blocks per row
  - 80 & 125 Hz: 3 blocks per row
  - 200 & 315 Hz: 2 blocks per row
  - 500, 800 & 1.3 kHz: 1 continuous solid wide bar per row
  - 2k & 3.2 kHz: 2 blocks per row
  - 5k & 8 kHz: 3 blocks per row
  - 12.5k & 20 kHz: 4 rectangular phosphor blocks per row
- **Dynamic Motion:** No static peak hold bars—columns bounce dynamically with instant attack and smooth decay.
- **Phosphor Persistence & Neon Bloom:** Multilayer bloom with white-hot cores, cyan phosphor bodies, unlit background matrix grids, and ~100 ms afterglow on de-excitation.
- **Modular & Standalone:** Isolated in `retro_vfd.py`; can run autonomously with its own device selector and test signal generator.

### 4. Mini Mode, Pin to Top & Fullscreen
- **Mini Mode (Spectrum Only):** Hides all routing panels and sound controls. In Retro VFD mode, side icons and auxiliary functions are hidden, stretching the 15 glowing columns across the entire display.
- **Always on Top (Pin):** Keeps the visualiser floating over any active game, DAW (Ableton, FL Studio, Pro Tools), or browser window.
- **Fullscreen (F11):** Expands the visualiser to cover the entire monitor.
- **Quick Shortcuts:**
  - `Double-click` canvas: Toggle Mini Mode
  - `Right-click` canvas: Context menu for modes, pin, mute, and colours
  - `M` or `Esc`: Toggle Mini Mode
  - `P`: Toggle Pin (Always on Top)
  - `F11`: Toggle Fullscreen

---

## Installation & Running

### Requirements
- Windows 10 / 11
- Python 3.10+

### Setup
```bash
git clone https://github.com/eOlmosDJ/StandAlone-FFT-visualizer.git
cd StandAlone-FFT-visualizer
pip install -r requirements.txt
```

### Launch
```bash
python main.py
```

Or run the retro display standalone:
```bash
python retro_vfd.py
```

### Building Standalone Executable (.exe)
Run the automated build script:
```cmd
build.bat
```
The packaged single-file binary will be generated in `dist\alpha8.exe`.
