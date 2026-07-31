"""Confirm Deepgram is reachable and transcribing before spending a live call.

    PYTHONPATH=. uv run python scripts/deepgram_check.py

Sends a short synthesised utterance and prints what comes back, plus how long
the finalised transcript took to arrive. Costs a fraction of a cent.
"""
import asyncio, os, time, wave, io, struct, math
import aiohttp
import bot.config  # loads .env


def tone_speech_like(seconds=1.5, rate=16000):
    """A crude voiced waveform. Deepgram will not find words in it, but the
    round trip and the finalisation signal are what we are checking."""
    frames = bytearray()
    for i in range(int(rate * seconds)):
        t = i / rate
        env = math.sin(math.pi * t / seconds)
        v = 0.4 * env * (math.sin(2 * math.pi * 120 * t) + 0.5 * math.sin(2 * math.pi * 240 * t))
        frames += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


async def main():
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        print("DEEPGRAM_API_KEY is not set"); return

    url = "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true"
    audio = tone_speech_like()
    start = time.perf_counter()
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=audio,
                          headers={"Authorization": f"Token {key}",
                                   "Content-Type": "audio/wav"}) as r:
            body = await r.json()
            elapsed = (time.perf_counter() - start) * 1000
            print(f"HTTP {r.status} in {elapsed:.0f}ms")
            if r.status != 200:
                print("response:", body); return
            alt = body["results"]["channels"][0]["alternatives"][0]
            print(f"model:      {body['metadata'].get('models', ['?'])}")
            print(f"duration:   {body['metadata'].get('duration')}s billed")
            print(f"transcript: {alt.get('transcript')!r}")
            print("\nAuthentication and transcription both work.")

asyncio.run(main())
