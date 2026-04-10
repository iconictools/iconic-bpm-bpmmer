/**
 * main.js – Application controller
 */

'use strict';

// ── State ────────────────────────────────────────────────────────
let currentJobId    = null;
let analysisResult  = null;
let waveSurfer      = null;
let audioObjectUrl  = null;
let selectedFile    = null;

// ── DOM ──────────────────────────────────────────────────────────
const fileInput          = document.getElementById('file-input');
const dropZone           = document.getElementById('drop-zone');
const uploadPrompt       = document.getElementById('upload-prompt');
const uploadSelected     = document.getElementById('upload-selected');
const selectedFilename   = document.getElementById('selected-filename');
const clearFileBtn       = document.getElementById('clear-file-btn');
const analyzeBtn         = document.getElementById('analyze-btn');

const progressPanel      = document.getElementById('progress-panel');
const progressBar        = document.getElementById('progress-bar');
const progressMsg        = document.getElementById('progress-msg');

const resultsPanel       = document.getElementById('results-panel');
const exportPanel        = document.getElementById('export-panel');
const normalizePanel     = document.getElementById('normalize-panel');

// Stat cards
const statBpm            = document.getElementById('stat-bpm');
const statBpmPrecise     = document.getElementById('stat-bpm-precise');
const statTs             = document.getElementById('stat-ts');
const statTsConf         = document.getElementById('stat-ts-conf');
const statDur            = document.getElementById('stat-dur');
const statBeats          = document.getElementById('stat-beats');
const statChanges        = document.getElementById('stat-changes');
const statSections       = document.getElementById('stat-sections');
const statConf           = document.getElementById('stat-conf');
const statSr             = document.getElementById('stat-sr');

const playBtn            = document.getElementById('play-btn');
const playheadTime       = document.getElementById('playhead-time');
const volumeSlider       = document.getElementById('volume-slider');
const showBeatsToggle    = document.getElementById('show-beats-toggle');
const showDownbeatsToggle= document.getElementById('show-downbeats-toggle');

const bpmChart           = document.getElementById('bpm-chart');
const sectionsCanvas     = document.getElementById('sections-canvas');
const sectionsLegend     = document.getElementById('sections-legend');
const tsCanvas           = document.getElementById('ts-canvas');
const beatOverlay        = document.getElementById('beat-overlay');

const changesTbody       = document.getElementById('changes-tbody');
const tsCgsTbody         = document.getElementById('ts-changes-tbody');
const sectionsTbody      = document.getElementById('sections-tbody');
const beatStats          = document.getElementById('beat-stats');

const useGlobalBpmBtn    = document.getElementById('use-global-bpm');
const targetBpmInput     = document.getElementById('target-bpm');
const normFormatSel      = document.getElementById('norm-format');
const normalizeBtn       = document.getElementById('normalize-btn');
const normProgressWrap   = document.getElementById('norm-progress-wrap');
const normProgressBar    = document.getElementById('norm-progress-bar');
const normProgressMsg    = document.getElementById('norm-progress-msg');
const normDownloadWrap   = document.getElementById('norm-download-wrap');
const normDownloadLink   = document.getElementById('norm-download-link');

// ── File Handling ────────────────────────────────────────────────
function setFile(file) {
  if (!file) return;
  selectedFile = file;
  selectedFilename.textContent = file.name;
  uploadPrompt.hidden = true;
  uploadSelected.hidden = false;
  analyzeBtn.disabled = false;

  if (audioObjectUrl) URL.revokeObjectURL(audioObjectUrl);
  audioObjectUrl = URL.createObjectURL(file);
}

function clearFile() {
  selectedFile = null;
  uploadPrompt.hidden = false;
  uploadSelected.hidden = true;
  analyzeBtn.disabled = true;
  fileInput.value = '';
  if (audioObjectUrl) { URL.revokeObjectURL(audioObjectUrl); audioObjectUrl = null; }
}

fileInput.addEventListener('change', e => {
  if (e.target.files[0]) setFile(e.target.files[0]);
});
clearFileBtn.addEventListener('click', e => { e.stopPropagation(); clearFile(); });
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

// ── Analyze ──────────────────────────────────────────────────────
analyzeBtn.addEventListener('click', startAnalysis);

async function startAnalysis() {
  if (!selectedFile) return;

  analyzeBtn.disabled = true;
  progressPanel.hidden = false;
  resultsPanel.hidden  = true;
  exportPanel.hidden   = true;
  normalizePanel.hidden= true;
  setProgress(1, 'Uploading …');

  const form = new FormData();
  form.append('file', selectedFile);
  form.append('sections', document.getElementById('opt-sections').checked ? 'true' : 'false');
  form.append('min_bpm', document.getElementById('opt-min-bpm').value);
  form.append('max_bpm', document.getElementById('opt-max-bpm').value);

  let jobId;
  try {
    const res  = await fetch('/api/analyze', { method: 'POST', body: form });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    jobId = data.job_id;
  } catch (err) {
    setProgress(-1, `Upload failed: ${err.message}`);
    analyzeBtn.disabled = false;
    return;
  }

  currentJobId = jobId;
  listenProgress(jobId);
}

function listenProgress(jobId) {
  const es = new EventSource(`/api/progress/${jobId}`);
  es.onmessage = e => {
    const ev = JSON.parse(e.data);

    if (ev.pct === -2) return; // ping

    if (ev.pct === -1 || ev.error) {
      setProgress(0, `Error: ${ev.msg || ev.error}`);
      es.close();
      analyzeBtn.disabled = false;
      return;
    }

    setProgress(ev.pct, ev.msg);

    if (ev.pct === 100 && ev.result) {
      es.close();
      analysisResult = ev.result;
      renderResults(ev.result);
      analyzeBtn.disabled = false;
    }
  };
  es.onerror = () => {
    es.close();
    // Fallback: poll
    pollResult(jobId);
  };
}

async function pollResult(jobId) {
  for (let i = 0; i < 120; i++) {
    await sleep(3000);
    try {
      const res  = await fetch(`/api/result/${jobId}`);
      const data = await res.json();
      if (data.status === 'done' || data.analysis_complete) {
        analysisResult = data;
        renderResults(data);
        analyzeBtn.disabled = false;
        return;
      }
      if (data.error) {
        setProgress(0, `Error: ${data.error}`);
        analyzeBtn.disabled = false;
        return;
      }
    } catch (_) {}
  }
  setProgress(0, 'Timeout waiting for result.');
  analyzeBtn.disabled = false;
}

function setProgress(pct, msg) {
  progressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  progressMsg.textContent = msg;
}

// ── Render Results ───────────────────────────────────────────────
function renderResults(data) {
  progressPanel.hidden = false;
  setProgress(100, 'Analysis complete ✓');

  const bpm = data.bpm || {};
  const ts  = data.time_signature || {};
  const sec = data.sections || {};

  // ── Stats ──
  const globalBpm = bpm.global_bpm || 0;
  statBpm.textContent       = globalBpm.toFixed(3);
  statBpmPrecise.textContent= bpm.global_bpm_str || '';
  statTs.textContent        = ts.global_signature || '—';
  statTsConf.textContent    = ts.global_confidence != null
    ? `Confidence: ${(ts.global_confidence * 100).toFixed(0)}%` : '';
  statDur.textContent       = formatTime(bpm.duration || 0);
  statBeats.textContent     = (bpm.beats || []).length.toLocaleString();
  statChanges.textContent   = (bpm.tempo_changes || []).length;
  statSections.textContent  = sec.n_sections || 0;
  statConf.textContent      = bpm.confidence != null
    ? `${(bpm.confidence * 100).toFixed(0)}%` : '—';
  statSr.textContent        = bpm.analysis_sr ? `${bpm.analysis_sr} Hz` : '—';

  // ── Waveform ──
  initWaveform(bpm, audioObjectUrl);

  // ── Beat overlay ──
  drawBeatOverlay(
    beatOverlay,
    bpm.beats, bpm.downbeats, bpm.duration,
    showBeatsToggle.checked, showDownbeatsToggle.checked
  );

  // ── BPM chart ──
  buildBpmChart(bpmChart, bpm.tempo_map, bpm.tempo_changes, globalBpm);

  // ── Sections ──
  if (sec.sections && sec.sections.length > 0) {
    document.getElementById('sections-viz-wrap').style.display = '';
    drawSections(sectionsCanvas, sec.sections, bpm.duration || 1);
    buildSectionsLegend(sectionsLegend, sec.sections);
    renderSectionsTable(sec.sections);
  } else {
    document.getElementById('sections-viz-wrap').style.display = 'none';
  }

  // ── Time signature band ──
  if (ts.segments && ts.segments.length > 0) {
    document.getElementById('ts-viz-wrap').style.display = '';
    drawTimeSignatureBand(tsCanvas, ts.segments, bpm.duration || 1);
  } else {
    document.getElementById('ts-viz-wrap').style.display = 'none';
  }

  // ── Tempo changes table ──
  renderChangesTable(bpm.tempo_changes || []);

  // ── TS changes table ──
  renderTsChangesTable(ts.changes || []);

  // ── Beat stats ──
  renderBeatStats(bpm.beat_intervals || []);

  // ── Set default target BPM ──
  targetBpmInput.value = globalBpm.toFixed(3);

  // ── Show panels ──
  resultsPanel.hidden  = false;
  exportPanel.hidden   = false;
  normalizePanel.hidden= false;
  resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── WaveSurfer ───────────────────────────────────────────────────
function initWaveform(bpm, objectUrl) {
  if (waveSurfer) { waveSurfer.destroy(); waveSurfer = null; }
  if (!objectUrl) return;

  waveSurfer = WaveSurfer.create({
    container:       '#waveform',
    waveColor:       '#4e57b0',
    progressColor:   '#7c5cfc',
    cursorColor:     '#f5a623',
    height:          128,
    normalize:       true,
    barWidth:        2,
    barGap:          1,
    barRadius:       2,
    backend:         'WebAudio',
    interact:        true,
  });

  waveSurfer.load(objectUrl);

  waveSurfer.on('timeupdate', t => {
    playheadTime.textContent = formatTime(t);
  });

  waveSurfer.on('finish', () => { playBtn.textContent = '▶'; });

  // Redraw beat overlay when waveform is ready (size may change)
  waveSurfer.once('ready', () => {
    if (bpm.beats) {
      drawBeatOverlay(
        beatOverlay, bpm.beats, bpm.downbeats, bpm.duration,
        showBeatsToggle.checked, showDownbeatsToggle.checked
      );
    }
  });

  volumeSlider.addEventListener('input', () => {
    if (waveSurfer) waveSurfer.setVolume(parseFloat(volumeSlider.value));
  });
}

playBtn.addEventListener('click', () => {
  if (!waveSurfer) return;
  waveSurfer.playPause();
  playBtn.textContent = waveSurfer.isPlaying() ? '⏸' : '▶';
});

showBeatsToggle.addEventListener('change', updateBeatOverlay);
showDownbeatsToggle.addEventListener('change', updateBeatOverlay);
function updateBeatOverlay() {
  if (!analysisResult) return;
  const bpm = analysisResult.bpm || {};
  drawBeatOverlay(
    beatOverlay, bpm.beats, bpm.downbeats, bpm.duration,
    showBeatsToggle.checked, showDownbeatsToggle.checked
  );
}

// ── Tables ───────────────────────────────────────────────────────
function renderChangesTable(changes) {
  changesTbody.innerHTML = '';
  if (!changes.length) {
    document.getElementById('changes-section').style.display = 'none';
    return;
  }
  document.getElementById('changes-section').style.display = '';
  changes.forEach(ch => {
    const tr = document.createElement('tr');

    const tdTime     = document.createElement('td');
    const tdFrom     = document.createElement('td');
    const tdTo       = document.createElement('td');
    const tdDelta    = document.createElement('td');
    const tdType     = document.createElement('td');
    const tdTrans    = document.createElement('td');

    tdTime.textContent  = formatTime(ch.time);
    tdFrom.textContent  = Number(ch.from_bpm).toFixed(6);
    tdTo.textContent    = Number(ch.to_bpm).toFixed(6);
    const delta = Number(ch.delta_bpm);
    tdDelta.textContent = (delta > 0 ? '+' : '') + delta.toFixed(4);
    tdDelta.style.color = delta > 0 ? '#3dd68c' : '#f05d5d';

    const span = document.createElement('span');
    span.textContent = String(ch.type || '');
    span.className   = ch.type === 'accelerando' ? 'tag-acc' : 'tag-rit';
    tdType.appendChild(span);

    tdTrans.textContent = `${formatTime(ch.start_time)} → ${formatTime(ch.end_time)}`;

    tr.appendChild(tdTime); tr.appendChild(tdFrom); tr.appendChild(tdTo);
    tr.appendChild(tdDelta); tr.appendChild(tdType); tr.appendChild(tdTrans);
    changesTbody.appendChild(tr);
  });
}

function renderTsChangesTable(changes) {
  tsCgsTbody.innerHTML = '';
  if (!changes.length) {
    document.getElementById('ts-changes-section').style.display = 'none';
    return;
  }
  document.getElementById('ts-changes-section').style.display = '';
  changes.forEach(ch => {
    const tr = document.createElement('tr');
    const tdTime = document.createElement('td');
    const tdFrom = document.createElement('td');
    const tdTo   = document.createElement('td');
    tdTime.textContent = formatTime(ch.time);
    tdFrom.textContent = String(ch.from_signature || '');
    tdTo.textContent   = String(ch.to_signature   || '');
    tr.appendChild(tdTime); tr.appendChild(tdFrom); tr.appendChild(tdTo);
    tsCgsTbody.appendChild(tr);
  });
}

function renderSectionsTable(sections) {
  sectionsTbody.innerHTML = '';
  sections.forEach(sec => {
    const tr = document.createElement('tr');

    const tdId   = document.createElement('td');
    const tdLbl  = document.createElement('td');
    const tdSt   = document.createElement('td');
    const tdEn   = document.createElement('td');
    const tdDur  = document.createElement('td');
    const tdClus = document.createElement('td');
    const tdConf = document.createElement('td');

    tdId.textContent   = String(sec.section_id || '—');
    const span = document.createElement('span');
    span.textContent   = String(sec.label || '');
    span.style.color   = sectionColor(sec.label);
    tdLbl.appendChild(span);
    tdSt.textContent   = formatTime(sec.start_time);
    tdEn.textContent   = formatTime(sec.end_time);
    tdDur.textContent  = Number(sec.duration).toFixed(2) + 's';
    tdClus.textContent = sec.cluster != null ? String(sec.cluster) : '—';
    tdConf.textContent = ((sec.confidence || 0) * 100).toFixed(0) + '%';

    tr.appendChild(tdId); tr.appendChild(tdLbl); tr.appendChild(tdSt);
    tr.appendChild(tdEn); tr.appendChild(tdDur); tr.appendChild(tdClus);
    tr.appendChild(tdConf);
    sectionsTbody.appendChild(tr);
  });
}

function renderBeatStats(intervals) {
  beatStats.innerHTML = '';
  if (!intervals.length) return;
  const arr = intervals.map(Number);
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  const min  = Math.min(...arr);
  const max  = Math.max(...arr);
  const std  = Math.sqrt(arr.reduce((a, b) => a + (b - mean) ** 2, 0) / arr.length);
  const bpmFromMean = 60 / mean;
  const cv  = (std / mean) * 100;

  const stats = [
    { l: 'Median IBI',  v: `${(arr.sort((a,b)=>a-b)[Math.floor(arr.length/2)]*1000).toFixed(2)} ms` },
    { l: 'Mean IBI',    v: `${(mean * 1000).toFixed(2)} ms` },
    { l: 'Min IBI',     v: `${(min  * 1000).toFixed(2)} ms` },
    { l: 'Max IBI',     v: `${(max  * 1000).toFixed(2)} ms` },
    { l: 'Std Dev',     v: `${(std  * 1000).toFixed(2)} ms` },
    { l: 'BPM (mean)',  v: bpmFromMean.toFixed(6) },
    { l: 'Tempo stability', v: `${(100 - cv).toFixed(1)}%` },
    { l: 'Beat count',  v: (arr.length + 1).toString() },
  ];

  stats.forEach(s => {
    const d  = document.createElement('div');
    d.className = 'mini-stat';
    const ml = document.createElement('div');
    ml.className  = 'ml';
    ml.textContent = s.l;
    const mv = document.createElement('div');
    mv.className  = 'mv';
    mv.textContent = s.v;
    d.appendChild(ml);
    d.appendChild(mv);
    beatStats.appendChild(d);
  });
}

// ── Export ───────────────────────────────────────────────────────
document.querySelectorAll('.btn-export').forEach(btn => {
  btn.addEventListener('click', () => {
    if (!currentJobId) return;
    const fmt = btn.dataset.fmt;
    window.location.href = `/api/export/${currentJobId}/${fmt}`;
  });
});

// ── Normalize ────────────────────────────────────────────────────
useGlobalBpmBtn.addEventListener('click', () => {
  if (analysisResult && analysisResult.bpm) {
    targetBpmInput.value = analysisResult.bpm.global_bpm.toFixed(3);
  }
});

normalizeBtn.addEventListener('click', async () => {
  if (!currentJobId) return;
  const targetBpm = parseFloat(targetBpmInput.value);
  if (isNaN(targetBpm) || targetBpm < 10) {
    alert('Please enter a valid target BPM.');
    return;
  }
  const fmt = normFormatSel.value;

  normalizeBtn.disabled   = true;
  normProgressWrap.hidden = false;
  normDownloadWrap.hidden = true;
  setNormProgress(1, 'Starting normalisation …');

  try {
    const res  = await fetch('/api/normalize', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ job_id: currentJobId, target_bpm: targetBpm, format: fmt }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const normJobId = data.norm_job_id;
    listenNormProgress(normJobId);
  } catch (err) {
    setNormProgress(-1, `Error: ${err.message}`);
    normalizeBtn.disabled = false;
  }
});

function listenNormProgress(normJobId) {
  const es = new EventSource(`/api/progress/${normJobId}`);
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.pct === -2) return;
    if (ev.pct === -1 || ev.error) {
      setNormProgress(0, `Error: ${ev.msg || ev.error}`);
      normalizeBtn.disabled = false;
      es.close();
      return;
    }
    setNormProgress(ev.pct, ev.msg);
    if (ev.pct === 100 && ev.download_id) {
      es.close();
      normDownloadWrap.hidden = false;
      normDownloadLink.href   = `/api/download_norm/${ev.download_id}`;
      normalizeBtn.disabled   = false;
    }
  };
  es.onerror = () => es.close();
}

function setNormProgress(pct, msg) {
  normProgressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  normProgressMsg.textContent = msg;
}

// ── Helpers ──────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Re-expose sectionColor for table rendering (defined in visualizer.js)
// (already accessible as global)

// ── Resize handler ───────────────────────────────────────────────
window.addEventListener('resize', () => {
  if (!analysisResult) return;
  const bpm = analysisResult.bpm || {};
  const sec = analysisResult.sections || {};
  const ts  = analysisResult.time_signature || {};
  if (sec.sections && sec.sections.length > 0)
    drawSections(sectionsCanvas, sec.sections, bpm.duration || 1);
  if (ts.segments && ts.segments.length > 0)
    drawTimeSignatureBand(tsCanvas, ts.segments, bpm.duration || 1);
  drawBeatOverlay(
    beatOverlay, bpm.beats, bpm.downbeats, bpm.duration,
    showBeatsToggle.checked, showDownbeatsToggle.checked
  );
});
