from unittest.mock import patch
from player import play, check_mpv

def test_play_calls_mpv_with_correct_args():
    url = "https://youtube.com/watch?v=abc"
    with patch("player.subprocess.run") as mock_run:
        play(url)

    mock_run.assert_called_once_with(
        ["mpv", "--no-video", "--ytdl-format=bestaudio", url],
        check=False,
    )

def test_check_mpv_returns_true_when_installed():
    with patch("player.shutil.which", return_value="/opt/homebrew/bin/mpv"):
        assert check_mpv() is True

def test_check_mpv_returns_false_when_not_installed():
    with patch("player.shutil.which", return_value=None):
        assert check_mpv() is False
