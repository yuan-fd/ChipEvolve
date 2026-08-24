from openroad_platform_analysis import circuitops_lpg_ir, export_netlist_to_circuitops, request_table_rows


def test_circuitops_relational_lpg_index_keeps_full_table_provenance(tmp_path):
    headers = {
        "design_properties.csv": "design_name,platform\ntop,sky130hd\n",
        "libcell_properties.csv": "libcell_name\nINV\n",
        "pin_properties.csv": "pin_name,cell_name,net_name,dir\na,u1,n1,0\n",
        "cell_properties.csv": "cell_name,libcell_name\nu1,INV\n",
        "net_properties.csv": "net_name,fanout\nn1,1\n",
        "pin_pin_edge.csv": "src,tar,cell_name\na,y,u1\n",
        "cell_pin_edge.csv": "src,tar\nu1,a\n",
        "net_pin_edge.csv": "src,tar\nn1,a\n",
        "cell_net_edge.csv": "src,tar\nu1,n1\n",
        "cell_cell_edge.csv": "src,tar\nu1,u2\n",
    }
    for name, text in headers.items():
        (tmp_path / name).write_text(text)
    index = circuitops_lpg_ir(tmp_path)
    assert len(index["tables"]) == 10
    assert index["loss_policy"].startswith("no rows")
    excerpt = request_table_rows(index, tmp_path, table="pin_properties.csv",
                                 columns=["pin_name", "net_name"], equals={"cell_name": "u1"})
    assert excerpt["rows"] == [{"pin_name": "a", "net_name": "n1"}]
    assert excerpt["execution_allowed"] is False


def test_netlist_export_is_low_loss_and_reindexable(tmp_path):
    netlist = tmp_path / "top.v"
    netlist.write_text(
        "module top(a,b,y);\ninput a; input b; output y; wire n1;\n"
        "AND2X1 u1 (.A(a), .B(b), .Y(n1));\n"
        "INVX1 u2 (.A(n1), .Y(y));\nendmodule\n"
    )
    exported = export_netlist_to_circuitops(netlist, tmp_path / "circuitops",
                                            platform="nangate45")
    assert exported["source_netlist"]["sha256"]
    index = circuitops_lpg_ir(tmp_path / "circuitops")
    pins = request_table_rows(index, tmp_path / "circuitops", table="pin_properties.csv",
                              columns=["cell_name", "net_name"], limit=16)
    assert {tuple(row.values()) for row in pins["rows"]} >= {("u1", "a"), ("u2", "y")}
    assert (tmp_path / "circuitops/export_manifest.json").is_file()
