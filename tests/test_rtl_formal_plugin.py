from pathlib import Path
from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import PluginRegistry, build_rtl_formal_task, rtl_formal_plugin_manifest
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime

def test_bounded_yosys_formal_gate(tmp_path):
    yosys=Path('/share/home/yuanwenjie/bin/yosys')
    if not yosys.is_file(): return
    rtl=tmp_path/'dut.sv'; rtl.write_text('module dut(input a, output y); assign y=a; endmodule\n')
    prop=tmp_path/'p.sv'; prop.write_text('module prop; wire a; wire y; dut d(.a(a),.y(y)); always @* assert(y==a); endmodule\n')
    m=rtl_formal_plugin_manifest(yosys_bin=yosys); r=WorkflowRuntime(RuntimeStore(tmp_path/'r.db'),PluginRegistry([m]),workspace_root=tmp_path/'runs')
    run=r.submit(build_rtl_formal_task(project_id='p',design_id='d',rtl_path=rtl,property_path=prop,property_top='prop',spec_id='s',verification_id='v'),capability='eda.rtl.formal')
    assert r.execute_once(run.run_id).status is RuntimeStatus.SUCCEEDED
