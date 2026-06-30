# video2audio

Extract audio from video files with `ffmpeg`.

## Requirements

- Python 3.11+
- `ffmpeg` available in `PATH`

Install `ffmpeg` on macOS with:

```bash
brew install ffmpeg
```

## Usage

Run the workspace CLI:

```bash
uv run --package video2audio video2audio input.mp4
```

You can provide defaults with a TOML config file:

```bash
uv run --package video2audio video2audio --config video2audio.toml
```

Example config:

```toml
input = "input.mp4"
output = "output/audio.m4a"
format = "m4a"
bitrate = "256k"
sample_rate = 44100
channels = 2
overwrite = true
ffmpeg = "ffmpeg"
```

To convert all supported videos directly under a directory, use `input_dir` instead of
`input`. Directory mode writes audio files to an `ffmpeg-audio` directory inside the input
directory, with each output file using the source video name and configured audio extension:

```toml
input_dir = "videos"
format = "mp3"
bitrate = "192k"
overwrite = true
```

`input` and `input_dir` are mutually exclusive in normal usage. If both are present, the single
file `input` takes priority.

Command-line values override config values:

```bash
uv run --package video2audio video2audio input.mov --format mp3 --overwrite
uv run --package video2audio video2audio --input-dir videos --format m4a --overwrite
```

Supported output formats: `mp3`, `aac`, `m4a`, `wav`, `flac`, `ogg`.

## Build

```bash
uv build --package video2audio
```
