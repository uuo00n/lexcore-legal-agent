# Strategy And Params

## Strategies

- `scene`: default. Use ffmpeg scene detection plus periodic sampling for screen recordings.
- `interval`: extract one frame every `interval_seconds`.
- `keyframe`: extract encoded key frames.
- `smart`: use ffmpeg duplicate-frame filtering.

## Defaults

| Parameter | Default | Meaning |
| --- | --- | --- |
| `strategy` | `scene` | screen-recording friendly extraction |
| `scene_threshold` | `0.10` | lower means more sensitive |
| `sample_interval` | `2.0` | periodic fallback sampling in scene mode |
| `dedup_threshold` | `4` | dHash Hamming distance threshold |
| `quality` | `2` | high-quality JPEG output |
| `max_size` | `0` | keep original frame size |

## Report

`_report.json` records:

- status
- evidence id
- original filename
- duration
- extraction strategy
- retained frame count
- frame filename
- capture timestamp
- SHA256 hash
- dedup stats
