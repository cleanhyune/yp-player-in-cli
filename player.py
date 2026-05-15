import os
import shutil
import subprocess
import tempfile

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
"""


def check_mpv() -> bool:
    return shutil.which("mpv") is not None


def play(url: str) -> None:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".lua", delete=False)
    try:
        tmp.write(_SEEK_SCRIPT)
        tmp.close()
        subprocess.run(
            ["mpv", "--no-video", "--ytdl-format=bestaudio",
             f"--script={tmp.name}", url],
            check=False,
        )
    finally:
        os.unlink(tmp.name)
