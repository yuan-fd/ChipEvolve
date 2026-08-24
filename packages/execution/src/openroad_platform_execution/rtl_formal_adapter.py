#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path
def now(): return datetime.now(timezone.utc).isoformat()
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v): p.write_text(json.dumps(v,indent=2),encoding="utf-8")
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--result',type=Path,required=True);a=p.parse_args(); start=now()
 try:
  t=json.loads(a.request.read_text())["task"]; root=a.result.parent; inp=root/'inputs';out=root/'outputs';inp.mkdir(exist_ok=True);out.mkdir(exist_ok=True)
  files=[]
  for key,name in (("rtl","design.sv"),("property","property.sv")):
   item=t['inputs'][key]; src=Path(item['path']).resolve()
   if not src.is_file() or sha(src)!=item['sha256']: raise ValueError(f'{key} immutable hash changed')
   dst=inp/name;shutil.copy2(src,dst);files.append(dst)
  log=out/'formal.log'; depth=int(t['parameters']['depth']); script=f'read_verilog -formal -sv {files[0]} {files[1]}; prep -top {t["inputs"]["property_top"]} -flatten; chformal -lower; sat -prove-asserts -seq {depth}'
  run=subprocess.run([os.environ['YOSYS_BIN'],'-Q','-p',script],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);log.write_text(run.stdout)
  if run.returncode: write(a.result,{"schema_version":1,"status":"failed","exit_code":run.returncode,"started_at":start,"ended_at":now(),"metrics":[],"artifacts":[],"failure":{"category":"rtl_formal_failed","message":"bounded proof failed; see log"},"provenance":{"adapter":"rtl-formal-v1"}});return run.returncode
  report=out/'formal.json';report.write_text(json.dumps({"property_top":t['inputs']['property_top'],"depth":depth,"rtl_sha256":sha(files[0]),"property_sha256":sha(files[1])}))
  write(a.result,{"schema_version":1,"status":"succeeded","exit_code":0,"started_at":start,"ended_at":now(),"metrics":[{"name":"rtl.formal","value":1,"unit":"pass"}],"artifacts":[{"kind":"formal_report","path":"outputs/formal.json"},{"kind":"log","path":"outputs/formal.log"}],"failure":None,"provenance":{"adapter":"rtl-formal-v1","bounded":True,"depth":depth}});return 0
 except Exception as e: write(a.result,{"schema_version":1,"status":"failed","exit_code":1,"started_at":start,"ended_at":now(),"metrics":[],"artifacts":[],"failure":{"category":"adapter_error","message":f'{type(e).__name__}: {e}'},"provenance":{"adapter":"rtl-formal-v1"}});return 1
if __name__=='__main__': raise SystemExit(main())
