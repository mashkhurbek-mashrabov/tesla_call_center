// Voice-only call client: mic -> PCM16 16kHz -> ws, ws -> PCM16 24kHz -> speakers.

const IN_RATE = 16000;   // Live API input format, fixed by the model
const OUT_RATE = 24000;  // Live API output format, fixed by the model

const button = document.getElementById('call-button');
const statusLine = document.getElementById('status');
const orb = document.getElementById('orb');

let agentName = 'Support';  // replaced from /config on load
let ws = null;
let micContext = null;
let playContext = null;
let playbackNode = null;
let stream = null;
let active = false;

function setStatus(text, state) {
  statusLine.textContent = text;
  orb.className = 'orb' + (state ? ' ' + state : '');
}

function floatToPCM16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function pcm16ToFloat(buffer) {
  const pcm = new Int16Array(buffer);
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 32768;
  return out;
}

async function startCall() {
  button.disabled = true;
  setStatus('Ulanmoqda…', 'connecting');

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: IN_RATE,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (err) {
    setStatus('Mikrofonga ruxsat berilmadi', 'error');
    button.disabled = false;
    return;
  }

  // Two contexts: the model's input and output sample rates differ, and letting the
  // browser resample is cheaper than resampling by hand.
  micContext = new AudioContext({ sampleRate: IN_RATE });
  playContext = new AudioContext({ sampleRate: OUT_RATE });
  await micContext.audioWorklet.addModule('/static/worklets/capture.js');
  await playContext.audioWorklet.addModule('/static/worklets/playback.js');

  playbackNode = new AudioWorkletNode(playContext, 'playback-processor');
  playbackNode.connect(playContext.destination);
  playbackNode.port.onmessage = (event) => {
    if (!active) return;
    setStatus(event.data === 'speaking' ? `${agentName} gapiryapti…` : 'Tinglayapman…',
              event.data === 'speaking' ? 'speaking' : 'listening');
  };

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/call`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    const source = micContext.createMediaStreamSource(stream);
    const capture = new AudioWorkletNode(micContext, 'capture-processor');
    capture.port.onmessage = (event) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(floatToPCM16(event.data).buffer);
      }
    };
    source.connect(capture);
    // Keep the graph alive without routing the mic to the speakers.
    capture.connect(micContext.destination);
  };

  ws.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      playbackNode.port.postMessage(pcm16ToFloat(event.data));
      return;
    }
    const frame = JSON.parse(event.data);
    if (frame.type === 'ready') {
      active = true;
      button.disabled = false;
      button.textContent = 'Tugatish';
      button.classList.add('active');
      setStatus('Ulandi — gapiring', 'listening');
    } else if (frame.type === 'interrupted') {
      playbackNode.port.postMessage('flush');
      setStatus('Tinglayapman…', 'listening');
    } else if (frame.type === 'error') {
      setStatus('Xatolik: ' + frame.message, 'error');
      endCall();
    }
  };

  ws.onclose = () => { if (active) endCall(); };
  ws.onerror = () => setStatus('Ulanishda xatolik', 'error');
}

function endCall() {
  active = false;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'hangup' }));
    ws.close();
  }
  ws = null;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  if (micContext) micContext.close();
  if (playContext) playContext.close();
  micContext = playContext = playbackNode = null;

  button.textContent = 'Qo\'ng\'iroq qilish';
  button.classList.remove('active');
  button.disabled = false;
  setStatus('Qo\'ng\'iroq tugadi', '');
}

button.addEventListener('click', () => (active ? endCall() : startCall()));

// Agent name lives in config.py; the UI asks for it rather than duplicating it.
fetch('/config')
  .then((r) => r.json())
  .then((cfg) => { if (cfg.agent_name) agentName = cfg.agent_name; })
  .catch(() => {});
