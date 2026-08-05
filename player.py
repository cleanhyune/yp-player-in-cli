import os
import shutil
import subprocess
import sys
import tempfile

_VOLUME_FILE = "/tmp/yp_volume"
_REASON_FILE = "/tmp/yp_last_reason"
_AUTOPLAY_FILE = "/tmp/yp_autoplay"

_SEEK_SCRIPT = """\
local input = require("mp.input")

local function parse_timecode(s)
    s = s:match("^%s*(.-)%s*$")
    s = s:gsub(":", "")
    if not s:match("^%d+$") then return nil end
    local padded
    if #s == 4 then
        padded = "00" .. s
    elseif #s == 5 then
        padded = "0" .. s
    elseif #s == 6 then
        padded = s
    else
        return nil
    end
    local h = tonumber(padded:sub(1, 2))
    local m = tonumber(padded:sub(3, 4))
    local sec = tonumber(padded:sub(5, 6))
    if m >= 60 or sec >= 60 then return nil end
    return h * 3600 + m * 60 + sec
end

mp.add_key_binding("g", "seek-to-time", function()
    input.get({
        prompt = "이동할 시간 (0710 → 7:10 / 012930 → 1:29:30): ",
        submit = function(text)
            local secs = parse_timecode(text)
            if secs then
                mp.commandv("seek", tostring(secs), "absolute")
                mp.osd_message(string.format("→ %02d:%02d:%02d",
                    math.floor(secs / 3600),
                    math.floor((secs % 3600) / 60),
                    secs % 60), 2)
            else
                mp.osd_message("잘못된 형식 (예: 0710, 012930)", 2)
            end
        end
    })
end)

mp.register_event("shutdown", function()
    local vol = mp.get_property_number("volume", 100)
    local f = io.open(os.getenv("YP_VOLUME_FILE") or "/tmp/yp_volume", "w")
    if f then
        f:write(tostring(math.floor(vol)))
        f:close()
    end
end)

mp.register_event("end-file", function(event)
    local f = io.open("/tmp/yp_last_reason", "w")
    if f then
        f:write(event.reason or "unknown")
        f:close()
    end
end)

mp.add_key_binding("a", "toggle-autoplay", function()
    local current = "1"
    local f = io.open("/tmp/yp_autoplay", "r")
    if f then
        current = f:read("*l") or "1"
        f:close()
    end
    local new_val = (current == "0") and "1" or "0"
    local out = io.open("/tmp/yp_autoplay", "w")
    if out then
        out:write(new_val)
        out:close()
    end
    mp.osd_message("자동재생: " .. (new_val == "1" and "켜짐" or "꺼짐"), 2)
end)
"""

_COMMENTS_BINDING_TEMPLATE = """\

mp.add_key_binding("t", "show-comments", function()
    local url = mp.get_property("path")
    local title = mp.get_property("media-title") or url
    mp.commandv("run", "{python}", "{helper}", url, title)
    mp.osd_message("댓글 불러오는 중...", 2)
end)
"""

_COMMENTS_HELPER_TEMPLATE = """\
import sys
sys.path.insert(0, {project_dir!r})
from comments import fetch_comments, open_comments_window

url = sys.argv[1]
title = sys.argv[2] if len(sys.argv) > 2 else 'YouTube'
try:
    comments = fetch_comments(url)
    if comments:
        open_comments_window(comments, title)
except Exception:
    import traceback
    import os
    import subprocess
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("댓글을 불러오는 중 오류가 발생했습니다:\\n" + traceback.format_exc())
    tmp.close()
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "new-window", f"cat {{tmp.name}}; read; rm {{tmp.name}}"], check=False)
    else:
        script = f'tell application "Terminal" to do script "cat {{tmp.name}}; read; rm {{tmp.name}}"'
        subprocess.run(["osascript", "-e", script], check=False)
"""


def _load_volume() -> int:
    try:
        with open(_VOLUME_FILE) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 100


def _load_reason() -> str:
    try:
        with open(_REASON_FILE) as f:
            return f.read().strip() or "unknown"
    except FileNotFoundError:
        return "unknown"


def is_autoplay_enabled() -> bool:
    try:
        with open(_AUTOPLAY_FILE) as f:
            return f.read().strip() != "0"
    except FileNotFoundError:
        return True


def check_mpv() -> bool:
    return shutil.which("mpv") is not None


def _ytdlp_path() -> str:
    """Prefer the yt-dlp bundled alongside this interpreter (e.g. Homebrew venv),
    then a Homebrew-managed yt-dlp, which is kept newer than pip's py3.9 build."""
    bundled = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.exists(bundled):
        return bundled
    for brew_path in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"):
        if os.path.exists(brew_path):
            return brew_path
    return "yt-dlp"


def play(url: str) -> str:
    volume = _load_volume()
    project_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        os.remove(_REASON_FILE)
    except FileNotFoundError:
        pass

    helper = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    lua = tempfile.NamedTemporaryFile(mode="w", suffix=".lua", delete=False)
    try:
        helper.write(_COMMENTS_HELPER_TEMPLATE.format(project_dir=project_dir))
        helper.close()

        lua.write(_SEEK_SCRIPT + _COMMENTS_BINDING_TEMPLATE.format(python=sys.executable, helper=helper.name))
        lua.close()

        subprocess.run(
            ["mpv", "--no-video", "--ytdl-format=bestaudio/best",
             "--msg-level=ao/coreaudio=error,ffmpeg=fatal",
             f"--script-opts=ytdl_hook-ytdl_path={_ytdlp_path()}",
             f"--volume={volume}",
             f"--script={lua.name}", url],
            check=False,
        )
    finally:
        os.unlink(lua.name)
        os.unlink(helper.name)

    return _load_reason()
