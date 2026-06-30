from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from video2audio.cli import (
    DIRECTORY_OUTPUT_NAME,
    Video2AudioConfig,
    Video2AudioError,
    Video2AudioOptions,
    build_ffmpeg_command,
    build_parser,
    convert_directory_to_audio,
    convert_single_video_to_audio,
    iter_video_files,
    parse_video2audio_options,
    resolve_output_path,
)

EXPECTED_DIRECTORY_CONVERSION_COUNT = 2


def make_options(
    *,
    input_path: Path | None = None,
    input_dir: Path | None = None,
    output: Path | None = None,
    audio_format: str = "mp3",
    overwrite: bool = True,
) -> Video2AudioOptions:
    return Video2AudioOptions(
        input=input_path,
        input_dir=input_dir,
        output=output,
        format=audio_format,
        bitrate="192k",
        sample_rate=None,
        channels=None,
        overwrite=overwrite,
        ffmpeg="ffmpeg",
    )


def test_resolve_output_path_defaults_to_input_stem() -> None:
    assert resolve_output_path(Path("clip.mp4"), None, "m4a") == Path("clip.m4a")


def test_resolve_output_path_uses_existing_directory(tmp_path: Path) -> None:
    assert resolve_output_path(Path("clip.mp4"), tmp_path, "mp3") == tmp_path / "clip.mp3"


def test_config_relative_paths_resolve_from_config_dir(tmp_path: Path) -> None:
    parser = build_parser()
    config = Video2AudioConfig(
        input="videos/input.mp4",
        output="audio/output.m4a",
        format="m4a",
        config_dir=tmp_path,
    )

    options = parse_video2audio_options(parser, config)

    assert options.input == tmp_path / "videos/input.mp4"
    assert options.output == tmp_path / "audio/output.m4a"
    assert options.format == "m4a"


def test_build_ffmpeg_command_includes_audio_options() -> None:
    options = Video2AudioOptions(
        input=Path("input.mp4"),
        input_dir=None,
        output=Path("output.m4a"),
        format="m4a",
        bitrate="256k",
        sample_rate=44100,
        channels=2,
        overwrite=True,
        ffmpeg="/usr/local/bin/ffmpeg",
    )

    assert build_ffmpeg_command(options, Path("input.mp4"), Path("output.m4a")) == [
        "/usr/local/bin/ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        "input.mp4",
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "256k",
        "output.m4a",
    ]


def test_iter_video_files_filters_supported_extensions(tmp_path: Path) -> None:
    (tmp_path / "b.mov").touch()
    (tmp_path / "a.mp4").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "nested.mkv").mkdir()

    assert [path.name for path in iter_video_files(tmp_path)] == ["a.mp4", "b.mov"]


def test_convert_single_video_runs_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.touch()
    run_mock = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.setattr(subprocess, "run", run_mock)

    output_path = convert_single_video_to_audio(make_options(), input_path)

    assert output_path == tmp_path / "input.mp3"
    run_mock.assert_called_once_with(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-b:a",
            "192k",
            str(output_path),
        ],
        check=True,
    )


def test_convert_directory_writes_to_ffmpeg_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "one.mp4").touch()
    (tmp_path / "two.mov").touch()
    run_mock = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.setattr(subprocess, "run", run_mock)

    created_paths = convert_directory_to_audio(make_options(audio_format="m4a"), tmp_path)

    assert created_paths == [
        tmp_path / DIRECTORY_OUTPUT_NAME / "one.m4a",
        tmp_path / DIRECTORY_OUTPUT_NAME / "two.m4a",
    ]
    assert run_mock.call_count == EXPECTED_DIRECTORY_CONVERSION_COUNT


def test_convert_directory_rejects_existing_output_without_overwrite(tmp_path: Path) -> None:
    (tmp_path / "one.mp4").touch()
    output_dir = tmp_path / DIRECTORY_OUTPUT_NAME
    output_dir.mkdir()
    (output_dir / "one.mp3").touch()

    with pytest.raises(Video2AudioError, match="Output file already exists"):
        convert_directory_to_audio(make_options(overwrite=False), tmp_path)


@pytest.mark.parametrize(
    ("config", "match"),
    [
        (Video2AudioConfig(input="input.mp4", sample_rate=True), "sample_rate"),
        (Video2AudioConfig(input="input.mp4", overwrite="yes"), "overwrite"),
        (Video2AudioConfig(input="input.mp4", bitrate=192), "bitrate"),
    ],
)
def test_invalid_config_value_types(config: Video2AudioConfig, match: str) -> None:
    with pytest.raises(Video2AudioError, match=match):
        parse_video2audio_options(build_parser(), config)


@pytest.mark.parametrize(
    ("args", "expected_format"),
    [
        (["input.mp4"], "mp3"),
        (["input.mp4", "--format", "m4a"], "m4a"),
    ],
)
def test_cli_args_resolve_expected_format(args: list[str], expected_format: str) -> None:
    namespace = build_parser().parse_args(args)
    config = Video2AudioConfig()
    config.input = namespace.input
    config.format = namespace.format

    options = parse_video2audio_options(build_parser(), config)

    assert options.input == Path("input.mp4")
    assert options.format == expected_format
