"""Jianying native TTS: one final subtitle cue -> one independently generated WAV.

Run with jianying-editor's Python through its run_skill.py launcher. Audio generation
does not create a final video or infer final source-dialogue/quiet-slot placement.
"""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HEADER = re.compile(r'^### (\d+:\d{2})–(\d+:\d{2})｜(.+)$', re.M)
DEFAULT_ROOT = Path.home()/'.workbuddy/skills/jianying-editor'
DEFAULT_SPEAKER = 'zh_female_xiaohe_saturn_bigtts'
DEFAULT_VOICE = '台湾腔甜妹'

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def bootstrap(skill_root):
    root=Path(skill_root or os.getenv('JY_SKILL_ROOT') or DEFAULT_ROOT).resolve()
    if not (root/'scripts/jy_wrapper.py').is_file():
        raise RuntimeError(f'Cannot locate jianying-editor: {root}')
    sys.path.insert(0,str(root/'scripts'))
    ff=Path.home()/'.workbuddy/binaries/python/envs/default/Lib/site-packages/static_ffmpeg/bin/win32'
    if ff.is_dir():os.environ['PATH']=str(ff)+os.pathsep+os.environ.get('PATH','')
    return root

def check_review(script):
    script=Path(script).resolve()
    if script.parent.name=='gameshow-recap-creator' or (script.parent/'narration-review.json').exists():
        validator=Path(__file__).resolve().parents[2]/'gameshow-recap-creator/tools/validate_narration_review.py'
        if not validator.is_file():raise RuntimeError('Narration review validator is missing')
        result=subprocess.run([sys.executable,'-X','utf8',str(validator),str(script)],capture_output=True,text=True,encoding='utf8')
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)

def parse_captions(script):
    """Keep semantic punctuation boundaries; never synthesize a section then slice it.

    Any unusually long clause is retained for editorial review, not arbitrarily cut
    inside names/words to satisfy a width target. Final caption edits regenerate only
    that cue and invalidate its cached TTS through the content/voice hash.
    """
    content=Path(script).read_text(encoding='utf-8-sig')
    headers=list(HEADER.finditer(content));sections=[];cues=[]
    for i,h in enumerate(headers):
        chunk=content[h.end():headers[i+1].start() if i+1<len(headers) else len(content)]
        chunk=chunk.split('## 包装清单',1)[0]
        paragraphs=[];inside=False
        for line in chunk.splitlines():
            s=line.strip()
            if s.startswith('**旁白**'):inside=True
            elif s.startswith('**') or s.startswith('###'):inside=False
            elif inside and s.startswith('>'):paragraphs.append(s.lstrip('>').strip())
        text=''.join(paragraphs)
        # Every punctuation-delimited complete clause becomes a final subtitle unit.
        # A script line is already an approved caption boundary. Never join it
        # to the next line before splitting clauses (some lines omit punctuation).
        parts=[part for paragraph in paragraphs
               for part in re.findall(r'[^，。！？；：\n]+[，。！？；：]*',paragraph)]
        section={'section_id':i+1,'title':h[3],'narration':text,'cue_ids':[],
                 'source_ranges':re.findall(r'\[(\d+:\d{2})–(\d+:\d{2})\]',chunk),
                 'has_original_dialogue':'**原声保留**' in chunk,
                 'has_quiet_action':'**留白**' in chunk}
        for j,part in enumerate(parts):
            caption=part.strip().rstrip('，。！？；： ')
            if not caption:continue
            cid=f's{i+1:03d}_c{j+1:03d}'
            cues.append({'cue_id':cid,'section_id':i+1,'subtitle':caption,'tts_text':part.strip(),
                         'needs_width_review':len(caption)>25})
            section['cue_ids'].append(cid)
        sections.append(section)
    if not cues:raise ValueError('No explicit narration blocks found')
    return sections,cues

def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries',
         'stream=codec_name,sample_rate,channels,duration:format=duration','-of','json',str(path)],
         capture_output=True,text=True,encoding='utf8',check=True)
    d=json.loads(result.stdout)
    if not d.get('streams'):raise RuntimeError(f'No audio stream: {path}')
    duration=float(d['format']['duration'])
    if duration<=0:raise RuntimeError(f'Empty audio: {path}')
    return duration,d['streams'][0]

def stamp(seconds):
    ms=round(seconds*1000)
    return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'

async def generate(args,sections,cues,out):
    from universal_tts import generate_voice_with_meta
    results=[]
    for n,cue in enumerate(cues,1):
        key=hashlib.sha256(json.dumps({'backend':'sami','speaker':args.speaker,'text':cue['tts_text']},ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:12]
        stem=f"{cue['cue_id']}_{key}";wave=out/'sentences'/f'{stem}.wav';ogg=wave.with_suffix('.ogg')
        wave.parent.mkdir(parents=True,exist_ok=True)
        cached=False
        if wave.is_file() and not args.force:
            try:probe(wave);cached=True
            except Exception:cached=False
        if not cached:
            trace=io.StringIO()
            with contextlib.redirect_stdout(trace):
                path,backend=await generate_voice_with_meta(cue['tts_text'],str(ogg),args.speaker,
                    backend='sami',allow_fallback=False,sami_retries=2)
            log=re.sub(r'dev=\d+, iid=\d+','dev=[redacted], iid=[redacted]',trace.getvalue())
            with (out/'generation.log').open('a',encoding='utf8') as f:f.write(cue['cue_id']+'\n'+log)
            if not path or backend!='sami':
                (out/'failure.json').write_text(json.dumps({'cue':cue,'completed':len(results),'voice':args.voice_name,'reason':log,'edge_fallback':False},ensure_ascii=False,indent=2),encoding='utf8')
                raise RuntimeError(f"Jianying TTS failed for {cue['cue_id']}; stopped, Edge fallback disabled. {log.strip()}")
            subprocess.run(['ffmpeg','-v','error','-y','-i',str(path),'-vn','-acodec','pcm_s16le',str(wave)],check=True)
        duration,stream=probe(wave)
        results.append({**cue,'audio_file':str(wave.resolve()),'duration':round(duration*1000)/1000,
                        'decoded_duration':round(duration,6),
                        'audio_sha256':digest(wave),'backend':'sami','voice_name':args.voice_name,
                        'speaker_id':args.speaker,'resource_id':args.resource_id,'cached':cached,
                        'sample_rate':int(stream['sample_rate']),'channels':stream['channels']})
        (out/'sentences-manifest.progress.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
        if n==1 or n%10==0 or n==len(cues):print(f'Jianying TTS {n}/{len(cues)}; one cue = one WAV',flush=True)
    return results

def write_delivery(args,sections,results,out,total_cues):
    cursor=0;entries=[]
    for i,c in enumerate(results,1):
        c['start']=round(cursor,6);cursor+=c['duration'];c['end']=round(cursor,6)
        entries.append(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['subtitle']}\n")
    manifest={'schema_version':1,'status':'complete' if len(results)==total_cues else 'pilot',
       'script':str(Path(args.from_script).resolve()),'script_sha256':digest(args.from_script),
       'provider':'jianying_sami','voice_name':args.voice_name,'speaker_id':args.speaker,
       'resource_id':args.resource_id,'edge_fallback':False,'subtitle_audio_mapping':'one_to_one',
       'timeline':'narration_only_review; insert original dialogue and real quiet actions during EDL construction',
       'duration':round(cursor,6),'total_script_cues':total_cues,'sections':sections,'cues':results}
    (out/'sentences-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    (out/'narration-only.srt').write_text('\n'.join(entries),encoding='utf8')
    (out/'captions-review.tsv').write_text('cue_id\tsection\tsubtitle\tduration\taudio\n'+'\n'.join(
       f"{c['cue_id']}\t{c['section_id']}\t{c['subtitle']}\t{c['duration']}\t{c['audio_file']}" for c in results)+'\n',encoding='utf8')
    return manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--from-script',required=True);p.add_argument('-o','--output',required=True)
    p.add_argument('--skill-root');p.add_argument('--speaker',default=DEFAULT_SPEAKER)
    p.add_argument('--voice-name',default=DEFAULT_VOICE);p.add_argument('--resource-id',default='7553535833219812618')
    p.add_argument('--limit',type=int,help='Pilot only; manifest explicitly marked incomplete')
    p.add_argument('--cue-id',help='Regenerate only this subtitle ID; write a separate replacement record')
    p.add_argument('--force',action='store_true',help='Regenerate selected audio even when a valid cached WAV exists')
    p.add_argument('--plan-only',action='store_true',help='Write the final subtitle units without sending speech text')
    args=p.parse_args();bootstrap(args.skill_root);check_review(args.from_script)
    sections,cues=parse_captions(args.from_script);out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    (out/'captions-plan.json').write_text(json.dumps({'script_sha256':digest(args.from_script),'sections':sections,'cues':cues},ensure_ascii=False,indent=2),encoding='utf8')
    if args.plan_only:
        print(json.dumps({'cues':len(cues),'long_clauses':[c for c in cues if c['needs_width_review']]},ensure_ascii=False));return
    total=len(cues)
    if args.cue_id:
        cues=[c for c in cues if c['cue_id']==args.cue_id]
        if not cues:raise ValueError(f'Unknown subtitle ID: {args.cue_id}')
    if args.limit:cues=cues[:args.limit]
    results=asyncio.run(generate(args,sections,cues,out))
    if args.cue_id:
        record={'status':'replacement','script_sha256':digest(args.from_script),'cues':results,'note':'Replace only the corresponding audio/subtitle in the draft, then recheck following timing.'}
        target=out/f'regeneration-{args.cue_id}.json'
        target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'status':'replacement','record':str(target)},ensure_ascii=False));return
    manifest=write_delivery(args,sections,results,out,total)
    print(json.dumps({'status':manifest['status'],'audio_files':len(results),'duration':manifest['duration'],'manifest':str(out/'sentences-manifest.json')},ensure_ascii=False))

if __name__=='__main__':main()
