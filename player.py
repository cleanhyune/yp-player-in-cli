import shutil
import subprocess


def check_mpv() -> bool:
    return shutil.which("mpv") is not None


def play(url: str) -> None:
    subprocess.run(
        ["mpv", "--no-video", "--ytdl-format=bestaudio", url],
        check=False,
    )
