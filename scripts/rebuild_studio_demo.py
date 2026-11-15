"""Re-cut the original 1080p film to 30 seconds, with a ~5 MB upload budget.

Run using a Python environment with imageio-ffmpeg installed. Keeps the GIF and
gallery intact. Competition source files remain untouched.
"""
import concurrent.futures
import json
import subprocess
from pathlib import Path
import imageio_ffmpeg
from build_studio_readme_media import ROOT, FILM, SOURCE, MUSIC_CREDIT, timeline

FF = imageio_ffmpeg.get_ffmpeg_exe()
WORK = ROOT / 'funGUI/.audit/readme-media/demo-1080p'
OUT = ROOT / 'assets/studio'
WORK.mkdir(parents=True, exist_ok=True)

def run(args, name):
    with (WORK / (name + '.log')).open('w', encoding='utf-8') as log:
        subprocess.run([FF, '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)],
                       stdout=log, stderr=log, check=True)

# Clean cuts keep short demos legible; playback stays close to real speed.
# 8 scenes × 3.75 seconds = exactly 30 seconds at 24 fps.
EDITS = [('Hook', 2, 2.5), ('ConceptPage', 5.8, 1),
         ('Sandbox', 6.5, 1), ('StudyGraph', 5, 1),
         ('StarMap', 5, 1), ('Teams', 9, 1),
         ('Swarm', 12, 1), ('Replay', 10, 1)]

def main():
    starts = timeline()
    shots = []
    for i, (scene, offset, speed) in enumerate(EDITS):
        shots.append(dict(scene=scene, source_start=round(starts[scene]+offset, 3),
                          source_seconds=3.75*speed, speed=speed, frames=90,
                          output_start=i*3.75))

    def cut(pair):
        i, shot = pair
        filters = [f"setpts=(PTS-STARTPTS)/{shot['speed']}", 'fps=24',
                   'scale=1920:1080:flags=lanczos:out_range=tv', 'format=yuv420p', 'setsar=1']
        if shot['scene'] != 'Hook':
            # Source header sits on a flat backdrop, outside the product UI.
            color = '0x142C28' if shot['scene'] == 'StarMap' else '0xF3F0E8'
            filters.append(f'drawbox=x=1100:y=25:w=800:h=55:color={color}:t=fill')
        if i == len(shots)-1:
            filters.append('fade=t=out:st=3.4:d=0.35')
        run(['-threads', '2', '-ss', shot['source_start'], '-t', shot['source_seconds'],
             '-i', FILM, '-an', '-vf', ','.join(filters), '-frames:v', '90',
             '-c:v', 'libx264', '-preset', 'fast', '-crf', '12', '-pix_fmt', 'yuv420p',
             '-threads', '2', WORK / f'shot-{i}.mp4'], f'shot-{i}')
        print('Cut:', shot['scene'], flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(cut, enumerate(shots)))
    listing = WORK / 'concat.txt'
    listing.write_text(''.join(f"file 'shot-{i}.mp4'\n" for i in range(len(shots))), encoding='utf-8')
    video_input = ['-f', 'concat', '-safe', '0', '-i', listing]
    codec = ['-c:v', 'libx264', '-preset', 'slow', '-b:v', '1200k',
             '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-threads', '4',
             '-passlogfile', WORK / 'two-pass', '-t', '30']
    print('Encoding 1080p, two-pass / 30 seconds', flush=True)
    run(video_input + codec + ['-pass', '1', '-an', '-f', 'null', '-'], 'pass1')
    music = SOURCE / 'videosrc/studio/public/audio/carefree.mp3'
    output = WORK / 'studio-demo.mp4'
    run(video_input + ['-ss', '8', '-i', music] + codec + ['-pass', '2',
        '-map', '0:v:0', '-map', '1:a:0', '-af',
        'loudnorm=I=-20:TP=-2:LRA=7,afade=t=in:d=0.35,afade=t=out:st=28.6:d=1.4',
        '-c:a', 'aac', '-b:a', '96k', '-ac', '2', '-movflags', '+faststart',
        '-metadata', 'title=FunHarness Studio — 30-second feature tour',
        '-metadata', f'comment={MUSIC_CREDIT}', output], 'pass2')
    run(['-i', output, '-f', 'null', '-'], 'decode')
    if not 4_000_000 < output.stat().st_size < 5_500_000:
        raise ValueError(f'Unexpected file size: {output.stat().st_size}')
    probe = ROOT / 'funGUI/video_src/node_modules/@remotion/compositor-win32-x64-msvc/ffprobe.exe'
    meta = json.loads(subprocess.check_output([str(probe), '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_name,width,height,pix_fmt,r_frame_rate',
        '-of', 'json', str(output)]))
    assert abs(float(meta['format']['duration']) - 30) < 0.1
    assert meta['streams'][0]['width'] == 1920
    run(['-i', output, '-vf', 'fps=1/3.75,scale=480:270,tile=2x4',
         '-frames:v', '1', WORK / 'contact.jpg'], 'contact')
    # Replace only after a complete decode and metadata verification.
    import shutil
    shutil.copyfile(output, OUT / 'studio-demo.mp4')
    report = json.loads((OUT / 'media.json').read_text(encoding='utf-8'))
    report.update(shots=shots, video=meta)
    report['video_edit'] = 'Original 1080p source; 30s; competition header and outro removed; two-pass H.264 ~5 MB.'
    (OUT / 'media.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(meta), flush=True)

if __name__ == '__main__':
    main()
