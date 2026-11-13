# Local transcription comparison — November 13, 2026

Decision: retain explicit Whisper small for the current TownRecord candidate.
Scott requested testing both engines and using the better result. This decision
prioritizes the wording of the municipal record, while documenting the real cost.

Both engines ran serially on identical 120-second, 16-kHz mono WAV files on the
same Windows host (16 physical / 32 logical processors), CPU only, through
TextFlowKit 0.1.19. Models were downloaded before timing. Measurements include
process startup, transcription and output, but exclude download/install time.
Peak memory samples the process tree every 100 ms; it is not GPU memory.

| Clip | Whistle elapsed / peak MiB | Whisper small elapsed / peak MiB |
|---|---:|---:|
| Longmont Council, September 8, 10:00–12:00 | 7.235 s / 82.78 | 30.430 s / 1884.17 |
| Longmont Planning, September 16, 10:00–12:00 | 7.316 s / 84.58 | 28.859 s / 1896.00 |

Sources:
- https://www.youtube.com/watch?v=3qfQAkAAC9U&t=600
- https://www.youtube.com/watch?v=skwd58zm6jk&t=600

Whistle was about four times faster and used about 23 times less peak memory.
Whisper produced more plausible municipal terminology and names in these outputs:

| Whistle output | Whisper output | Clip |
|---|---|---|
| class from capacity | classroom capacity | Council |
| Bridor Start Boulder County | Brighter Start Boulder County | Council |
| neugorage | new garage | Planning |
| envision long want | Envision Longmont | Planning |
| requested variants | requested variance | Planning |

Both engines produced timestamps and both had apparent errors. This was an output
comparison, not a human-verified reference transcript or word-error-rate study.
Four minutes cannot establish general accuracy, full-meeting reliability, noisy
room performance, or results on lower-powered computers. No numerical accuracy
claim is made. Generated text remains a draft requiring operator review before
publication; retained source audio/provenance permits correction.

The existing small-model path is retained with an explicit `--engine whisper`
argument and the required `textflowkit[whisper]` dependency. Without these changes,
TextFlowKit 0.1.19 selects Whistle but receives the incompatible model `small`,
which was reproduced as a runtime failure. Version-output normalization prevents
an already-current TextFlowKit installation being repeatedly treated as outdated.

Raw reproducibility evidence is retained locally under
`outputs/townrecord-community-acceptance/`: `compare_engines.py`,
`compare_planning_engines.py`, `engine-comparison.json`,
`planning-engine-comparison.json`, all four transcript JSON files, logs and WAVs.
Council WAV SHA-256:
`021d8e373fccafa7a91facc602d830ade7c5723d75c6de0d8bc12180512b32ed`.
Planning WAV SHA-256:
`6d2ffae38c09e798a14d4958b557c9d9c2f81df10e2e8d6297bb900c6c57fa9b`.
