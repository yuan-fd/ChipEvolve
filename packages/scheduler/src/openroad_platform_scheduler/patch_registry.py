"""Immutable registry for reviewed tool-code patch artifacts and fixed policies."""
from __future__ import annotations
import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from openroad_platform_execution import PatchProposal, VerificationPolicy

class PatchRegistry:
    def __init__(self,path: str|Path):
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as c:c.execute("CREATE TABLE IF NOT EXISTS patch_registry_v1 (patch_ref TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL)")
    def register(self,proposal:PatchProposal,policy:VerificationPolicy,*,patch_surface:str)->str:
        proposal.validate();policy.validate()
        if not patch_surface or len(patch_surface)>400: raise ValueError("patch_surface is required")
        payload={"proposal_id":proposal.proposal_id,"base_commit":proposal.base_commit,"patch_text":proposal.patch_text,"evidence_refs":list(proposal.evidence_refs),"patch_sha256":proposal.sha256,"policy":{"allowed_paths":list(policy.allowed_paths),"commands":[list(x) for x in policy.commands],"timeout_seconds":policy.timeout_seconds,"require_human_for_source":policy.require_human_for_source},"patch_surface":patch_surface}
        encoded=json.dumps(payload,sort_keys=True,separators=(',',':')); digest=hashlib.sha256(encoded.encode()).hexdigest();ref=f"artifact:patch-registry:{digest}"
        with self._connect() as c:
            try:c.execute("INSERT INTO patch_registry_v1 VALUES (?,?,?,?)",(ref,encoded,digest,datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError: pass
        return ref
    def resolve(self,ref:str)->tuple[PatchProposal,VerificationPolicy,str]:
        if not ref.startswith("artifact:patch-registry:"): raise ValueError("unsupported patch reference")
        with self._connect() as c:r=c.execute("SELECT payload,sha256 FROM patch_registry_v1 WHERE patch_ref=?",(ref,)).fetchone()
        if r is None: raise KeyError(ref)
        if hashlib.sha256(r[0].encode()).hexdigest()!=r[1]: raise RuntimeError("patch registry integrity check failed")
        p=json.loads(r[0]); proposal=PatchProposal(p["proposal_id"],p["base_commit"],p["patch_text"],tuple(p["evidence_refs"])); policy=VerificationPolicy(tuple(p["policy"]["allowed_paths"]),tuple(tuple(x) for x in p["policy"]["commands"]),p["policy"]["timeout_seconds"],p["policy"]["require_human_for_source"]);proposal.validate();policy.validate();return proposal,policy,p["patch_surface"]
    def _connect(self): return sqlite3.connect(self.path)
