// Playback: a growable queue of Float32 chunks, drained sample by sample.
// A 'flush' message empties it instantly -- that is how barge-in actually cuts audio.

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.current = null;
    this.pos = 0;
    this.wasPlaying = false;

    this.port.onmessage = (event) => {
      if (event.data === 'flush') {
        this.queue = [];
        this.current = null;
        this.pos = 0;
      } else {
        this.queue.push(event.data);
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;

    for (let i = 0; i < out.length; i++) {
      if (!this.current || this.pos >= this.current.length) {
        this.current = this.queue.shift() || null;
        this.pos = 0;
      }
      out[i] = this.current ? this.current[this.pos++] : 0;
    }

    // Tell the UI when speech starts and stops so the indicator matches the audio.
    const playing = this.current !== null || this.queue.length > 0;
    if (playing !== this.wasPlaying) {
      this.wasPlaying = playing;
      this.port.postMessage(playing ? 'speaking' : 'idle');
    }
    return true;
  }
}

registerProcessor('playback-processor', PlaybackProcessor);
