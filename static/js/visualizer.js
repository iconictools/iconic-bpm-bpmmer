/**
 * visualizer.js – Canvas-based helpers for sections timeline and time-signature band.
 */

/* ── Section palette (per label) ───────────────────────────── */
const SECTION_COLORS = {
  intro:      '#5c8fff',
  verse:      '#3dd68c',
  'pre-chorus':'#f0c050',
  chorus:     '#f05d9e',
  bridge:     '#b57bfc',
  drop:       '#ff6b35',
  break:      '#52d9f5',
  outro:      '#8890a8',
  'full track':'#5c8fff',
  section:    '#5c8fff',
};

function sectionColor(label) {
  return SECTION_COLORS[label] || '#aaaaaa';
}

/**
 * Draw section band on a canvas.
 * @param {HTMLCanvasElement} canvas
 * @param {Array} sections  – [{start_time, end_time, label, section_id}]
 * @param {number} duration – total audio duration (seconds)
 */
function drawSections(canvas, sections, duration) {
  if (!sections || sections.length === 0) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.offsetWidth || canvas.parentElement.clientWidth || 800;
  const H = canvas.height;
  canvas.width = W;

  ctx.clearRect(0, 0, W, H);

  const toX = t => (t / duration) * W;

  sections.forEach(sec => {
    const x1 = toX(sec.start_time);
    const x2 = toX(sec.end_time);
    const w  = Math.max(1, x2 - x1);
    const color = sectionColor(sec.label);

    // Fill
    ctx.fillStyle = color + '44';
    ctx.fillRect(x1, 0, w, H);

    // Border
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x1 + 0.5, 0.5, w - 1, H - 1);

    // Label
    if (w > 28) {
      ctx.fillStyle = color;
      ctx.font = `bold 10px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const label = (sec.section_id || sec.label || '').toUpperCase();
      ctx.fillText(label, x1 + w / 2, H / 2, w - 4);
    }
  });
}

/**
 * Build the sections legend DOM.
 * @param {HTMLElement} container
 * @param {Array} sections
 */
function buildSectionsLegend(container, sections) {
  container.innerHTML = '';
  const seen = new Set();
  sections.forEach(sec => {
    const key = sec.label;
    if (seen.has(key)) return;
    seen.add(key);
    const item = document.createElement('div');
    item.className = 'legend-item';

    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.background = sectionColor(key);

    const label = document.createElement('span');
    label.textContent = String(key);

    item.appendChild(swatch);
    item.appendChild(label);
    container.appendChild(item);
  });
}

/**
 * Draw time-signature band.
 * @param {HTMLCanvasElement} canvas
 * @param {Array} segments  – [{start_time, end_time, signature}]
 * @param {number} duration
 */
function drawTimeSignatureBand(canvas, segments, duration) {
  if (!segments || segments.length === 0) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.offsetWidth || canvas.parentElement.clientWidth || 800;
  const H = canvas.height;
  canvas.width = W;
  ctx.clearRect(0, 0, W, H);

  const palette = ['#7c5cfc', '#3dd68c', '#f5a623', '#52d9f5', '#f05d5d', '#b57bfc'];
  const sigIndex = {};
  let colorIdx = 0;

  const toX = t => (t / duration) * W;

  // Build consolidated (non-overlapping) segments
  const cons = consolidateSegments(segments);

  cons.forEach(seg => {
    if (!(seg.signature in sigIndex)) {
      sigIndex[seg.signature] = colorIdx++ % palette.length;
    }
    const color = palette[sigIndex[seg.signature]];
    const x1 = toX(seg.start_time);
    const x2 = toX(seg.end_time);
    const w  = Math.max(1, x2 - x1);

    ctx.fillStyle = color + '55';
    ctx.fillRect(x1, 0, w, H);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.strokeRect(x1, 0, w, H);

    if (w > 20) {
      ctx.fillStyle = color;
      ctx.font = `bold 10px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(seg.signature, x1 + w / 2, H / 2, w - 4);
    }
  });
}

/**
 * Collapse overlapping/adjacent time-signature segments into non-overlapping spans.
 */
function consolidateSegments(segs) {
  if (!segs || segs.length === 0) return [];
  const sorted = [...segs].sort((a, b) => a.start_time - b.start_time);
  const result = [];
  let cur = { ...sorted[0] };
  for (let i = 1; i < sorted.length; i++) {
    const s = sorted[i];
    if (s.signature === cur.signature && s.start_time <= cur.end_time + 0.5) {
      cur.end_time = Math.max(cur.end_time, s.end_time);
    } else {
      result.push(cur);
      cur = { ...s };
    }
  }
  result.push(cur);
  return result;
}

/**
 * Build the BPM over-time Chart.js chart.
 * @param {HTMLCanvasElement} canvas
 * @param {Array} tempoMap   – [{time, bpm}]
 * @param {Array} changes    – [{time, from_bpm, to_bpm}]
 * @param {number} globalBpm
 */
function buildBpmChart(canvas, tempoMap, changes, globalBpm) {
  if (!tempoMap || tempoMap.length === 0) return;

  const labels = tempoMap.map(e => e.time.toFixed(2));
  const data   = tempoMap.map(e => parseFloat(e.bpm.toFixed(6)));

  // Change annotations (vertical lines)
  const changeAnnotations = {};
  (changes || []).forEach((ch, i) => {
    changeAnnotations[`change_${i}`] = {
      type: 'line',
      xMin: ch.time.toFixed(2),
      xMax: ch.time.toFixed(2),
      borderColor: '#f05d5d',
      borderWidth: 1,
      borderDash: [4, 3],
      label: {
        display: true,
        content: `${ch.from_bpm.toFixed(1)}→${ch.to_bpm.toFixed(1)}`,
        color: '#f05d5d',
        font: { size: 9 },
      },
    };
  });

  // Reference BPM line
  changeAnnotations['ref_bpm'] = {
    type: 'line',
    yMin: globalBpm,
    yMax: globalBpm,
    borderColor: 'rgba(255,255,255,0.2)',
    borderWidth: 1,
    borderDash: [6, 4],
  };

  const existingChart = Chart.getChart(canvas);
  if (existingChart) existingChart.destroy();

  new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'BPM',
        data,
        borderColor: '#7c5cfc',
        backgroundColor: 'rgba(124,92,252,0.12)',
        fill: true,
        tension: 0.3,
        pointRadius: tempoMap.length > 200 ? 0 : 2,
        pointHoverRadius: 4,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: ctx => `Time: ${ctx[0].label}s`,
            label: ctx => `BPM: ${ctx.raw.toFixed(6)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#8890a8', maxTicksLimit: 12, font: { size: 10 } },
          title: { display: true, text: 'Time (s)', color: '#8890a8', font: { size: 10 } },
        },
        y: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#8890a8', font: { size: 10 } },
          title: { display: true, text: 'BPM', color: '#8890a8', font: { size: 10 } },
        },
      },
    },
  });
}

/**
 * Draw beat lines on the overlay div over the waveform.
 * @param {HTMLElement} overlay    – the #beat-overlay div
 * @param {Array}  beats           – timestamps in seconds
 * @param {Array}  downbeats       – timestamps in seconds
 * @param {number} duration        – total duration
 * @param {boolean} showBeats
 * @param {boolean} showDownbeats
 */
function drawBeatOverlay(overlay, beats, downbeats, duration, showBeats, showDownbeats) {
  overlay.innerHTML = '';
  if (!duration) return;

  const dbSet = new Set((downbeats || []).map(d => d.toFixed(4)));

  (beats || []).forEach(b => {
    const pct = (b / duration) * 100;
    const line = document.createElement('div');
    const isDb = dbSet.has(b.toFixed(4));
    line.style.cssText = `
      position:absolute; top:0; bottom:0;
      left:${pct}%;
      width:${isDb ? 2 : 1}px;
      background:${isDb ? 'rgba(255,200,60,0.75)' : 'rgba(124,92,252,0.4)'};
      pointer-events:none;
      display:${isDb ? (showDownbeats ? 'block' : 'none') : (showBeats ? 'block' : 'none')};
    `;
    overlay.appendChild(line);
  });
}

/**
 * Format seconds → M:SS.mmm
 */
function formatTime(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(3);
  return `${m}:${sec.padStart(6, '0')}`;
}
