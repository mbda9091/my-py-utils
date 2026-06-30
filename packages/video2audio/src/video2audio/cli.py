from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from video2audio.config_options import ConfigOptionError, load_toml_config

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_FORMAT = "mp3"
SUPPORTED_FORMATS = ("mp3", "aac", "m4a", "wav", "flac", "ogg")
SUPPORTED_VIDEO_EXTENSIONS = (
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
    ".wmv",
)
DIRECTORY_OUTPUT_NAME = "ffmpeg-audio"


class Video2AudioError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Video2AudioOptions:
    input: Path | None
    input_dir: Path | None
    output: Path | None
    format: str
    bitrate: str
    sample_rate: int | None
    channels: int | None
    overwrite: bool
    ffmpeg: str


@dataclass(slots=True)
class Video2AudioConfig:
    input: object | None = None
    input_dir: object | None = None
    output: object | None = None
    format: object | None = None
    bitrate: object | None = None
    sample_rate: object | None = None
    channels: object | None = None
    overwrite: object | None = None
    ffmpeg: object | None = None
    config_dir: Path | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video2audio",
        description="Extract audio from a video file with ffmpeg.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="Input video file path. Can also be provided in the config file.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help=(
            "Input directory containing video files. Used only when no single input file is "
            f"provided. Outputs are written to {DIRECTORY_OUTPUT_NAME}/ under this directory."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to a TOML config file with default values.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output audio file path. Defaults to the input path with the audio extension.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=SUPPORTED_FORMATS,
        default=None,
        help=f"Output audio format. Default: {DEFAULT_FORMAT}.",
    )
    parser.add_argument(
        "-b",
        "--bitrate",
        default=None,
        help="Audio bitrate for compressed formats. Default: 192k.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        help="Audio sample rate, for example 44100 or 48000.",
    )
    parser.add_argument(
        "--channels",
        type=int,
        choices=(1, 2),
        help="Number of audio channels. Use 1 for mono or 2 for stereo.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=None,
        help="Overwrite the output file if it already exists.",
    )
    parser.add_argument(
        "--ffmpeg",
        default=None,
        help="ffmpeg executable path or command name. Default: ffmpeg.",
    )
    return parser


def resolve_output_path(input_path: Path, output_path: Path | None, audio_format: str) -> Path:
    if output_path is None:
        return input_path.with_suffix(f".{audio_format}")

    if output_path.exists() and output_path.is_dir():
        return output_path / f"{input_path.stem}.{audio_format}"

    if output_path.suffix:
        return output_path

    return output_path.with_suffix(f".{audio_format}")


def load_video2audio_config(config_path: Path | None) -> Video2AudioConfig:
    if config_path is None:
        return Video2AudioConfig()

    resolved_path = config_path.expanduser().resolve()
    try:
        raw_config = load_toml_config(resolved_path)
    except ConfigOptionError as exc:
        raise Video2AudioError(str(exc)) from exc

    return video2audio_config_from_mapping(raw_config, resolved_path.parent)


def video2audio_config_from_mapping(
    raw_config: Mapping[str, object],
    config_dir: Path,
) -> Video2AudioConfig:
    return Video2AudioConfig(
        input=raw_config.get("input"),
        input_dir=raw_config.get("input_dir", raw_config.get("input-dir")),
        output=raw_config.get("output"),
        format=raw_config.get("format"),
        bitrate=raw_config.get("bitrate"),
        sample_rate=raw_config.get("sample_rate", raw_config.get("sample-rate")),
        channels=raw_config.get("channels"),
        overwrite=raw_config.get("overwrite"),
        ffmpeg=raw_config.get("ffmpeg"),
        config_dir=config_dir,
    )


def resolve_cli_path(path: Path) -> Path:
    if path.is_absolute():
        return path

    return (Path.cwd() / path).resolve()


def apply_cli_overrides(config: Video2AudioConfig, namespace: argparse.Namespace) -> None:
    input_arg: Path | None = namespace.input
    input_dir_arg: Path | None = namespace.input_dir
    output_arg: Path | None = namespace.output
    format_arg: str | None = namespace.format
    bitrate_arg: str | None = namespace.bitrate
    sample_rate_arg: int | None = namespace.sample_rate
    channels_arg: int | None = namespace.channels
    overwrite_arg: bool | None = namespace.overwrite
    ffmpeg_arg: str | None = namespace.ffmpeg

    if input_arg is not None:
        config.input = resolve_cli_path(input_arg)
    if input_dir_arg is not None:
        config.input_dir = resolve_cli_path(input_dir_arg)
    if output_arg is not None:
        config.output = resolve_cli_path(output_arg)
    if format_arg is not None:
        config.format = format_arg
    if bitrate_arg is not None:
        config.bitrate = bitrate_arg
    if sample_rate_arg is not None:
        config.sample_rate = sample_rate_arg
    if channels_arg is not None:
        config.channels = channels_arg
    if overwrite_arg is not None:
        config.overwrite = overwrite_arg
    if ffmpeg_arg is not None:
        config.ffmpeg = ffmpeg_arg


def path_from_value(value: object | None, field_name: str, config_dir: Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        path = value
    elif isinstance(value, str):
        path = Path(value)
    else:
        message = f"{field_name} must be a path string."
        raise Video2AudioError(message)

    if config_dir is not None and not path.is_absolute():
        return (config_dir / path).resolve()

    return path


def str_from_value(value: object | None, field_name: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        message = f"{field_name} must be a string."
        raise Video2AudioError(message)

    return value


def int_from_value(value: object | None, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{field_name} must be an integer."
        raise Video2AudioError(message)

    return value


def bool_from_value(value: object | None, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        message = f"{field_name} must be a boolean."
        raise Video2AudioError(message)

    return value


def parse_video2audio_options(
    parser: argparse.ArgumentParser,
    config: Video2AudioConfig,
) -> Video2AudioOptions:
    input_path = path_from_value(config.input, "input", config.config_dir)
    input_dir = path_from_value(config.input_dir, "input_dir", config.config_dir)
    if input_path is None and input_dir is None:
        parser.error("the following arguments are required: input or input_dir")

    output_path = path_from_value(config.output, "output", config.config_dir)

    audio_format = str_from_value(config.format, "format", DEFAULT_FORMAT)
    if audio_format not in SUPPORTED_FORMATS:
        parser.error(
            f"invalid choice for format: {audio_format!r} "
            f"(choose from {', '.join(SUPPORTED_FORMATS)})",
        )

    bitrate = str_from_value(config.bitrate, "bitrate", "192k")
    sample_rate = int_from_value(config.sample_rate, "sample_rate")
    channels = int_from_value(config.channels, "channels")
    if channels is not None and channels not in (1, 2):
        parser.error("invalid choice for channels: choose from 1, 2")

    overwrite = bool_from_value(config.overwrite, "overwrite", default=False)
    ffmpeg = str_from_value(config.ffmpeg, "ffmpeg", "ffmpeg")

    return Video2AudioOptions(
        input=input_path,
        input_dir=input_dir,
        output=output_path,
        format=audio_format,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channels=channels,
        overwrite=overwrite,
        ffmpeg=ffmpeg,
    )


def parse_options(argv: Sequence[str] | None = None) -> Video2AudioOptions:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    config_path: Path | None = namespace.config
    config = load_video2audio_config(config_path)
    apply_cli_overrides(config, namespace)

    return parse_video2audio_options(parser, config)


def build_ffmpeg_command(
    options: Video2AudioOptions,
    input_path: Path,
    output_path: Path,
) -> list[str]:
    command: list[str] = [
        options.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if options.overwrite else "-n",
        "-i",
        str(input_path),
        "-vn",
    ]

    if options.sample_rate is not None:
        command.extend(["-ar", str(options.sample_rate)])
    if options.channels is not None:
        command.extend(["-ac", str(options.channels)])
    if options.format != "wav":
        command.extend(["-b:a", options.bitrate])

    command.append(str(output_path))
    return command


def ensure_ffmpeg_exists(options: Video2AudioOptions) -> None:
    if shutil.which(options.ffmpeg) is not None or Path(options.ffmpeg).exists():
        return

    message = (
        f"Cannot find ffmpeg executable: {options.ffmpeg}. Install ffmpeg or pass "
        "its path with --ffmpeg."
    )
    raise Video2AudioError(message)


def convert_single_video_to_audio(options: Video2AudioOptions, input_path: Path) -> Path:
    input_path = input_path.expanduser().resolve()
    output_path = resolve_output_path(input_path, options.output, options.format).expanduser()
    output_path = output_path.resolve()

    if not input_path.exists():
        message = f"Input video does not exist: {input_path}"
        raise Video2AudioError(message)
    if not input_path.is_file():
        message = f"Input path is not a file: {input_path}"
        raise Video2AudioError(message)
    if output_path.exists() and not options.overwrite:
        message = f"Output file already exists: {output_path}. Use --overwrite to replace it."
        raise Video2AudioError(message)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(options, input_path, output_path)

    try:
        subprocess.run(command, check=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        message = f"ffmpeg failed with exit code {exc.returncode}."
        raise Video2AudioError(message) from exc

    return output_path


def iter_video_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
    )


def convert_directory_to_audio(options: Video2AudioOptions, input_dir: Path) -> list[Path]:
    input_dir = input_dir.expanduser().resolve()

    if not input_dir.exists():
        message = f"Input directory does not exist: {input_dir}"
        raise Video2AudioError(message)
    if not input_dir.is_dir():
        message = f"Input directory path is not a directory: {input_dir}"
        raise Video2AudioError(message)

    video_files = iter_video_files(input_dir)
    if not video_files:
        extensions = ", ".join(SUPPORTED_VIDEO_EXTENSIONS)
        message = (
            f"No supported video files found in directory: {input_dir}. Supported: {extensions}"
        )
        raise Video2AudioError(message)

    output_dir = input_dir / DIRECTORY_OUTPUT_NAME
    created_paths: list[Path] = []
    for video_file in video_files:
        output_path = output_dir / f"{video_file.stem}.{options.format}"
        if output_path.exists() and not options.overwrite:
            message = f"Output file already exists: {output_path}. Use --overwrite to replace it."
            raise Video2AudioError(message)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = build_ffmpeg_command(options, video_file, output_path)

        try:
            subprocess.run(command, check=True)  # noqa: S603
        except subprocess.CalledProcessError as exc:
            message = f"ffmpeg failed for {video_file} with exit code {exc.returncode}."
            raise Video2AudioError(message) from exc

        created_paths.append(output_path)

    return created_paths


def convert_video_to_audio(options: Video2AudioOptions) -> list[Path]:
    ensure_ffmpeg_exists(options)

    if options.input is not None:
        return [convert_single_video_to_audio(options, options.input)]
    if options.input_dir is not None:
        return convert_directory_to_audio(options, options.input_dir)

    message = "Input video or input directory is required."
    raise Video2AudioError(message)


def main(argv: list[str] | None = None) -> int:
    try:
        options = parse_options(argv)
        output_paths = convert_video_to_audio(options)
    except Video2AudioError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    for output_path in output_paths:
        sys.stdout.write(f"created: {output_path}\n")
    return 0
