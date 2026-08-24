import sys
from openroad_platform_execution import PatchProposal, VerificationPolicy
from openroad_platform_scheduler import PatchRegistry
def test_patch_registry_is_immutable_and_rehydrates_fixed_policy(tmp_path):
 p=PatchProposal('patch-1','a'*40,'diff --git a/tools/x.cc b/tools/x.cc\n--- a/tools/x.cc\n+++ b/tools/x.cc\n@@ -1 +1 @@\n-a\n+b\n',('artifact:evidence',)); policy=VerificationPolicy(('tools/**',),((sys.executable,'-m','py_compile','x.py'),),30)
 r=PatchRegistry(tmp_path/'patch.db'); ref=r.register(p,policy,patch_surface='tools/**'); got,got_policy,surface=r.resolve(ref)
 assert got.sha256==p.sha256 and got_policy.commands==policy.commands and surface=='tools/**'
