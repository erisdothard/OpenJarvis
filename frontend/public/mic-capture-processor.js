/**
 * AudioWorklet processor — captures mic audio, resamples to 16 kHz,
 * and emits 512-sample (1024-byte) 16-bit PCM chunks for Silero VAD.
 *
 * The worklet runs at the AudioContext's native sample rate (often 48 kHz)
 * and performs linear-interpolation downsampling to 16 kHz internally.
 */

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(4096);
    this._bufferLen = 0;
    this._inputRate = sampleRate; // AudioContext.sampleRate (e.g. 48000)
    this._outputRate = 16000;
    this._chunkSize = 512; // Silero VAD expects 512 samples at 16 kHz
    this._srcPos = 0; // fractional position tracker for resampling
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel || channel.length === 0) return true;

    if (this._inputRate === this._outputRate) {
      // No resampling needed — direct copy
      this._ensureCapacity(this._bufferLen + channel.length);
      this._buffer.set(channel, this._bufferLen);
      this._bufferLen += channel.length;
    } else {
      // Linear interpolation downsampling
      const step = this._inputRate / this._outputRate;
      const maxOut = Math.ceil(channel.length / step) + 1;
      this._ensureCapacity(this._bufferLen + maxOut);

      while (this._srcPos < channel.length - 1) {
        const idx = Math.floor(this._srcPos);
        const frac = this._srcPos - idx;
        const next = Math.min(idx + 1, channel.length - 1);
        this._buffer[this._bufferLen++] = channel[idx] * (1 - frac) + channel[next] * frac;
        this._srcPos += step;
      }
      // Carry fractional position to next call
      this._srcPos -= channel.length;
      if (this._srcPos < 0) this._srcPos = 0;
    }

    // Emit full 512-sample chunks as 16-bit PCM
    while (this._bufferLen >= this._chunkSize) {
      const pcm = new Int16Array(this._chunkSize);
      for (let i = 0; i < this._chunkSize; i++) {
        const s = Math.max(-1, Math.min(1, this._buffer[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      // Shift remaining samples to front
      this._buffer.copyWithin(0, this._chunkSize, this._bufferLen);
      this._bufferLen -= this._chunkSize;
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }

    return true;
  }

  _ensureCapacity(needed) {
    if (needed <= this._buffer.length) return;
    const next = new Float32Array(Math.max(needed, this._buffer.length * 2));
    next.set(this._buffer.subarray(0, this._bufferLen));
    this._buffer = next;
  }
}

registerProcessor('mic-capture-processor', MicCaptureProcessor);
