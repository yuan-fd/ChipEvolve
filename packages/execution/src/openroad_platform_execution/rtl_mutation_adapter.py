#!/usr/bin/env python3
"""Run bounded single-site mutants with fixed Icarus commands."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path
def _now(): return datetime.now(timezone.utc).isoformat()
def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def _write(path,value): path.write_text(json.dumps(value,indent=2),encoding="utf-8")
def _mutants(source, maximum):
    source_sha=hashlib.sha256(source.encode()).hexdigest(); rows=[]
    for name, pattern, replacement in (("eq_to_ne",r"==(?!=)","!="),("ne_to_eq",r"!=","=="),("plus_to_minus",r"(?<!\+)\+(?!\+)","-"),("minus_to_plus",r"(?<!-)\-(?!-)","+"),("and_to_or",r"&&","||"),("or_to_and",r"\|\|","&&"),("zero_to_one",r"(?<![\w'])0(?![\w])","1"),("one_to_zero",r"(?<![\w'])1(?![\w])","0")):
        for match in re.finditer(pattern,source):
            mutated=source[:match.start()]+replacement+source[match.end():]; digest=hashlib.sha256(mutated.encode()).hexdigest(); rows.append({"mutation_id":f"mut-{hashlib.sha256(f'{source_sha}:{name}:{match.start()}:{digest}'.encode()).hexdigest()[:20]}","operator":name,"source_sha256":source_sha,"mutated_source":mutated,"mutated_source_sha256":digest,"location":match.start()})
            if len(rows)>=maximum:return rows
    return rows
def _report(mutants,outcomes,tb_sha,verifier,minimum):
    rows=[{key:value for key,value in item.items() if key!="mutated_source"}|{"outcome":outcomes.get(item["mutation_id"],"not_run")} for item in mutants]; executable=[row for row in rows if row["outcome"] in {"killed","survived"}]; killed=sum(row["outcome"]=="killed" for row in executable);score=killed/len(executable) if executable else 0.0
    return {"schema_version":1,"kind":"mutation_evidence","verifier_identity":verifier,"testbench_sha256":tb_sha,"source_sha256":mutants[0]["source_sha256"] if mutants else None,"mutants":rows,"generated_count":len(rows),"executable_count":len(executable),"killed_count":killed,"survived_count":len(executable)-killed,"invalid_count":sum(row["outcome"]=="invalid" for row in rows),"timed_out_count":sum(row["outcome"]=="timed_out" for row in rows),"not_run_count":sum(row["outcome"]=="not_run" for row in rows),"mutation_score":score,"minimum_score":minimum,"eligible":bool(executable) and score>=minimum,"claim":"testbench fault-detection evidence only; not a proof of functional correctness","execution_allowed":False}
def _oracle_passed(output):
    matches=re.findall(r"TB_SUMMARY\s+total=(\d+)\s+errors=(\d+)",output)
    return bool(matches) and int(matches[-1][0])>0 and int(matches[-1][1])==0 and bool(re.search(r"(?m)^PASS\s*$",output))
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--request",type=Path,required=True);parser.add_argument("--result",type=Path,required=True);args=parser.parse_args();started=_now()
    try:
        task=json.loads(args.request.read_text())["task"];ins=task["inputs"];params=task["parameters"];rtl,tb=Path(ins["rtl"]["path"]).resolve(),Path(ins["testbench"]["path"]).resolve()
        if not rtl.is_file() or not tb.is_file() or _sha(rtl)!=ins["rtl"]["sha256"] or _sha(tb)!=ins["testbench"]["sha256"]: raise ValueError("immutable RTL/testbench input changed")
        root=args.result.parent.resolve();work=root/"mutants";out=root/"outputs";work.mkdir(exist_ok=True);out.mkdir(exist_ok=True);staged_tb=work/"frozen_tb.sv";shutil.copy2(tb,staged_tb);mutants=_mutants(rtl.read_text(encoding="utf-8"),int(params["maximum_mutants"]));outcomes={}
        with (out/"mutation.log").open("w",encoding="utf-8") as log:
            for item in mutants:
                source=work/f"{item['mutation_id']}.sv";image=work/f"{item['mutation_id']}.out";source.write_text(item["mutated_source"],encoding="utf-8")
                command=[os.environ["IVERILOG_BIN"],"-g2012","-s",ins["testbench_top"],"-o",str(image),str(source),str(staged_tb)];step=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False);log.write(f"$ {' '.join(command)}\n{step.stdout}\n")
                if step.returncode: outcomes[item["mutation_id"]]="invalid";continue
                try:
                    run=subprocess.run([os.environ["VVP_BIN"],str(image)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False,timeout=int(params.get("per_mutant_timeout_seconds",30)))
                except subprocess.TimeoutExpired as exc:
                    log.write(f"$ {os.environ['VVP_BIN']} {image}\nTIMEOUT after {params.get('per_mutant_timeout_seconds',30)}s\n{(exc.stdout or '')}\n")
                    outcomes[item["mutation_id"]]="timed_out";continue
                log.write(f"$ {os.environ['VVP_BIN']} {image}\n{run.stdout}\n");outcomes[item["mutation_id"]]="survived" if run.returncode==0 and _oracle_passed(run.stdout) else "killed"
        report=_report(mutants,outcomes,ins["testbench"]["sha256"],str(params["verifier_identity"]),float(params["minimum_score"]));_write(out/"mutation.json",report)
        _write(args.result,{"schema_version":1,"status":"succeeded","exit_code":0,"started_at":started,"ended_at":_now(),"metrics":[{"name":"rtl.mutation_score","value":report["mutation_score"],"unit":"ratio"}],"artifacts":[{"kind":"mutation_report","path":"outputs/mutation.json"},{"kind":"log","path":"outputs/mutation.log"}],"failure":None,"provenance":{"adapter":"rtl-mutation-v1","eligible":report["eligible"],"source_sha256":report.get("source_sha256")}});return 0
    except Exception as exc:
        _write(args.result,{"schema_version":1,"status":"failed","exit_code":1,"started_at":started,"ended_at":_now(),"metrics":[],"artifacts":[],"failure":{"category":"mutation_adapter_error","message":f"{type(exc).__name__}: {exc}"},"provenance":{"adapter":"rtl-mutation-v1"}});return 1
if __name__=="__main__": raise SystemExit(main())
