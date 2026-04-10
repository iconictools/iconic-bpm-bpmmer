# iconic-bpm-bpmmer
find the bpm in unstable/bpm shifting recordings and possibly normalize it

## Ultimate BPM Finder

A full-stack web application for precise tempo and time-signature analysis of any audio file.

### Features
- **High-precision BPM detection** — decimal BPM (e.g. `120.4537219…`) via Fourier tempogram with parabolic sub-bin interpolation and cross-validation
- **Tempo change tracking** — sliding-window analysis detects accelerando/ritardando events with precise start/end times
- **Time signature detection** — per-segment meter detection (2/4, 3/4, 4/4, 5/4, 6/8, 7/8 …) with change tracking
- **Song section analysis** — MFCC + chroma feature clustering labels Intro, Verse, Pre-chorus, Chorus, Bridge, Outro, etc.
- **Visual timeline** — interactive waveform with beat grid overlay, BPM-over-time chart, section colour bands, time-signature map
- **DAW export** — MIDI tempo map (FL Studio, Ableton Live, Logic, Reaper …), Ableton `.asd` warp file, FL Studio `.txt` tempo map, CSV, JSON, beat markers
- **BPM normalisation** — time-stretch audio to a perfectly stable BPM, export as WAV, FLAC, or MP3 320 kbps
- **Wide format support** — WAV, FLAC, MP3, AIFF, OGG, M4A, Opus, and more (via librosa + ffmpeg)

---

### Quick Start (auto-build)

**One-click launchers**
- **Windows**: double-click `start.bat` (native Windows launcher; auto-creates `.venv`, installs Python dependencies, and opens the app)
- **macOS**: double-click `start.command` (if needed once: `chmod +x start.command`)

**Option 1 — `run.sh` (Linux / macOS)**
```bash
chmod +x run.sh
./run.sh
```
Opens `http://localhost:5000` in your browser. The script creates a virtual environment, installs all dependencies, and starts the server automatically.

**Option 2 — `make`**
```bash
make          # install + run (default)
make install  # install deps only
make run      # run server (after install)
make run PORT=8080  # custom port
make clean    # remove venv and outputs
```

**Option 3 — manual**
```bash
pip install -r requirements.txt
python app.py [--port 5000] [--host 0.0.0.0]
```

> **ffmpeg** is required for MP3 export.
> Install via `sudo apt install ffmpeg` (Ubuntu/Debian) or `brew install ffmpeg` (macOS).

---

### Usage
1. Open the web UI and drag & drop (or browse for) an audio file.
2. Set the BPM range and whether to analyse song sections, then click **Analyse**.
3. View results: global BPM, time signature, beat grid, BPM-over-time chart, section map, and change tables.
4. Export in your preferred format (MIDI, CSV, JSON, etc.) from the Export panel.
5. Optionally normalise the audio to a stable target BPM and download the result.

---

### Project Structure
```
app.py                  Flask web server
analyzer/
  bpm_detector.py       High-precision BPM engine (Fourier + autocorrelation tempogram)
  time_signature.py     Meter detection and change tracking
  section_detector.py   Song structure analysis (MFCC + clustering)
  normalizer.py         BPM normalisation (time-stretch + export)
  exporter.py           DAW export formats (MIDI, CSV, JSON, ASD, TXT)
static/
  css/style.css         Dark-theme UI styles
  js/main.js            Frontend controller
  js/visualizer.js      Canvas visualisations (sections, time-sig, beat grid)
  lib/                  Bundled WaveSurfer.js + Chart.js (no CDN required)
templates/index.html    Single-page application template
requirements.txt        Python dependencies
Makefile                Build targets (install / run / clean / test)
run.sh                  One-shot auto-build & run script
```
