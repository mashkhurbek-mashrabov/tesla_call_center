// Mic capture: buffers mono Float32 and posts fixed-size frames to the main thread.
// ponytail: raw AudioWorklet, no audio library -- mono PCM16 is a 3-line conversion.

const FRAME = 2048; // ~128ms at 16kHz, small enough to keep latency low

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(FRAME);
    this.offset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.offset++] = channel[i];
      if (this.offset === FRAME) {
        this.port.postMessage(this.buffer.slice());
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
