# Setup

## Required Runtime

- Python 3.10+
- Pillow
- ffmpeg
- ffprobe

The FastAPI app runs the productized service in `services.evidence_video`; it does not require `uv`.

## macOS

```bash
brew install ffmpeg
```

## Verification

```bash
ffmpeg -version
ffprobe -version
python -c "import PIL; print(PIL.__version__)"
```

If `ffmpeg` or `ffprobe` is missing, `/api/evidence/video/extract` still accepts the upload but the extraction task returns `dependency_missing`.
