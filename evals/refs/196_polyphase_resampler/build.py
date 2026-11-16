#!/usr/bin/env python3
"""Regenerate evals/tasks/196_polyphase_resampler.json from the sources here.

  ref.py             reference solution (dest: /tmp/eval_resamp196/resample.py)
  runner.py          subprocess that imports the submission and runs the cases
  validator_main.py  validator body: lint, seeded cases, FFT/fit measurements
  prove.json         prover arms (ref, wrong, leak, slow) with failure classes

The case generator and every measurement live only inside the validator
script; the agent-visible fixture dir holds the sample and the submission.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(REPO, "evals", "tasks", "196_polyphase_resampler.json")


def read(n):
    with open(os.path.join(HERE, n)) as f:
        return f.read()


TASK = """\
Create /tmp/eval_resamp196/resample.py (Python 3.12, standard library only): a sample-rate converter from 44100 Hz to 48000 Hz for real mono signals.

Signal model. An input x of N samples (a list of floats, |x[n]| <= 1, 0 <= N <= 200000) has sample n at time n/44100 s; x is 0 outside 0..N-1. The output y has sample m at time m/48000 s, for every m >= 0 whose time is strictly before N/44100 s, so len(y) is exactly the number of such m. y must be time-aligned with x (zero latency): y[m] estimates the band-limited signal at time m/48000 s itself, not at a delayed time. Each y[m] may depend only on input samples whose time is within 64 input-sample periods (64/44100 s) of m/48000 s, on the past side as well as the future side. This is checked: changing input samples outside that window of m must leave y[m] unchanged (to 1e-9).

API (both required):
- resample(x: list[float]) -> list[float]: the whole signal at once.
- class Resampler with Resampler() (no arguments), process(chunk: list[float]) -> list[float] and flush() -> list[float]. Feeding x as consecutive chunks of any sizes (empty chunks included) and concatenating every process() result followed by the flush() result must give resample(x) to within 1e-9 per sample. Streaming may hold outputs back, but once process() calls have delivered n samples in total, every output m with 147*m/160 + 64 < n (its whole dependency window delivered) must already have been returned. One Resampler instance converts one stream; flush() is called once, at the end.

Quality, judged on interior outputs (at least 2 ms from either end of the signal). For any input tone x[n] = A*sin(2*pi*f*n/44100 + phi) with 0 <= f <= 20000 Hz, 0.1 <= A <= 1 (for f = 0, x[n] = A), with t = m/48000 s:
- passband: the output's component at f has amplitude within +/-0.1 dB of A and phase within 2*pi*f*1e-6 rad of phi (timing error at most 1 microsecond);
- stopband: every other spectral component of the output, at a frequency more than 100 Hz away from f, is at least 80 dB below that tone. The validator measures this with a Kaiser-window (beta = 14) FFT over 8192 consecutive interior output samples;
- sample accuracy: every interior y[m] is within A*(0.0136 + 2*pi*f*1e-6) of A*sin(2*pi*f*t + phi) (of A for f = 0).

Sums of tones (what superposition gives any linear resampler that meets the single-tone rules): for x[n] = sum_i A_i*sin(2*pi*f_i*n/44100 + phi_i) with 20 <= f_i <= 20000 Hz and sum_i A_i <= 1, every interior y[m] is within sum_i A_i*(0.0136 + 2*pi*f_i*1e-6) of sum_i A_i*sin(2*pi*f_i*t + phi_i). Hidden cases check this on one-shot and streamed outputs alike.

Speed: all hidden cases together (about 300,000 output samples in all) must finish within 8 s of CPU time, counted over the solution process and anything it starts (the budget scales with the harness's contention factor). One of them is a 2-second signal made of back-to-back tone segments (each segment a fresh tone of the form above, starting at its own segment boundary); every one of its outputs at least 2 ms from a segment boundary is held to all three quality criteria for its segment's tone.

Allowed imports: math, cmath, array, itertools, functools, operator, bisect, collections, typing, dataclasses, fractions, decimal, abc, enum, numbers, copy, heapq. No other module (no numpy/scipy), and no exec/eval/compile/__import__/open; this is also enforced at run time (loading any other module, or opening files, makes the solution process fail).

/tmp/eval_resamp196/sample.txt lists a few input/output lengths. Validation runs about 49 hidden, seeded cases (lengths, tones and sums of tones across the passband, two locality probes, streaming with random chunk sizes on tone content, streams stopped part-way, a speed case), spread over several separate solution processes, and reports only the index and kind of the first failing case. Write your own tests.
"""

SAMPLE = (
    "# len(x) -> len(resample(x))\n"
    "0 -> 0\n"
    "1 -> 2\n"
    "147 -> 160\n"
    "44100 -> 48000\n"
)

validator = "RUNNER = " + repr(read("runner.py")) + "\n" + read("validator_main.py")

task = {
    "id": "196_polyphase_resampler",
    "name": "Polyphase resampler 44.1k -> 48k with >= 80 dB stopband",
    "difficulty": "hard",
    "category": "Signal Processing & DSP",
    "subcategory": "Multirate / sample-rate conversion",
    "max_iter": 25,
    "est_s": 165,
    "max_wall_s": 900,
    "role": "scored",
    "language": "python",
    "ref_stem": "196_polyphase_resampler/ref",
    "dest": "/tmp/eval_resamp196/resample.py",
    "setup": ("rm -rf /tmp/eval_resamp196 && mkdir -p /tmp/eval_resamp196 && "
              "cat > /tmp/eval_resamp196/sample.txt <<'EOF'\n" + SAMPLE + "EOF"),
    "task": TASK,
    "validation": {"type": "python", "script": validator},
    "cleanup": "rm -rf /tmp/eval_resamp196",
}

compile(validator, "validator", "exec")
with open(OUT, "w") as f:
    json.dump(task, f, indent=2)
    f.write("\n")
print("wrote", OUT)
