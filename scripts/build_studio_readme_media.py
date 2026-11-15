"""Build small README media from the existing competition film; originals stay intact.

Requires a full FFmpeg build (PATH, --ffmpeg, or imageio-ffmpeg) and ffprobe.
The competition sources are local, ignored assets under funGUI/contestfiles.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "funGUI/contestfiles"
FILM = SOURCE / "videosrc/exports/FunHarness_Studio_参赛演示_FunStudio_V2_1080p.mp4"
OUT = ROOT / "assets/studio"
WORK = ROOT / "funGUI/.audit/readme-media"
FPS = 24
OVERLAP = 4  # A short dissolve: 1/6 second, with exact frame-aligned cuts.
SCREENSHOTS = {
    "chat": "chatsession.png",
    "teams": "agent_teams_visualization.png",
    "concepts": "conceptworkshop_8.png",
    "study": "studylab_1.png",
    "garden": "mindgarden_9.png",
    "replay": "sessionreplay.png",
}
# Scene, in-scene offset, source seconds, playback speed. Preserve the original
# explanatory typography, real product interactions, and complete visual frames.
EDITS = [
    ("Hook", 0, 17.2, 3.2),
    ("ConceptPage", 5.8, 5.6, 1.15),
    ("Sandbox", 6.5, 6, 1.2),
    ("StudyGraph", 5, 6, 1.2),
    ("StarMap", 5, 5.5, 1.2),
    ("Teams", 9, 7, 1.25),
    ("Swarm", 12, 6, 1.25),
    ("Replay", 10, 6, 1.2),
    ("Core", 6, 4.5, 1.05),
    ("Outro", 8, 3.5, 1),
]
MUSIC_CREDIT = "Carefree by Kevin MacLeod (incompetech.com), CC BY 4.0. Excerpt, volume adjustment and fades."


def run(args: list[str | Path], log: str):
    with (WORK / log).open("w", encoding="utf-8") as stream:
        result = subprocess.run([str(arg) for arg in args], stdout=stream, stderr=stream)
    if result.returncode:
        raise RuntimeError((WORK / log).read_text(encoding="utf-8")[-4000:])


def timeline():
    source = (SOURCE / "videosrc/studio/src/Composition.tsx").read_text(encoding="utf-8")
    position = 0
    scenes = {}
    pattern = r'Sequence durationInFrames=\{(\d+)\} name="([^"]+)"|linearTiming\(\{durationInFrames:(\d+)\}'
    for match in re.finditer(pattern, source):
        if match[1]:
            scenes[match[2]] = position / 30
            position += int(match[1])
        else:
            position -= int(match[3])
    return scenes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    options = parser.parse_args()
    if not options.ffmpeg:
        import imageio_ffmpeg
        options.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg = options.ffmpeg
    ffprobe = options.ffprobe or str(ROOT / "funGUI/video_src/node_modules/@remotion/compositor-win32-x64-msvc/ffprobe.exe")
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    for target, name in SCREENSHOTS.items():
        shutil.copy2(SOURCE / "docsrc" / name, OUT / f"{target}.png")
    starts = timeline()
    shots = []
    cursor = 0
    for index, (scene, offset, seconds, speed) in enumerate(EDITS):
        frames = round(seconds / speed * FPS)
        shots.append(dict(scene=scene, source_start=round(starts[scene] + offset, 3),
                          source_seconds=seconds, speed=speed, frames=frames,
                          output_start=cursor / FPS))
        cursor += frames - (OVERLAP if index < len(EDITS) - 1 else 0)
    duration = cursor / FPS

    def encode_shot(pair):
        index, shot = pair
        dest = WORK / f"shot-{index:02}.mp4"
        run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-threads", "2",
             "-ss", str(shot["source_start"]), "-t", str(shot["source_seconds"]), "-i", FILM,
             "-an", "-vf", f"setpts=(PTS-STARTPTS)/{shot['speed']},fps={FPS},scale=1280:720:flags=lanczos,setsar=1",
             "-frames:v", str(shot["frames"]), "-c:v", "libx264", "-preset", "fast",
             "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "2", dest], f"shot-{index:02}.log")
        print(f"Cut {index + 1}/{len(shots)}: {shot['scene']}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(encode_shot, enumerate(shots)))
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "1"]
    for index in range(len(shots)):
        args += ["-threads", "1", "-i", str(WORK / f"shot-{index:02}.mp4")]
    args += ["-ss", "8", "-i", str(SOURCE / "videosrc/studio/public/audio/carefree.mp3")]
    filters = [f"[{i}:v]setpts=PTS-STARTPTS,fps={FPS},settb=1/{FPS}[v{i}]" for i in range(len(shots))]
    previous = "v0"
    for index in range(1, len(shots)):
        label = f"mix{index}"
        filters.append(f"[{previous}][v{index}]xfade=transition=fade:duration={OVERLAP / FPS:.8f}:offset={shots[index]['output_start']:.8f}[{label}]")
        previous = label
    filters.append(f"[{len(shots)}:a]atrim=duration={duration},asetpts=PTS-STARTPTS,loudnorm=I=-20:TP=-2:LRA=7,afade=t=in:d=0.35,afade=t=out:st={duration - 1.4}:d=1.4[audio]")
    filters_file = WORK / "video-filter.txt"
    filters_file.write_text(";\n".join(filters), encoding="utf-8")
    video = OUT / "studio-demo.mp4"
    encode_args = ["-filter_complex_script", str(filters_file), "-map", f"[{previous}]", "-map", "[audio]",
                   "-t", str(duration), "-c:v", "libx264", "-preset", "medium", "-crf", "25",
                   "-maxrate", "1400k", "-bufsize", "2800k", "-pix_fmt", "yuv420p", "-profile:v", "high",
                   "-g", "48", "-threads", "3", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart",
                   "-metadata", "title=FunHarness Studio — feature tour",
                   "-metadata", f"comment={MUSIC_CREDIT} https://creativecommons.org/licenses/by/4.0/",
                   str(video)]
    print(f"Encoding {duration:.2f}s feature tour", flush=True)
    run(args + encode_args, "video-encode.log")
    if video.stat().st_size >= 9_000_000:
        raise RuntimeError("Video exceeded the 9 MB publishing budget")

    # A separate short teaser, with no full-length animated duplicate in README.
    teaser = []
    for index, (scene, shift) in enumerate([("Hook", 0.4), ("ConceptPage", 0.5), ("Teams", 0.5), ("Replay", 0.5)]):
        start = next(shot["output_start"] for shot in shots if shot["scene"] == scene) + shift
        teaser.append(f"[0:v]trim=start={start}:duration=3,setpts=PTS-STARTPTS[g{index}]")
    teaser.append("[g0][g1][g2][g3]concat=n=4:v=1:a=0,fps=10,scale=720:405:flags=lanczos,split[frames][colors]")
    teaser.append("[colors]palettegen=max_colors=96:stats_mode=diff[palette]")
    teaser.append("[frames][palette]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle[gif]")
    gif = OUT / "studio-demo.gif"
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "1", "-i", video,
         "-filter_complex", ";".join(teaser), "-map", "[gif]", "-loop", "0", gif], "gif-encode.log")
    if gif.stat().st_size >= 4_000_000:
        raise RuntimeError("GIF exceeded the 4 MB page-load budget")
    run([ffmpeg, "-v", "error", "-i", video, "-f", "null", "-"], "decode-check.log")
    metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_entries",
                                                  "format=duration,size:stream=codec_name,width,height,pix_fmt,r_frame_rate", "-of", "json", str(video)]))
    report = {"source": str(FILM.relative_to(ROOT)).replace("\\", "/"), "source_seconds": 500.458667,
              "shots": shots, "screenshots": SCREENSHOTS, "video": metadata,
              "gif": {"seconds": 12, "width": 720, "fps": 10, "bytes": gif.stat().st_size},
              "music": {"credit": MUSIC_CREDIT, "source": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1400037",
                        "license": "https://creativecommons.org/licenses/by/4.0/"}}
    (OUT / "media.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Video contact sheet for local visual QA; not included in repository assets.
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video,
         "-vf", "fps=1/4,scale=426:240,tile=3x4", "-frames:v", "1", WORK / "video-contact.jpg"], "contact.log")
    print(json.dumps({"video_seconds": duration, "video_bytes": video.stat().st_size,
                      "gif_bytes": gif.stat().st_size, "output": str(OUT)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
